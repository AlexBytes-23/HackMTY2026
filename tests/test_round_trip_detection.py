from __future__ import annotations

import pandas as pd

from src.detectors.deterministic_relational import detect_directed_bank_transfer_cycles


def _bank_txns():
    return pd.DataFrame(
        [
            {
                "txn_id": "TX-AB",
                "date": "2026-01-10",
                "from_clabe": "A",
                "to_clabe": "B",
                "amount": 100000.0,
                "reference": "one",
                "channel": "SPEI",
            },
            {
                "txn_id": "TX-BC",
                "date": "2026-01-10",
                "from_clabe": "B",
                "to_clabe": "C",
                "amount": 98000.0,
                "reference": "two",
                "channel": "SPEI",
            },
            {
                "txn_id": "TX-CA",
                "date": "2026-01-10",
                "from_clabe": "C",
                "to_clabe": "A",
                "amount": 97000.0,
                "reference": "three",
                "channel": "SPEI",
            },
        ]
    )


def test_directed_cycle_becomes_structural_observation_with_exact_refs():
    observations = detect_directed_bank_transfer_cycles(_bank_txns())

    assert len(observations) == 1
    observation = observations[0]
    assert observation.signal_type == "directed_bank_transfer_cycle"
    assert observation.facts["account_cycle"] == ["A", "B", "C", "A"]
    assert observation.facts["transaction_ids"] == ["TX-AB", "TX-BC", "TX-CA"]
    assert [(ref.source_table, ref.record_id) for ref in observation.evidence] == [
        ("bank_txns", "TX-AB"),
        ("bank_txns", "TX-BC"),
        ("bank_txns", "TX-CA"),
    ]
    assert observation.score is None


def test_cycle_language_does_not_claim_same_funds_or_round_trip_proof():
    observation = detect_directed_bank_transfer_cycles(_bank_txns())[0]

    assert "round-tripping fraud" not in observation.statement.lower()
    assert any("same funds" in item.lower() for item in observation.limitations)
    assert any("intraday" in item.lower() for item in observation.limitations)


def test_no_directed_cycle_produces_no_cycle_observation():
    bank_txns = _bank_txns().iloc[:2].copy()
    assert detect_directed_bank_transfer_cycles(bank_txns) == []
