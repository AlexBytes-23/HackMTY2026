"""Multi-seed stability layer for the unsupervised discovery GNN.

This module aggregates repeated deterministic training runs.  Stability across
random initializations is useful diagnostic evidence about the *model signal*;
it is not evidence that an entity committed fraud and it is not a calibrated
probability.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from src.gnn.contracts import GNNGraphBundle
from src.gnn.scoring import GNNNodeScore, score_discovery_model
from src.gnn.training import GNNTrainingConfig, train_discovery_model


ENSEMBLE_SCORE_SEMANTICS = (
    "Median peer-relative unsupervised discovery rank across deterministic "
    "training seeds; dispersion describes optimization stability only. The "
    "score is estate-relative, not a calibrated fraud probability, not proof "
    "of intent, and not a finding."
)


@dataclass(frozen=True)
class GNNSeedRun:
    seed: int
    best_epoch: int
    best_loss: float
    epochs_ran: int


@dataclass(frozen=True)
class StableGNNNodeScore:
    node_type: str
    entity_id: str
    per_seed_relative_scores: tuple[float, ...]
    per_seed_feature_percentiles: tuple[float, ...]
    per_seed_edge_percentiles: tuple[float | None, ...]
    median_relative_score: float
    median_feature_percentile: float
    median_edge_percentile: float | None
    score_iqr: float
    score_stddev: float
    score_min: float
    score_max: float
    score_semantics: str = ENSEMBLE_SCORE_SEMANTICS


@dataclass(frozen=True)
class GNNEnsembleResult:
    seeds: tuple[int, ...]
    seed_runs: tuple[GNNSeedRun, ...]
    node_scores: tuple[StableGNNNodeScore, ...]


def _median(values: tuple[float, ...]) -> float:
    return float(np.median(np.asarray(values, dtype=float)))


def _iqr(values: tuple[float, ...]) -> float:
    array = np.asarray(values, dtype=float)
    if array.size <= 1:
        return 0.0
    q25, q75 = np.percentile(array, [25.0, 75.0])
    return float(q75 - q25)


def _aggregate_rows(rows: tuple[GNNNodeScore, ...]) -> dict[tuple[str, str], GNNNodeScore]:
    result: dict[tuple[str, str], GNNNodeScore] = {}
    for row in rows:
        key = (row.node_type, row.entity_id)
        if key in result:
            raise ValueError(f"Duplicate GNN score key: {key}")
        result[key] = row
    return result


def run_multi_seed_discovery(
    bundle: GNNGraphBundle,
    *,
    seeds: tuple[int, ...] = (11, 17, 23),
    training_config: GNNTrainingConfig | None = None,
) -> GNNEnsembleResult:
    """Train and score the discovery model across deterministic seeds.

    The graph and low-level facts are identical for every run.  Only neural
    initialization/training randomness changes.  Aggregating seeds therefore
    measures optimization stability, not out-of-sample generalization.
    """

    if not seeds:
        raise ValueError("At least one GNN training seed is required")
    if len(set(seeds)) != len(seeds):
        raise ValueError("GNN training seeds must be unique")

    base_config = training_config or GNNTrainingConfig()
    per_seed: list[dict[tuple[str, str], GNNNodeScore]] = []
    seed_runs: list[GNNSeedRun] = []
    expected_keys: set[tuple[str, str]] | None = None

    for seed in seeds:
        result = train_discovery_model(bundle, replace(base_config, seed=int(seed)))
        rows = score_discovery_model(bundle, result.model)
        keyed = _aggregate_rows(rows)
        keys = set(keyed)
        if expected_keys is None:
            expected_keys = keys
        elif keys != expected_keys:
            raise RuntimeError("GNN node universe changed across training seeds")
        per_seed.append(keyed)
        seed_runs.append(
            GNNSeedRun(
                seed=int(seed),
                best_epoch=result.best_epoch,
                best_loss=float(result.best_loss),
                epochs_ran=len(result.history),
            )
        )

    assert expected_keys is not None
    aggregated: list[StableGNNNodeScore] = []
    for key in sorted(expected_keys):
        seed_rows = [mapping[key] for mapping in per_seed]
        relative = tuple(float(row.final_relative_score) for row in seed_rows)
        feature = tuple(float(row.feature_percentile) for row in seed_rows)
        edge = tuple(
            None if row.edge_percentile is None else float(row.edge_percentile)
            for row in seed_rows
        )
        edge_present = tuple(value for value in edge if value is not None)
        aggregated.append(
            StableGNNNodeScore(
                node_type=key[0],
                entity_id=key[1],
                per_seed_relative_scores=relative,
                per_seed_feature_percentiles=feature,
                per_seed_edge_percentiles=edge,
                median_relative_score=_median(relative),
                median_feature_percentile=_median(feature),
                median_edge_percentile=(
                    _median(edge_present) if edge_present else None
                ),
                score_iqr=_iqr(relative),
                score_stddev=float(np.std(np.asarray(relative, dtype=float))),
                score_min=float(min(relative)),
                score_max=float(max(relative)),
            )
        )

    aggregated.sort(
        key=lambda row: (
            -row.median_relative_score,
            row.score_iqr,
            row.node_type,
            row.entity_id,
        )
    )
    return GNNEnsembleResult(
        seeds=tuple(int(seed) for seed in seeds),
        seed_runs=tuple(seed_runs),
        node_scores=tuple(aggregated),
    )
