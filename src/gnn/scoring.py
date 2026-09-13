"""Peer-relative anomaly scoring for the discovery GNN.

Scores are ranks within the current estate, not probabilities of fraud.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from torch.nn import functional as F

from src.gnn.contracts import GNNGraphBundle
from src.gnn.graph_builder import TRANSFER_EDGE
from src.gnn.model import HeteroDiscoveryGNN, NODE_TYPES


SCORE_SEMANTICS = (
    "Relative unsupervised discovery rank within peers of the same node type; "
    "not a calibrated fraud probability, not proof of intent, and not a finding."
)


@dataclass(frozen=True)
class GNNNodeScore:
    node_type: str
    entity_id: str
    feature_reconstruction_error: float
    edge_surprise: float | None
    feature_percentile: float
    edge_percentile: float | None
    final_relative_score: float
    score_semantics: str = SCORE_SEMANTICS


def _peer_percentiles(values: np.ndarray) -> np.ndarray:
    n = int(values.size)
    if n == 0:
        return np.asarray([], dtype=float)
    if n == 1:
        return np.asarray([0.5], dtype=float)
    result = np.zeros(n, dtype=float)
    for index, value in enumerate(values):
        lower = float(np.sum(values < value))
        equal = float(np.sum(values == value))
        average_rank = lower + 0.5 * (equal - 1.0)
        result[index] = average_rank / float(n - 1)
    return result


def _feature_errors(
    bundle: GNNGraphBundle,
    model: HeteroDiscoveryGNN,
) -> dict[str, torch.Tensor]:
    errors = {
        node_type: torch.zeros(bundle.data[node_type].x.shape[0], dtype=torch.float32)
        for node_type in NODE_TYPES
    }
    model.eval()
    with torch.no_grad():
        for node_type in NODE_TYPES:
            original = bundle.data[node_type].x
            if original.shape[0] == 0 or original.shape[1] == 0:
                continue
            accumulated = torch.zeros(original.shape[0], dtype=torch.float32)
            for feature_index in range(original.shape[1]):
                masked = {
                    current_type: bundle.data[current_type].x.clone()
                    for current_type in NODE_TYPES
                }
                masks = {
                    current_type: torch.zeros_like(
                        bundle.data[current_type].x, dtype=torch.bool
                    )
                    for current_type in NODE_TYPES
                }
                masks[node_type][:, feature_index] = True
                masked[node_type][:, feature_index] = 0.0
                _, recon = model(bundle.data, masked, mask_dict=masks)
                delta = recon[node_type][:, feature_index] - original[:, feature_index]
                accumulated += delta.pow(2).cpu()
            errors[node_type] = accumulated / float(original.shape[1])
    return errors


def _account_edge_surprise(
    bundle: GNNGraphBundle,
    model: HeteroDiscoveryGNN,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Score each observed transfer while hiding that edge from message passing."""

    num_accounts = int(bundle.data["account"].num_nodes)
    totals = torch.zeros(num_accounts, dtype=torch.float32)
    counts = torch.zeros(num_accounts, dtype=torch.float32)
    edge_index = bundle.data[TRANSFER_EDGE].edge_index
    edge_count = int(edge_index.shape[1])
    if edge_count == 0:
        return totals, counts

    model.eval()
    with torch.no_grad():
        # Five deterministic folds cap scoring cost while ensuring every edge is
        # scored without being present in the graph that produced its embedding.
        fold_count = min(5, edge_count)
        positions = torch.arange(edge_count)
        for fold in range(fold_count):
            held_positions = positions[positions % fold_count == fold]
            keep_mask = torch.ones(edge_count, dtype=torch.bool)
            keep_mask[held_positions] = False
            hidden = model.encode(bundle.data, transfer_keep_mask=keep_mask)
            held_edges = edge_index[:, held_positions]
            logits = model.transfer_logits(hidden, held_edges)
            surprises = F.binary_cross_entropy_with_logits(
                logits,
                torch.ones_like(logits),
                reduction="none",
            ).cpu()
            for local_position, edge_position in enumerate(held_positions.tolist()):
                source = int(edge_index[0, edge_position])
                target = int(edge_index[1, edge_position])
                value = surprises[local_position]
                totals[source] += value
                counts[source] += 1.0
                totals[target] += value
                counts[target] += 1.0

    means = torch.where(counts > 0, totals / counts.clamp_min(1.0), totals)
    return means, counts


def score_discovery_model(
    bundle: GNNGraphBundle,
    model: HeteroDiscoveryGNN,
) -> tuple[GNNNodeScore, ...]:
    feature_errors = _feature_errors(bundle, model)
    account_edge, account_edge_counts = _account_edge_surprise(bundle, model)
    rows: list[GNNNodeScore] = []

    for node_type in NODE_TYPES:
        feature_values = feature_errors[node_type].numpy().astype(float)
        feature_pct = _peer_percentiles(feature_values)

        edge_values: np.ndarray | None = None
        edge_pct: np.ndarray | None = None
        if node_type == "account":
            edge_values = account_edge.numpy().astype(float)
            # Accounts with no scored transfer must not become zero-valued peers.
            # Otherwise edge percentile mostly measures "has bank activity" rather
            # than surprise among accounts whose transfer edges were actually scored.
            active = account_edge_counts.numpy().astype(float) > 0
            edge_pct = np.full(edge_values.shape, np.nan, dtype=float)
            edge_pct[active] = _peer_percentiles(edge_values[active])

        for index, entity_id in enumerate(bundle.node_ids[node_type]):
            edge_value: float | None = None
            edge_rank: float | None = None
            components = [float(feature_pct[index])]
            if node_type == "account" and account_edge_counts[index].item() > 0:
                assert edge_values is not None and edge_pct is not None
                edge_value = float(edge_values[index])
                edge_rank = float(edge_pct[index])
                components.append(edge_rank)

            rows.append(
                GNNNodeScore(
                    node_type=node_type,
                    entity_id=entity_id,
                    feature_reconstruction_error=float(feature_values[index]),
                    edge_surprise=edge_value,
                    feature_percentile=float(feature_pct[index]),
                    edge_percentile=edge_rank,
                    final_relative_score=float(sum(components) / len(components)),
                )
            )

    rows.sort(key=lambda row: (-row.final_relative_score, row.node_type, row.entity_id))
    return tuple(rows)
