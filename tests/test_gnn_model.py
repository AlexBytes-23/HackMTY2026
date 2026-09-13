from __future__ import annotations

import math

import pytest

# El subsistema GNN es opcional (requirements-gnn.txt). Sin torch instalado estos
# modulos deben SALTARSE, no romper la coleccion completa de pytest.
pytest.importorskip("torch")
pytest.importorskip("torch_geometric")

import torch
from torch_geometric.data import HeteroData

from src.core.models import EvidenceRef
from src.gnn.contracts import GNNGraphBundle
from src.gnn.graph_builder import (
    ACCOUNT_EMPLOYEE_EDGE,
    ACCOUNT_VENDOR_EDGE,
    EMPLOYEE_OWNS_EDGE,
    TRANSFER_EDGE,
    VENDOR_OWNS_EDGE,
)
from src.gnn.model import HeteroDiscoveryGNN
from src.gnn.scoring import SCORE_SEMANTICS, score_discovery_model
from src.gnn.training import (
    GNNTrainingConfig,
    make_masked_inputs,
    make_transfer_holdout,
    train_discovery_model,
)


def _bundle() -> GNNGraphBundle:
    data = HeteroData()
    data["vendor"].x = torch.tensor(
        [[0.0, 0.2, 0.1], [0.1, 0.3, 0.2], [2.5, -1.0, 1.8]], dtype=torch.float32
    )
    data["employee"].x = torch.tensor(
        [[0.0, 0.2], [0.1, 0.1]], dtype=torch.float32
    )
    data["account"].x = torch.tensor(
        [
            [0.0, 0.2, 0.1, 0.0],
            [0.1, 0.3, 0.0, 0.1],
            [0.2, 0.1, 0.2, 0.0],
            [2.2, -1.5, 1.7, 2.0],
        ],
        dtype=torch.float32,
    )
    for node_type in ("vendor", "employee", "account"):
        data[node_type].num_nodes = data[node_type].x.shape[0]

    data[VENDOR_OWNS_EDGE].edge_index = torch.tensor([[0, 1, 2], [0, 1, 3]], dtype=torch.long)
    data[ACCOUNT_VENDOR_EDGE].edge_index = torch.tensor([[0, 1, 3], [0, 1, 2]], dtype=torch.long)
    data[EMPLOYEE_OWNS_EDGE].edge_index = torch.tensor([[0, 1], [2, 3]], dtype=torch.long)
    data[ACCOUNT_EMPLOYEE_EDGE].edge_index = torch.tensor([[2, 3], [0, 1]], dtype=torch.long)
    data[TRANSFER_EDGE].edge_index = torch.tensor([[0, 1, 3], [1, 2, 2]], dtype=torch.long)
    data[TRANSFER_EDGE].edge_attr = torch.tensor(
        [
            [0.0, 0.1, 0.1, 0.1, 0.0],
            [0.0, 0.2, 0.2, 0.2, 0.0],
            [2.0, 2.5, 2.0, 2.8, 1.5],
        ],
        dtype=torch.float32,
    )
    data[TRANSFER_EDGE].edge_attr_raw = data[TRANSFER_EDGE].edge_attr.clone()

    node_ids = {
        "vendor": ("V1", "V2", "V3"),
        "employee": ("E1", "E2"),
        "account": ("A1", "A2", "A3", "A4"),
    }
    return GNNGraphBundle(
        data=data,
        node_ids=node_ids,
        node_index={kind: {value: i for i, value in enumerate(ids)} for kind, ids in node_ids.items()},
        feature_names={
            "vendor": ("f1", "f2", "f3"),
            "employee": ("f1", "f2"),
            "account": ("f1", "f2", "f3", "f4"),
        },
        raw_features={
            "vendor": tuple({} for _ in node_ids["vendor"]),
            "employee": tuple({} for _ in node_ids["employee"]),
            "account": tuple({} for _ in node_ids["account"]),
        },
        edge_feature_names={TRANSFER_EDGE: ("e1", "e2", "e3", "e4", "e5")},
        edge_provenance={
            VENDOR_OWNS_EDGE: tuple(),
            ACCOUNT_VENDOR_EDGE: tuple(),
            EMPLOYEE_OWNS_EDGE: tuple(),
            ACCOUNT_EMPLOYEE_EDGE: tuple(),
            TRANSFER_EDGE: (
                (EvidenceRef(source_table="bank_txns", record_id="T1"),),
                (EvidenceRef(source_table="bank_txns", record_id="T2"),),
                (EvidenceRef(source_table="bank_txns", record_id="T3"),),
            ),
        },
    )


def test_model_forward_shapes_match_each_node_type():
    bundle = _bundle()
    model = HeteroDiscoveryGNN.from_data(bundle.data, hidden_dim=8, dropout=0.0)
    hidden, recon = model(bundle.data)
    for node_type in ("vendor", "employee", "account"):
        assert hidden[node_type].shape == (bundle.data[node_type].num_nodes, 8)
        assert recon[node_type].shape == bundle.data[node_type].x.shape


def test_transfer_edge_attributes_change_account_embeddings():
    bundle = _bundle()
    torch.manual_seed(4)
    model = HeteroDiscoveryGNN.from_data(bundle.data, hidden_dim=8, dropout=0.0)
    model.eval()
    with torch.no_grad():
        first = model.encode(bundle.data)["account"].clone()
        original = bundle.data[TRANSFER_EDGE].edge_attr.clone()
        bundle.data[TRANSFER_EDGE].edge_attr = original * 7.0
        second = model.encode(bundle.data)["account"].clone()
        bundle.data[TRANSFER_EDGE].edge_attr = original
    assert not torch.allclose(first, second)


def test_masking_hides_values_without_mutating_graph_and_marks_missingness():
    bundle = _bundle()
    original = bundle.data["vendor"].x.clone()
    masked, masks = make_masked_inputs(bundle, mask_rate=0.35, seed=5)
    assert masks["vendor"].any()
    assert torch.all(masked["vendor"][masks["vendor"]] == 0.0)
    assert torch.equal(bundle.data["vendor"].x, original)


def test_transfer_holdout_removes_target_from_message_passing_view():
    bundle = _bundle()
    keep, heldout = make_transfer_holdout(bundle, holdout_rate=0.34, seed=3)
    assert keep is not None
    assert heldout.shape[1] >= 1
    kept_pairs = {
        (int(bundle.data[TRANSFER_EDGE].edge_index[0, i]), int(bundle.data[TRANSFER_EDGE].edge_index[1, i]))
        for i in range(bundle.data[TRANSFER_EDGE].edge_index.shape[1])
        if bool(keep[i])
    }
    held_pairs = {
        (int(heldout[0, i]), int(heldout[1, i]))
        for i in range(heldout.shape[1])
    }
    assert kept_pairs.isdisjoint(held_pairs)


def test_training_is_deterministic_for_same_seed_and_finite():
    config = GNNTrainingConfig(
        hidden_dim=8,
        num_layers=2,
        dropout=0.0,
        epochs=8,
        patience=8,
        learning_rate=3e-3,
        seed=123,
    )
    first = train_discovery_model(_bundle(), config)
    second = train_discovery_model(_bundle(), config)
    assert math.isfinite(first.best_loss)
    assert first.best_epoch >= 0
    assert len(first.history) > 0
    assert first.best_loss == second.best_loss
    assert first.history == second.history
    assert "monitor_loss" in first.history[0]
    assert first.best_loss == min(row["monitor_loss"] for row in first.history)


def test_scoring_is_peer_relative_finite_and_not_probability_claim():
    bundle = _bundle()
    trained = train_discovery_model(
        bundle,
        GNNTrainingConfig(hidden_dim=8, dropout=0.0, epochs=6, patience=6, seed=11),
    )
    scores = score_discovery_model(bundle, trained.model)
    assert len(scores) == sum(len(ids) for ids in bundle.node_ids.values())
    for row in scores:
        assert math.isfinite(row.feature_reconstruction_error)
        assert 0.0 <= row.feature_percentile <= 1.0
        assert 0.0 <= row.final_relative_score <= 1.0
        assert row.score_semantics == SCORE_SEMANTICS
        assert "not a calibrated fraud probability" in row.score_semantics


def test_graph_with_no_transfer_edges_still_trains_and_scores():
    bundle = _bundle()
    bundle.data[TRANSFER_EDGE].edge_index = torch.empty((2, 0), dtype=torch.long)
    bundle.data[TRANSFER_EDGE].edge_attr = torch.empty((0, 5), dtype=torch.float32)
    bundle.data[TRANSFER_EDGE].edge_attr_raw = torch.empty((0, 5), dtype=torch.float32)
    trained = train_discovery_model(
        bundle,
        GNNTrainingConfig(hidden_dim=8, dropout=0.0, epochs=4, patience=4, seed=7),
    )
    scores = score_discovery_model(bundle, trained.model)
    assert len(scores) > 0
    assert all(row.edge_surprise is None for row in scores if row.node_type != "account")
    assert all(row.edge_surprise is None for row in scores if row.node_type == "account")


def test_edge_percentile_compares_only_accounts_with_scored_transfer_edges():
    bundle = _bundle()
    # Keep one transfer A1 -> A2. A3/A4 have no scored transfer and therefore
    # must not act as zero-surprise peers for the edge ranking.
    bundle.data[TRANSFER_EDGE].edge_index = bundle.data[TRANSFER_EDGE].edge_index[:, :1].clone()
    bundle.data[TRANSFER_EDGE].edge_attr = bundle.data[TRANSFER_EDGE].edge_attr[:1].clone()
    bundle.data[TRANSFER_EDGE].edge_attr_raw = bundle.data[TRANSFER_EDGE].edge_attr_raw[:1].clone()
    bundle.edge_provenance[TRANSFER_EDGE] = bundle.edge_provenance[TRANSFER_EDGE][:1]

    trained = train_discovery_model(
        bundle,
        GNNTrainingConfig(hidden_dim=8, dropout=0.0, epochs=4, patience=4, seed=19),
    )
    scores = score_discovery_model(bundle, trained.model)
    account_scores = {row.entity_id: row for row in scores if row.node_type == "account"}

    assert account_scores["A1"].edge_percentile == 0.5
    assert account_scores["A2"].edge_percentile == 0.5
    assert account_scores["A3"].edge_percentile is None
    assert account_scores["A4"].edge_percentile is None
