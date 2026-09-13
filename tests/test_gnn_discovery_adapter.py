from __future__ import annotations

import math

import pandas as pd
import torch
from torch_geometric.data import HeteroData

from src.core.models import EvidenceRef, Observation
from src.gnn.contracts import GNNGraphBundle
from src.gnn.discovery import (
    ENSEMBLE_SCORE_SEMANTICS,
    GNNEnsembleResult,
    StableGNNNodeScore,
    run_multi_seed_discovery,
)
from src.gnn.graph_builder import (
    ACCOUNT_EMPLOYEE_EDGE,
    ACCOUNT_VENDOR_EDGE,
    EMPLOYEE_OWNS_EDGE,
    TRANSFER_EDGE,
    VENDOR_OWNS_EDGE,
)
from src.gnn.observation_adapter import (
    GNNObservationPolicy,
    build_gnn_observations,
    select_observation_candidates,
)
from src.gnn.training import GNNTrainingConfig


def _bundle() -> GNNGraphBundle:
    data = HeteroData()
    data["vendor"].x = torch.tensor(
        [
            [0.0, 0.1, 0.1],
            [0.1, 0.2, 0.1],
            [0.0, 0.1, 0.2],
            [0.1, 0.0, 0.2],
            [2.8, -1.5, 2.0],
        ],
        dtype=torch.float32,
    )
    data["employee"].x = torch.tensor(
        [[0.0, 0.1], [0.1, 0.0], [0.0, 0.2], [0.2, 0.1], [1.8, -1.0]],
        dtype=torch.float32,
    )
    data["account"].x = torch.tensor(
        [
            [0.0, 0.1, 0.0],
            [0.1, 0.0, 0.1],
            [0.0, 0.2, 0.1],
            [0.2, 0.1, 0.0],
            [2.5, -1.4, 2.1],
        ],
        dtype=torch.float32,
    )
    for node_type in ("vendor", "employee", "account"):
        data[node_type].num_nodes = data[node_type].x.shape[0]

    data[VENDOR_OWNS_EDGE].edge_index = torch.tensor(
        [[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]], dtype=torch.long
    )
    data[ACCOUNT_VENDOR_EDGE].edge_index = torch.tensor(
        [[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]], dtype=torch.long
    )
    data[EMPLOYEE_OWNS_EDGE].edge_index = torch.tensor(
        [[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]], dtype=torch.long
    )
    data[ACCOUNT_EMPLOYEE_EDGE].edge_index = torch.tensor(
        [[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]], dtype=torch.long
    )
    data[TRANSFER_EDGE].edge_index = torch.tensor(
        [[0, 1, 2, 4], [1, 2, 3, 0]], dtype=torch.long
    )
    data[TRANSFER_EDGE].edge_attr = torch.tensor(
        [
            [0.0, 0.1, 0.1, 0.1, 0.0],
            [0.0, 0.2, 0.2, 0.2, 0.0],
            [0.1, 0.1, 0.2, 0.2, 0.0],
            [2.0, 2.5, 2.0, 2.8, 1.5],
        ],
        dtype=torch.float32,
    )
    data[TRANSFER_EDGE].edge_attr_raw = data[TRANSFER_EDGE].edge_attr.clone()

    node_ids = {
        "vendor": ("V1", "V2", "V3", "V4", "V5"),
        "employee": ("E1", "E2", "E3", "E4", "E5"),
        "account": ("A1", "A2", "A3", "A4", "A5"),
    }
    return GNNGraphBundle(
        data=data,
        node_ids=node_ids,
        node_index={kind: {value: i for i, value in enumerate(ids)} for kind, ids in node_ids.items()},
        feature_names={
            "vendor": ("invoice_count", "invoice_total", "bank_total_out"),
            "employee": ("bank_total_in", "vendor_owned_counterparties"),
            "account": ("total_in", "total_out", "vendor_owner_count"),
        },
        raw_features={
            "vendor": tuple(
                {"invoice_count": float(i + 1), "invoice_total": float((i + 1) * 100), "bank_total_out": float(i * 10)}
                for i in range(5)
            ),
            "employee": tuple(
                {"bank_total_in": float(i * 20), "vendor_owned_counterparties": float(i % 2)}
                for i in range(5)
            ),
            "account": tuple(
                {"total_in": float(i * 30), "total_out": float(i * 15), "vendor_owner_count": 1.0}
                for i in range(5)
            ),
        },
        edge_feature_names={TRANSFER_EDGE: ("e1", "e2", "e3", "e4", "e5")},
        edge_provenance={
            VENDOR_OWNS_EDGE: tuple(),
            ACCOUNT_VENDOR_EDGE: tuple(),
            EMPLOYEE_OWNS_EDGE: tuple(),
            ACCOUNT_EMPLOYEE_EDGE: tuple(),
            TRANSFER_EDGE: tuple(
                (EvidenceRef(source_table="bank_txns", record_id=f"T{i+1}"),)
                for i in range(4)
            ),
        },
    )


def _stable(node_type: str, entity_id: str, score: float, iqr: float = 0.05) -> StableGNNNodeScore:
    """Build a synthetic aggregate whose stored IQR matches the requested IQR.

    For three ordered values (low, median, high), NumPy's linear percentile
    interpolation gives IQR = (high - low) / 2.  Near 0/1 the fixture therefore
    becomes asymmetric rather than silently shrinking the requested IQR.
    """

    if not 0.0 <= score <= 1.0:
        raise ValueError("score must be in [0, 1]")
    if not 0.0 <= iqr <= 0.5:
        raise ValueError("iqr must be in [0, 0.5] for a three-score fixture")

    width = 2.0 * iqr
    lower_bound = max(0.0, score - width)
    upper_bound = min(score, 1.0 - width)
    low = min(max(score - iqr, lower_bound), upper_bound)
    high = low + width
    per_seed = (low, score, high)

    variance = sum((value - score) ** 2 for value in per_seed) / len(per_seed)

    return StableGNNNodeScore(
        node_type=node_type,
        entity_id=entity_id,
        per_seed_relative_scores=per_seed,
        per_seed_feature_percentiles=per_seed,
        per_seed_edge_percentiles=(None, None, None),
        median_relative_score=score,
        median_feature_percentile=score,
        median_edge_percentile=None,
        score_iqr=iqr,
        score_stddev=math.sqrt(variance),
        score_min=min(per_seed),
        score_max=max(per_seed),
    )


def test_multi_seed_discovery_is_repeatable_and_exposes_dispersion():
    config = GNNTrainingConfig(hidden_dim=8, dropout=0.0, epochs=4, patience=4)
    first = run_multi_seed_discovery(_bundle(), seeds=(3, 5), training_config=config)
    second = run_multi_seed_discovery(_bundle(), seeds=(3, 5), training_config=config)
    assert first == second
    assert first.seeds == (3, 5)
    assert len(first.seed_runs) == 2
    assert all(math.isfinite(row.median_relative_score) for row in first.node_scores)
    assert all(row.score_iqr >= 0.0 for row in first.node_scores)
    assert all(row.score_semantics == ENSEMBLE_SCORE_SEMANTICS for row in first.node_scores)


def test_multi_seed_discovery_rejects_duplicate_seeds():
    try:
        run_multi_seed_discovery(_bundle(), seeds=(7, 7), training_config=GNNTrainingConfig(epochs=1))
    except ValueError as error:
        assert "unique" in str(error)
    else:
        raise AssertionError("duplicate seeds should be rejected")


def test_candidate_policy_is_explicit_about_peer_count_score_and_dispersion():
    bundle = _bundle()
    ensemble = GNNEnsembleResult(
        seeds=(1, 2, 3),
        seed_runs=tuple(),
        node_scores=(
            _stable("vendor", "V5", 0.95, 0.05),
            _stable("vendor", "V4", 0.92, 0.30),
            _stable("vendor", "V3", 0.70, 0.05),
        ),
    )
    selected = select_observation_candidates(
        bundle,
        ensemble,
        GNNObservationPolicy(
            min_median_relative_score=0.85,
            max_score_iqr=0.20,
            min_peer_count=5,
            max_per_node_type=2,
        ),
    )
    assert [(row.node_type, row.entity_id) for row in selected] == [("vendor", "V5")]


class _FakeEstate:
    def __init__(self):
        self.frames = {
            "vendors": pd.DataFrame(
                [
                    {"rfc": "V5", "bank_clabe": "A5"},
                    {"rfc": "V1", "bank_clabe": "A1"},
                ]
            ),
            "employees": pd.DataFrame(
                [{"emp_id": "E5", "bank_clabe": "A5"}]
            ),
            "bank_txns": pd.DataFrame(
                [
                    {"txn_id": "T10", "from_clabe": "A5", "to_clabe": "A1", "amount": 5000.0},
                    {"txn_id": "T11", "from_clabe": "A1", "to_clabe": "A5", "amount": 100.0},
                ]
            ),
            "invoices": pd.DataFrame(
                [
                    {"uuid": "I1", "issuer_rfc": "V5", "total": 9000.0},
                    {"uuid": "I2", "issuer_rfc": "V5", "total": 1000.0},
                ]
            ),
            "ledger": pd.DataFrame(
                [{"entry_id": "L1", "invoice_uuid": "I1", "debit": 9000.0, "credit": 0.0}]
            ),
            "purchase_orders": pd.DataFrame(
                [{"po_id": "P1", "vendor_rfc": "V5", "amount": 3000.0}]
            ),
            "contracts": pd.DataFrame(
                [{"contract_id": "C1", "vendor_rfc": "V5", "value": 7000.0}]
            ),
            "efos_list": pd.DataFrame(
                [{"rfc": "V5", "status": "listed"}]
            ),
        }

    def table_df(self, table: str) -> pd.DataFrame:
        return self.frames[table].copy()


def test_observation_adapter_uses_shared_observation_and_concrete_refs_only():
    bundle = _bundle()
    ensemble = GNNEnsembleResult(
        seeds=(1, 2, 3),
        seed_runs=tuple(),
        node_scores=(_stable("vendor", "V5", 0.95, 0.05),),
    )
    policy = GNNObservationPolicy(
        min_median_relative_score=0.85,
        max_score_iqr=0.20,
        min_peer_count=5,
        max_per_node_type=1,
        max_evidence_refs=5,
    )
    observations = build_gnn_observations(_FakeEstate(), bundle, ensemble, policy=policy)
    assert len(observations) == 1
    observation = observations[0]
    assert isinstance(observation, Observation)
    assert observation.signal_type == "gnn_relational_anomaly"
    assert observation.entities == ["RFC:V5"]
    assert observation.score == 0.95
    assert "not a calibrated fraud probability" in observation.score_semantics
    assert len(observation.evidence) <= 5
    assert len({ref.source_table for ref in observation.evidence}) >= 4
    assert all(ref.record_id for ref in observation.evidence)
    assert observation.facts["evidence_selection_semantics"].endswith("not neural attribution.")
    assert observation.facts["training_seeds"] == [1, 2, 3]
    assert "scheme_type" not in observation.model_dump()


def test_observation_adapter_does_not_emit_when_peer_group_is_too_small():
    bundle = _bundle()
    bundle.node_ids["vendor"] = bundle.node_ids["vendor"][:4]
    ensemble = GNNEnsembleResult(
        seeds=(1, 2, 3),
        seed_runs=tuple(),
        node_scores=(_stable("vendor", "V1", 1.0, 0.0),),
    )
    observations = build_gnn_observations(
        _FakeEstate(),
        bundle,
        ensemble,
        policy=GNNObservationPolicy(min_peer_count=5),
    )
    assert observations == []
