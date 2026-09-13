"""Small heterogeneous GNN used only for discovery.

The model deliberately stays simple and auditable. It consumes the factual
``HeteroData`` produced by :mod:`src.gnn.graph_builder`; it does not emit fraud
labels, scheme labels, findings, or probabilities.
"""

from __future__ import annotations

from typing import Mapping

import torch
from torch import nn
from torch.nn import functional as F
from torch_geometric.data import HeteroData

from src.gnn.contracts import EdgeType
from src.gnn.graph_builder import (
    ACCOUNT_EMPLOYEE_EDGE,
    ACCOUNT_VENDOR_EDGE,
    EMPLOYEE_OWNS_EDGE,
    TRANSFER_EDGE,
    VENDOR_OWNS_EDGE,
)


NODE_TYPES = ("vendor", "employee", "account")
RELATIONS: tuple[EdgeType, ...] = (
    VENDOR_OWNS_EDGE,
    ACCOUNT_VENDOR_EDGE,
    EMPLOYEE_OWNS_EDGE,
    ACCOUNT_EMPLOYEE_EDGE,
    TRANSFER_EDGE,
)


def _relation_key(edge_type: EdgeType) -> str:
    return "__".join(edge_type)


class HeteroMessageLayer(nn.Module):
    """One relation-aware message passing layer.

    Ownership relations use source-node messages. Bank-transfer messages also
    receive the aggregated low-level edge attributes so amount/count/timing
    information is not discarded by the neural representation.
    """

    def __init__(self, hidden_dim: int, transfer_edge_dim: int, dropout: float) -> None:
        super().__init__()
        self.dropout = float(dropout)
        self.self_linears = nn.ModuleDict(
            {node_type: nn.Linear(hidden_dim, hidden_dim) for node_type in NODE_TYPES}
        )
        self.relation_linears = nn.ModuleDict(
            {
                _relation_key(edge_type): nn.Linear(hidden_dim, hidden_dim, bias=False)
                for edge_type in RELATIONS
            }
        )
        self.transfer_edge_linear = nn.Linear(transfer_edge_dim, hidden_dim, bias=False)
        self.norms = nn.ModuleDict(
            {node_type: nn.LayerNorm(hidden_dim) for node_type in NODE_TYPES}
        )

    def forward(
        self,
        hidden: Mapping[str, torch.Tensor],
        data: HeteroData,
        *,
        transfer_keep_mask: torch.Tensor | None = None,
    ) -> dict[str, torch.Tensor]:
        outputs = {
            node_type: self.self_linears[node_type](hidden[node_type])
            for node_type in NODE_TYPES
        }

        for edge_type in RELATIONS:
            source_type, _, target_type = edge_type
            edge_index = data[edge_type].edge_index
            edge_attr = None
            if edge_type == TRANSFER_EDGE:
                edge_attr = data[edge_type].edge_attr
                if transfer_keep_mask is not None:
                    edge_index = edge_index[:, transfer_keep_mask]
                    edge_attr = edge_attr[transfer_keep_mask]
            if edge_index.numel() == 0:
                continue

            source_index = edge_index[0]
            target_index = edge_index[1]
            messages = self.relation_linears[_relation_key(edge_type)](
                hidden[source_type][source_index]
            )
            if edge_type == TRANSFER_EDGE:
                assert edge_attr is not None
                if edge_attr.shape[0] != messages.shape[0]:
                    raise ValueError("transfer edge_attr rows must match transfer edges")
                messages = messages + self.transfer_edge_linear(edge_attr)

            aggregated = torch.zeros_like(hidden[target_type])
            aggregated.index_add_(0, target_index, messages)
            counts = torch.zeros(
                hidden[target_type].shape[0],
                dtype=messages.dtype,
                device=messages.device,
            )
            counts.index_add_(
                0,
                target_index,
                torch.ones(target_index.shape[0], dtype=messages.dtype, device=messages.device),
            )
            aggregated = aggregated / counts.clamp_min(1.0).unsqueeze(-1)
            outputs[target_type] = outputs[target_type] + aggregated

        return {
            node_type: F.dropout(
                F.gelu(self.norms[node_type](outputs[node_type])),
                p=self.dropout,
                training=self.training,
            )
            for node_type in NODE_TYPES
        }


class HeteroDiscoveryGNN(nn.Module):
    """Compact heterogeneous encoder with self-supervised decoders."""

    def __init__(
        self,
        input_dims: Mapping[str, int],
        *,
        transfer_edge_dim: int,
        hidden_dim: int = 32,
        dropout: float = 0.10,
        num_layers: int = 2,
    ) -> None:
        super().__init__()
        if num_layers < 1:
            raise ValueError("num_layers must be >= 1")
        self.input_dims = {key: int(value) for key, value in input_dims.items()}
        self.hidden_dim = int(hidden_dim)
        self.transfer_edge_dim = int(transfer_edge_dim)

        self.encoders = nn.ModuleDict(
            {
                node_type: nn.Linear(self.input_dims[node_type], hidden_dim)
                for node_type in NODE_TYPES
            }
        )
        # Zero is a common normalized feature value. A learned mask embedding
        # explicitly tells the encoder which inputs were hidden so masking does
        # not silently become indistinguishable from a genuine zero.
        self.mask_embeddings = nn.ParameterDict(
            {
                node_type: nn.Parameter(torch.empty(self.input_dims[node_type], hidden_dim))
                for node_type in NODE_TYPES
            }
        )
        for parameter in self.mask_embeddings.values():
            nn.init.normal_(parameter, mean=0.0, std=0.02)

        self.layers = nn.ModuleList(
            [
                HeteroMessageLayer(hidden_dim, transfer_edge_dim, dropout)
                for _ in range(num_layers)
            ]
        )
        self.decoders = nn.ModuleDict(
            {
                node_type: nn.Linear(hidden_dim, self.input_dims[node_type])
                for node_type in NODE_TYPES
            }
        )
        self.transfer_bilinear = nn.Bilinear(hidden_dim, hidden_dim, 1, bias=True)

    @classmethod
    def from_data(
        cls,
        data: HeteroData,
        *,
        hidden_dim: int = 32,
        dropout: float = 0.10,
        num_layers: int = 2,
    ) -> "HeteroDiscoveryGNN":
        input_dims = {node_type: int(data[node_type].x.shape[1]) for node_type in NODE_TYPES}
        transfer_edge_dim = int(data[TRANSFER_EDGE].edge_attr.shape[1])
        return cls(
            input_dims,
            transfer_edge_dim=transfer_edge_dim,
            hidden_dim=hidden_dim,
            dropout=dropout,
            num_layers=num_layers,
        )

    def encode(
        self,
        data: HeteroData,
        x_dict: Mapping[str, torch.Tensor] | None = None,
        *,
        mask_dict: Mapping[str, torch.Tensor] | None = None,
        transfer_keep_mask: torch.Tensor | None = None,
    ) -> dict[str, torch.Tensor]:
        source = x_dict if x_dict is not None else {
            node_type: data[node_type].x for node_type in NODE_TYPES
        }
        hidden: dict[str, torch.Tensor] = {}
        for node_type in NODE_TYPES:
            encoded = self.encoders[node_type](source[node_type])
            if mask_dict is not None and node_type in mask_dict:
                mask = mask_dict[node_type].to(dtype=encoded.dtype)
                encoded = encoded + mask @ self.mask_embeddings[node_type]
            hidden[node_type] = F.gelu(encoded)

        for layer in self.layers:
            hidden = layer(hidden, data, transfer_keep_mask=transfer_keep_mask)
        return hidden

    def reconstruct(self, hidden: Mapping[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        return {
            node_type: self.decoders[node_type](hidden[node_type])
            for node_type in NODE_TYPES
        }

    def forward(
        self,
        data: HeteroData,
        x_dict: Mapping[str, torch.Tensor] | None = None,
        *,
        mask_dict: Mapping[str, torch.Tensor] | None = None,
        transfer_keep_mask: torch.Tensor | None = None,
    ) -> tuple[dict[str, torch.Tensor], dict[str, torch.Tensor]]:
        hidden = self.encode(
            data,
            x_dict,
            mask_dict=mask_dict,
            transfer_keep_mask=transfer_keep_mask,
        )
        return hidden, self.reconstruct(hidden)

    def transfer_logits(
        self,
        hidden: Mapping[str, torch.Tensor],
        edge_index: torch.Tensor,
    ) -> torch.Tensor:
        if edge_index.numel() == 0:
            return torch.empty(
                0,
                dtype=hidden["account"].dtype,
                device=hidden["account"].device,
            )
        sources = hidden["account"][edge_index[0]]
        targets = hidden["account"][edge_index[1]]
        return self.transfer_bilinear(sources, targets).squeeze(-1)
