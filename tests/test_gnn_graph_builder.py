from __future__ import annotations

import math

import pandas as pd
import torch

from src.gnn.graph_builder import (
    ACCOUNT_EMPLOYEE_EDGE,
    ACCOUNT_VENDOR_EDGE,
    EMPLOYEE_OWNS_EDGE,
    TRANSFER_EDGE,
    VENDOR_OWNS_EDGE,
    build_gnn_graph,
)


class FakeEstate:
    def __init__(self) -> None:
        self.tables = {
            "vendors": pd.DataFrame(
                [
                    {
                        "rfc": "VEND1",
                        "legal_name": "Vendor One",
                        "registered_date": "2024-01-01",
                        "address": "A",
                        "bank_clabe": "ACC-V1",
                        "category": "services",
                        "contact_email": "v1@example.com",
                    },
                    {
                        "rfc": "VEND2",
                        "legal_name": "Vendor Two",
                        "registered_date": "2024-01-02",
                        "address": "B",
                        "bank_clabe": "ACC-SHARED",
                        "category": "services",
                        "contact_email": "v2@example.com",
                    },
                ]
            ),
            "employees": pd.DataFrame(
                [
                    {
                        "emp_id": "EMP1",
                        "name": "Employee One",
                        "role": "buyer",
                        "bank_clabe": "ACC-E1",
                        "hire_date": "2023-01-01",
                    },
                    {
                        "emp_id": "EMP2",
                        "name": "Employee Two",
                        "role": "analyst",
                        "bank_clabe": "ACC-SHARED",
                        "hire_date": "2023-02-01",
                    },
                ]
            ),
            "bank_txns": pd.DataFrame(
                [
                    {
                        "txn_id": "T1",
                        "date": "2026-01-01",
                        "from_clabe": "ACC-V1",
                        "to_clabe": "ACC-UNKNOWN",
                        "amount": 100.0,
                        "reference": "x",
                        "channel": "SPEI",
                    },
                    {
                        "txn_id": "T2",
                        "date": "2026-01-02",
                        "from_clabe": "ACC-V1",
                        "to_clabe": "ACC-UNKNOWN",
                        "amount": 200.0,
                        "reference": "y",
                        "channel": "SPEI",
                    },
                    {
                        "txn_id": "T3",
                        "date": "2026-01-03",
                        "from_clabe": "ACC-UNKNOWN",
                        "to_clabe": "ACC-E1",
                        "amount": 75.0,
                        "reference": "z",
                        "channel": "SPEI",
                    },
                ]
            ),
            "invoices": pd.DataFrame(
                [
                    {
                        "uuid": "I1",
                        "issuer_rfc": "VEND1",
                        "receiver_rfc": "COMPANY",
                        "issue_date": "2026-01-01",
                        "subtotal": 100.0,
                        "iva": 16.0,
                        "total": 116.0,
                        "concepto_text": "service",
                        "uso_cfdi": "G03",
                        "forma_pago": "03",
                        "metodo_pago": "PUE",
                        "status": "active",
                    }
                ]
            ),
            "ledger": pd.DataFrame(
                [
                    {
                        "entry_id": "L1",
                        "date": "2026-01-01",
                        "account_code": "100",
                        "account_name": "Expense",
                        "debit": 116.0,
                        "credit": 0.0,
                        "description": "invoice",
                        "invoice_uuid": "I1",
                        "cost_center": "CC",
                        "approver": "EMP1",
                    }
                ]
            ),
            "purchase_orders": pd.DataFrame(
                [
                    {
                        "po_id": "PO1",
                        "vendor_rfc": "VEND1",
                        "date": "2025-12-30",
                        "amount": 116.0,
                        "requester": "EMP1",
                        "approver": "EMP2",
                        "description": "service",
                    }
                ]
            ),
            "contracts": pd.DataFrame(
                [
                    {
                        "contract_id": "C1",
                        "vendor_rfc": "VEND1",
                        "start_date": "2025-01-01",
                        "value": 1000.0,
                        "scope_text": "services",
                    }
                ]
            ),
            "efos_list": pd.DataFrame(
                [
                    {
                        "rfc": "VEND2",
                        "legal_name": "Vendor Two",
                        "status": "definitivo",
                        "publication_date": "2025-01-01",
                    }
                ]
            ),
        }

    def table_df(self, table: str) -> pd.DataFrame:
        return self.tables[table].copy()


def test_builds_expected_node_types_and_reversible_ids():
    bundle = build_gnn_graph(FakeEstate())

    assert set(bundle.data.node_types) == {"vendor", "employee", "account"}
    assert bundle.entity_index("vendor", "VEND1") == 0
    assert bundle.entity_id("vendor", 0) == "VEND1"
    assert bundle.entity_id("account", bundle.entity_index("account", "ACC-UNKNOWN")) == "ACC-UNKNOWN"


def test_unknown_clabe_is_preserved_as_account_node():
    bundle = build_gnn_graph(FakeEstate())
    assert "ACC-UNKNOWN" in bundle.node_ids["account"]


def test_vendor_and_employee_ownership_relations_exist_both_directions():
    bundle = build_gnn_graph(FakeEstate())

    assert bundle.data[VENDOR_OWNS_EDGE].edge_index.shape[1] == 2
    assert bundle.data[ACCOUNT_VENDOR_EDGE].edge_index.shape[1] == 2
    assert bundle.data[EMPLOYEE_OWNS_EDGE].edge_index.shape[1] == 2
    assert bundle.data[ACCOUNT_EMPLOYEE_EDGE].edge_index.shape[1] == 2


def test_shared_clabe_remains_shared_instead_of_forcing_single_owner():
    bundle = build_gnn_graph(FakeEstate())
    account_idx = bundle.entity_index("account", "ACC-SHARED")
    raw = bundle.raw_features["account"][account_idx]

    assert raw["vendor_owner_count"] == 1.0
    assert raw["employee_owner_count"] == 1.0


def test_parallel_bank_transactions_aggregate_for_gnn_but_keep_both_refs():
    bundle = build_gnn_graph(FakeEstate())
    edge_index = bundle.data[TRANSFER_EDGE].edge_index

    source_idx = bundle.entity_index("account", "ACC-V1")
    target_idx = bundle.entity_index("account", "ACC-UNKNOWN")
    matching = [
        idx
        for idx in range(edge_index.shape[1])
        if int(edge_index[0, idx]) == source_idx and int(edge_index[1, idx]) == target_idx
    ]
    assert len(matching) == 1

    edge_idx = matching[0]
    refs = bundle.edge_provenance[TRANSFER_EDGE][edge_idx]
    assert [ref.record_id for ref in refs] == ["T1", "T2"]

    raw_attr = bundle.data[TRANSFER_EDGE].edge_attr_raw[edge_idx]
    assert raw_attr[0].item() == 2.0
    assert raw_attr[1].item() == 300.0


def test_all_model_features_are_finite():
    bundle = build_gnn_graph(FakeEstate())

    for node_type in bundle.data.node_types:
        assert torch.isfinite(bundle.data[node_type].x).all()
    assert torch.isfinite(bundle.data[TRANSFER_EDGE].edge_attr).all()


def test_feature_contract_contains_no_ids_or_forensic_conclusions():
    bundle = build_gnn_graph(FakeEstate())

    forbidden = {"rfc", "emp_id", "clabe", "kickback", "round_trip", "fraud", "suspicious"}
    for names in bundle.feature_names.values():
        for name in names:
            assert name not in forbidden
            assert not any(token in name for token in ("kickback", "round_trip", "fraud", "suspicious"))


def test_raw_feature_snapshot_matches_known_low_level_facts():
    bundle = build_gnn_graph(FakeEstate())
    vendor_idx = bundle.entity_index("vendor", "VEND1")
    raw = bundle.raw_features["vendor"][vendor_idx]

    assert raw["invoice_count"] == 1.0
    assert raw["invoice_total"] == 116.0
    assert raw["po_count"] == 1.0
    assert raw["contract_count"] == 1.0
    assert raw["ledger_entry_count"] == 1.0
    assert raw["bank_txn_count_out"] == 2.0


def test_empty_bank_table_still_builds_owned_accounts():
    estate = FakeEstate()
    estate.tables["bank_txns"] = estate.tables["bank_txns"].iloc[0:0].copy()
    bundle = build_gnn_graph(estate)

    assert "ACC-V1" in bundle.node_ids["account"]
    assert "ACC-E1" in bundle.node_ids["account"]
    assert bundle.data[TRANSFER_EDGE].edge_index.shape == (2, 0)
    assert bundle.data[TRANSFER_EDGE].edge_attr.shape[0] == 0


def test_invoice_only_vendor_is_preserved_without_inventing_master_registration():
    estate = FakeEstate()
    extra = estate.tables["invoices"].iloc[0].copy()
    extra["uuid"] = "I-ORPHAN"
    extra["issuer_rfc"] = "VEND-NOT-IN-MASTER"
    estate.tables["invoices"] = pd.concat(
        [estate.tables["invoices"], pd.DataFrame([extra])], ignore_index=True
    )

    bundle = build_gnn_graph(estate)

    assert "VEND-NOT-IN-MASTER" in bundle.node_ids["vendor"]
    idx = bundle.entity_index("vendor", "VEND-NOT-IN-MASTER")
    assert bundle.raw_features["vendor"][idx]["registered_vendor"] == 0.0
    assert bundle.raw_features["vendor"][idx]["invoice_count"] == 1.0


def test_efos_rows_not_referenced_by_estate_do_not_expand_graph():
    estate = FakeEstate()
    extra = estate.tables["efos_list"].iloc[0].copy()
    extra["rfc"] = "EFOS-UNUSED"
    estate.tables["efos_list"] = pd.concat(
        [estate.tables["efos_list"], pd.DataFrame([extra])], ignore_index=True
    )

    bundle = build_gnn_graph(estate)

    assert "EFOS-UNUSED" not in bundle.node_ids["vendor"]


def test_vendor_unique_counterparties_counts_bidirectional_peer_once():
    estate = FakeEstate()
    reverse = estate.tables["bank_txns"].iloc[0].copy()
    reverse["txn_id"] = "T-RETURN"
    reverse["from_clabe"] = "ACC-UNKNOWN"
    reverse["to_clabe"] = "ACC-V1"
    reverse["amount"] = 25.0
    estate.tables["bank_txns"] = pd.concat(
        [estate.tables["bank_txns"], pd.DataFrame([reverse])], ignore_index=True
    )

    bundle = build_gnn_graph(estate)
    idx = bundle.entity_index("vendor", "VEND1")
    raw = bundle.raw_features["vendor"][idx]

    assert raw["bank_unique_counterparties"] == 1.0
