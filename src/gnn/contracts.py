from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from torch_geometric.data import HeteroData

from src.core.models import EvidenceRef


EdgeType: TypeAlias = tuple[str, str, str]


@dataclass(frozen=True)
class GNNGraphBundle:
    """Heterogeneous discovery graph plus reversible forensic provenance.

    ``data`` is the numerical representation consumed by future GNN code.
    The remaining fields deliberately retain the semantic mapping back to the
    estate.  Node IDs are never encoded numerically as model features.
    """

    data: HeteroData
    node_ids: dict[str, tuple[str, ...]]
    node_index: dict[str, dict[str, int]]
    feature_names: dict[str, tuple[str, ...]]
    raw_features: dict[str, tuple[dict[str, float], ...]]
    edge_feature_names: dict[EdgeType, tuple[str, ...]]
    edge_provenance: dict[EdgeType, tuple[tuple[EvidenceRef, ...], ...]]

    def entity_id(self, node_type: str, index: int) -> str:
        return self.node_ids[node_type][index]

    def entity_index(self, node_type: str, entity_id: str) -> int:
        return self.node_index[node_type][entity_id]
