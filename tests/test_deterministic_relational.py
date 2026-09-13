from __future__ import annotations

import pandas as pd

from src.detectors.deterministic_relational import (
    detect_short_window_similar_invoice_clusters,
    detect_vendor_to_employee_transfers,
    run_deterministic_relational_detectors,
)


def _vendors():
    return pd.DataFrame(
        [
            {"rfc": "AAA010101AAA", "bank_clabe": "111111111111111111"},
        ]
    )


def _employees():
    return pd.DataFrame(
        [
            {"emp_id": "EMP001", "bank_clabe": "222222222222222222"},
        ]
    )


def test_vendor_to_employee_transfer_has_exact_source_records():
    bank_txns = pd.DataFrame(
        [
            {
                "txn_id": "TX-1",
                "date": "2026-01-10",
                "from_clabe": "111111111111111111",
                "to_clabe": "222222222222222222",
                "amount": 25000.0,
                "reference": "transfer",
                "channel": "SPEI",
            }
        ]
    )

    observations = detect_vendor_to_employee_transfers(
        _vendors(), _employees(), bank_txns
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.signal_type == "vendor_to_employee_bank_transfer"
    assert observation.facts["amount"] == 25000.0
    assert [(ref.source_table, ref.record_id) for ref in observation.evidence] == [
        ("vendors", "AAA010101AAA"),
        ("employees", "EMP001"),
        ("bank_txns", "TX-1"),
    ]
    assert observation.score is None


def test_reverse_employee_to_vendor_transfer_is_not_same_signal():
    bank_txns = pd.DataFrame(
        [
            {
                "txn_id": "TX-1",
                "date": "2026-01-10",
                "from_clabe": "222222222222222222",
                "to_clabe": "111111111111111111",
                "amount": 25000.0,
            }
        ]
    )

    assert detect_vendor_to_employee_transfers(
        _vendors(), _employees(), bank_txns
    ) == []


def test_short_window_similar_invoice_cluster_is_observation_not_threshold_claim():
    invoices = pd.DataFrame(
        [
            {
                "uuid": "INV-1",
                "issuer_rfc": "AAA010101AAA",
                "receiver_rfc": "AUD010101AAA",
                "issue_date": "2026-02-01",
                "total": 100000.0,
            },
            {
                "uuid": "INV-2",
                "issuer_rfc": "AAA010101AAA",
                "receiver_rfc": "AUD010101AAA",
                "issue_date": "2026-02-03",
                "total": 101000.0,
            },
            {
                "uuid": "INV-3",
                "issuer_rfc": "AAA010101AAA",
                "receiver_rfc": "AUD010101AAA",
                "issue_date": "2026-02-05",
                "total": 99000.0,
            },
        ]
    )

    observations = detect_short_window_similar_invoice_clusters(invoices)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.signal_type == "short_window_similar_invoice_cluster"
    assert observation.facts["invoice_count"] == 3
    assert observation.facts["cluster_total"] == 300000.0
    assert len(observation.evidence) == 3
    assert any(
        "does not provide an approval threshold" in item
        for item in observation.limitations
    )
    assert "threshold splitting" not in observation.statement.lower()


def test_monthly_recurring_invoices_do_not_form_short_window_cluster():
    invoices = pd.DataFrame(
        [
            {
                "uuid": "INV-1",
                "issuer_rfc": "AAA010101AAA",
                "receiver_rfc": "AUD010101AAA",
                "issue_date": "2026-01-01",
                "total": 50000.0,
            },
            {
                "uuid": "INV-2",
                "issuer_rfc": "AAA010101AAA",
                "receiver_rfc": "AUD010101AAA",
                "issue_date": "2026-02-01",
                "total": 50000.0,
            },
            {
                "uuid": "INV-3",
                "issuer_rfc": "AAA010101AAA",
                "receiver_rfc": "AUD010101AAA",
                "issue_date": "2026-03-01",
                "total": 50000.0,
            },
        ]
    )

    assert detect_short_window_similar_invoice_clusters(invoices) == []


def test_similar_invoices_to_different_receivers_are_not_combined():
    invoices = pd.DataFrame(
        [
            {
                "uuid": "INV-1",
                "issuer_rfc": "AAA010101AAA",
                "receiver_rfc": "REC010101AAA",
                "issue_date": "2026-02-01",
                "total": 100000.0,
            },
            {
                "uuid": "INV-2",
                "issuer_rfc": "AAA010101AAA",
                "receiver_rfc": "REC020202BBB",
                "issue_date": "2026-02-02",
                "total": 100000.0,
            },
            {
                "uuid": "INV-3",
                "issuer_rfc": "AAA010101AAA",
                "receiver_rfc": "REC030303CCC",
                "issue_date": "2026-02-03",
                "total": 100000.0,
            },
        ]
    )

    assert detect_short_window_similar_invoice_clusters(invoices) == []


class FakeEstate:
    def __init__(self):
        self.tables = {
            "vendors": _vendors(),
            "employees": _employees(),
            "bank_txns": pd.DataFrame(
                [
                    {
                        "txn_id": "TX-1",
                        "date": "2026-01-10",
                        "from_clabe": "111111111111111111",
                        "to_clabe": "222222222222222222",
                        "amount": 10000.0,
                    }
                ]
            ),
            "invoices": pd.DataFrame(
                columns=["uuid", "issuer_rfc", "receiver_rfc", "issue_date", "total"]
            ),
        }

    def table_df(self, table):
        return self.tables[table].copy()


def test_relational_runner_uses_repository_dataframe_boundary():
    observations = run_deterministic_relational_detectors(FakeEstate())
    assert [obs.signal_type for obs in observations] == [
        "vendor_to_employee_bank_transfer"
    ]
