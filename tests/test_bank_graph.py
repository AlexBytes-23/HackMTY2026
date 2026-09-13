from __future__ import annotations

import pandas as pd
import pytest

from src.graph.bank_graph import build_bank_multidigraph, find_directed_bank_cycles


def _txns(rows):
    defaults = {"date": "2026-01-01", "reference": "ref", "channel": "SPEI"}
    return pd.DataFrame([{**defaults, **row} for row in rows])


def test_bank_graph_preserves_unknown_clabes_and_parallel_transactions():
    bank_txns = _txns(
        [
            {
                "txn_id": "TX-1",
                "from_clabe": "UNKNOWN-A",
                "to_clabe": "UNKNOWN-B",
                "amount": 100.0,
            },
            {
                "txn_id": "TX-2",
                "from_clabe": "UNKNOWN-A",
                "to_clabe": "UNKNOWN-B",
                "amount": 200.0,
            },
        ]
    )

    graph = build_bank_multidigraph(bank_txns)

    assert set(graph.nodes) == {"UNKNOWN-A", "UNKNOWN-B"}
    assert graph.number_of_edges("UNKNOWN-A", "UNKNOWN-B") == 2
    assert set(graph["UNKNOWN-A"]["UNKNOWN-B"].keys()) == {"TX-1", "TX-2"}
    assert graph["UNKNOWN-A"]["UNKNOWN-B"]["TX-1"]["record_id"] == "TX-1"


def test_bank_graph_rejects_duplicate_transaction_ids_instead_of_overwriting():
    bank_txns = _txns(
        [
            {
                "txn_id": "TX-DUP",
                "from_clabe": "A",
                "to_clabe": "B",
                "amount": 100.0,
            },
            {
                "txn_id": "TX-DUP",
                "from_clabe": "B",
                "to_clabe": "C",
                "amount": 100.0,
            },
        ]
    )

    with pytest.raises(ValueError, match="duplicate txn_id"):
        build_bank_multidigraph(bank_txns)


def test_cycle_search_returns_exact_transaction_provenance():
    graph = build_bank_multidigraph(
        _txns(
            [
                {"txn_id": "TX-AB", "from_clabe": "A", "to_clabe": "B", "amount": 90.0},
                {"txn_id": "TX-BC", "from_clabe": "B", "to_clabe": "C", "amount": 80.0},
                {"txn_id": "TX-CA", "from_clabe": "C", "to_clabe": "A", "amount": 70.0},
            ]
        )
    )

    cycles = find_directed_bank_cycles(graph, max_cycle_length=4)

    assert len(cycles) == 1
    cycle = cycles[0]
    assert cycle.closed_account_path == ("A", "B", "C", "A")
    assert cycle.transaction_ids == ("TX-AB", "TX-BC", "TX-CA")
    assert cycle.amounts == (90.0, 80.0, 70.0)


def test_cycle_search_does_not_invent_return_edge():
    graph = build_bank_multidigraph(
        _txns(
            [
                {"txn_id": "TX-AB", "from_clabe": "A", "to_clabe": "B", "amount": 90.0},
                {"txn_id": "TX-BC", "from_clabe": "B", "to_clabe": "C", "amount": 80.0},
            ]
        )
    )

    assert find_directed_bank_cycles(graph) == []
