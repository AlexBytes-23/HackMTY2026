"""Deterministic self-supervised training for the discovery GNN."""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from src.gnn.contracts import GNNGraphBundle
from src.gnn.graph_builder import TRANSFER_EDGE
from src.gnn.model import HeteroDiscoveryGNN, NODE_TYPES


@dataclass(frozen=True)
class GNNTrainingConfig:
    hidden_dim: int = 32
    num_layers: int = 2
    dropout: float = 0.10
    epochs: int = 80
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    mask_rate: float = 0.25
    edge_holdout_rate: float = 0.25
    edge_loss_weight: float = 0.25
    patience: int = 15
    seed: int = 17


@dataclass(frozen=True)
class GNNTrainingResult:
    model: HeteroDiscoveryGNN
    history: tuple[dict[str, float], ...]
    best_epoch: int
    best_loss: float


def set_deterministic_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    try:
        torch.use_deterministic_algorithms(True)
    except (RuntimeError, AttributeError):
        pass


def make_masked_inputs(
    bundle: GNNGraphBundle,
    *,
    mask_rate: float,
    seed: int,
) -> tuple[dict[str, torch.Tensor], dict[str, torch.Tensor]]:
    if not 0.0 < mask_rate < 1.0:
        raise ValueError("mask_rate must be between 0 and 1")
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    masked: dict[str, torch.Tensor] = {}
    masks: dict[str, torch.Tensor] = {}

    for node_type in NODE_TYPES:
        original = bundle.data[node_type].x
        current = original.clone()
        if original.numel() == 0:
            mask = torch.zeros_like(original, dtype=torch.bool)
        else:
            mask = torch.rand(original.shape, generator=generator) < mask_rate
            if not bool(mask.any()):
                mask.view(-1)[0] = True
            current[mask] = 0.0
        masked[node_type] = current
        masks[node_type] = mask
    return masked, masks


def make_transfer_holdout(
    bundle: GNNGraphBundle,
    *,
    holdout_rate: float,
    seed: int,
) -> tuple[torch.Tensor | None, torch.Tensor]:
    """Hide positive transfer edges before asking the decoder to predict them.

    This prevents the edge objective from becoming tautological: a held-out
    transfer is never present in the message-passing graph used to predict it.
    """

    if not 0.0 < holdout_rate < 1.0:
        raise ValueError("holdout_rate must be between 0 and 1")
    positive = bundle.data[TRANSFER_EDGE].edge_index
    count = int(positive.shape[1])
    if count == 0:
        return None, torch.empty((2, 0), dtype=torch.long)
    if count == 1:
        keep = torch.zeros(1, dtype=torch.bool)
        return keep, positive.clone()

    holdout_count = max(1, int(round(count * holdout_rate)))
    holdout_count = min(holdout_count, count - 1)
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    order = torch.randperm(count, generator=generator)
    held_positions = order[:holdout_count]
    keep = torch.ones(count, dtype=torch.bool)
    keep[held_positions] = False
    return keep, positive[:, held_positions]


def _masked_reconstruction_loss(
    recon: dict[str, torch.Tensor],
    bundle: GNNGraphBundle,
    masks: dict[str, torch.Tensor],
) -> torch.Tensor:
    losses: list[torch.Tensor] = []
    for node_type in NODE_TYPES:
        mask = masks[node_type]
        if bool(mask.any()):
            losses.append(F.mse_loss(recon[node_type][mask], bundle.data[node_type].x[mask]))
    if not losses:
        sample = bundle.data["account"].x
        return torch.zeros((), dtype=sample.dtype, device=sample.device)
    return torch.stack(losses).mean()


def _negative_transfer_edges(
    bundle: GNNGraphBundle,
    *,
    count: int,
    seed: int,
) -> torch.Tensor:
    num_accounts = int(bundle.data["account"].num_nodes)
    if count <= 0 or num_accounts < 2:
        return torch.empty((2, 0), dtype=torch.long)

    positive = bundle.data[TRANSFER_EDGE].edge_index
    existing = {
        (int(positive[0, i]), int(positive[1, i]))
        for i in range(positive.shape[1])
    }
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    chosen: set[tuple[int, int]] = set()
    max_attempts = max(100, count * 100)
    attempts = 0
    while len(chosen) < count and attempts < max_attempts:
        pair = torch.randint(0, num_accounts, (2,), generator=generator)
        source, target = int(pair[0]), int(pair[1])
        attempts += 1
        if source == target or (source, target) in existing or (source, target) in chosen:
            continue
        chosen.add((source, target))

    if not chosen:
        return torch.empty((2, 0), dtype=torch.long)
    ordered = sorted(chosen)
    return torch.tensor(ordered, dtype=torch.long).t().contiguous()


def _edge_prediction_loss(
    model: HeteroDiscoveryGNN,
    hidden: dict[str, torch.Tensor],
    heldout_positive: torch.Tensor,
    negative_edges: torch.Tensor,
) -> torch.Tensor:
    if heldout_positive.shape[1] == 0 or negative_edges.shape[1] == 0:
        sample = hidden["account"]
        return torch.zeros((), dtype=sample.dtype, device=sample.device)

    positive_logits = model.transfer_logits(hidden, heldout_positive)
    negative_logits = model.transfer_logits(hidden, negative_edges)
    logits = torch.cat([positive_logits, negative_logits])
    labels = torch.cat(
        [torch.ones_like(positive_logits), torch.zeros_like(negative_logits)]
    )
    return F.binary_cross_entropy_with_logits(logits, labels)


def _objective_on_view(
    model: HeteroDiscoveryGNN,
    bundle: GNNGraphBundle,
    *,
    masked_inputs: dict[str, torch.Tensor],
    masks: dict[str, torch.Tensor],
    keep_mask: torch.Tensor | None,
    heldout_positive: torch.Tensor,
    negative_edges: torch.Tensor,
    edge_loss_weight: float,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    hidden, recon = model(
        bundle.data,
        masked_inputs,
        mask_dict=masks,
        transfer_keep_mask=keep_mask,
    )
    feature_loss = _masked_reconstruction_loss(recon, bundle, masks)
    edge_loss = _edge_prediction_loss(
        model,
        hidden,
        heldout_positive,
        negative_edges,
    )
    loss = feature_loss + edge_loss_weight * edge_loss
    return loss, feature_loss, edge_loss


def train_discovery_model(
    bundle: GNNGraphBundle,
    config: GNNTrainingConfig | None = None,
) -> GNNTrainingResult:
    config = config or GNNTrainingConfig()
    if config.epochs < 1:
        raise ValueError("epochs must be >= 1")
    if config.patience < 1:
        raise ValueError("patience must be >= 1")

    set_deterministic_seed(config.seed)
    model = HeteroDiscoveryGNN.from_data(
        bundle.data,
        hidden_dim=config.hidden_dim,
        dropout=config.dropout,
        num_layers=config.num_layers,
    )
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )

    # Training masks/edge holdouts change each epoch. Comparing those stochastic
    # training losses directly for early stopping would compare different tasks.
    # Keep one deterministic monitoring view instead. This is only a stable
    # optimization monitor on the same estate, NOT an independent estimate of
    # generalization or real-world fraud performance.
    monitor_inputs, monitor_masks = make_masked_inputs(
        bundle, mask_rate=config.mask_rate, seed=config.seed + 1_000_000
    )
    monitor_keep, monitor_positive = make_transfer_holdout(
        bundle, holdout_rate=config.edge_holdout_rate, seed=config.seed + 2_000_000
    )
    monitor_negative = _negative_transfer_edges(
        bundle,
        count=int(monitor_positive.shape[1]),
        seed=config.seed + 3_000_000,
    )

    best_loss = math.inf
    best_epoch = -1
    best_state: dict[str, torch.Tensor] | None = None
    stale_epochs = 0
    history: list[dict[str, float]] = []

    for epoch in range(config.epochs):
        model.train()
        masked_inputs, masks = make_masked_inputs(
            bundle,
            mask_rate=config.mask_rate,
            seed=config.seed + epoch,
        )
        keep_mask, heldout_positive = make_transfer_holdout(
            bundle,
            holdout_rate=config.edge_holdout_rate,
            seed=config.seed + 10_000 + epoch,
        )
        negative_edges = _negative_transfer_edges(
            bundle,
            count=int(heldout_positive.shape[1]),
            seed=config.seed + 100_000 + epoch,
        )

        optimizer.zero_grad(set_to_none=True)
        loss, feature_loss, edge_loss = _objective_on_view(
            model,
            bundle,
            masked_inputs=masked_inputs,
            masks=masks,
            keep_mask=keep_mask,
            heldout_positive=heldout_positive,
            negative_edges=negative_edges,
            edge_loss_weight=config.edge_loss_weight,
        )
        if not torch.isfinite(loss):
            raise RuntimeError("GNN training produced a non-finite loss")
        if not loss.requires_grad:
            raise ValueError("estate does not contain enough signal to train the discovery GNN")
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
        optimizer.step()

        model.eval()
        with torch.no_grad():
            monitor_loss, monitor_feature_loss, monitor_edge_loss = _objective_on_view(
                model,
                bundle,
                masked_inputs=monitor_inputs,
                masks=monitor_masks,
                keep_mask=monitor_keep,
                heldout_positive=monitor_positive,
                negative_edges=monitor_negative,
                edge_loss_weight=config.edge_loss_weight,
            )
        if not torch.isfinite(monitor_loss):
            raise RuntimeError("GNN monitoring produced a non-finite loss")

        row = {
            "epoch": float(epoch),
            "loss": float(loss.detach().cpu()),
            "feature_loss": float(feature_loss.detach().cpu()),
            "edge_loss": float(edge_loss.detach().cpu()),
            "monitor_loss": float(monitor_loss.detach().cpu()),
            "monitor_feature_loss": float(monitor_feature_loss.detach().cpu()),
            "monitor_edge_loss": float(monitor_edge_loss.detach().cpu()),
        }
        history.append(row)

        current = row["monitor_loss"]
        if current + 1e-8 < best_loss:
            best_loss = current
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= config.patience:
                break

    if best_state is None:
        raise RuntimeError("GNN training did not produce a valid model state")
    model.load_state_dict(best_state)
    model.eval()
    return GNNTrainingResult(
        model=model,
        history=tuple(history),
        best_epoch=best_epoch,
        best_loss=float(best_loss),
    )
