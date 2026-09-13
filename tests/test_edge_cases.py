import pytest

from graph_engine.analyzer import (
    analyze_graph,
)
from graph_engine.builder import build_graph
from graph_engine.features import (
    extract_graph_features,
)
from graph_engine.flow import (
    find_temporal_cycles,
)
from graph_engine.patterns import (
    find_cycles,
    find_fan_in,
)


def test_empty_case_returns_empty_result():
    data = {
        "case_id": "EMPTY_CASE",
        "target_entity_id": None,
        "entities": [],
        "accounts": [],
        "transactions": [],
    }

    result = analyze_graph(data)

    assert result.case_id == "EMPTY_CASE"
    assert result.patterns == []
    assert result.suspicious_entities == []
    assert result.graph_features == {}


def test_unknown_target_entity_is_rejected():
    data = {
        "case_id": "CASE_BAD_TARGET",
        "target_entity_id": "X",
        "entities": [
            {
                "entity_id": "A",
                "name": "A",
                "entity_type": "company",
                "rfc": None,
            }
        ],
        "accounts": [],
        "transactions": [],
    }

    with pytest.raises(
        ValueError,
        match="Unknown target entity",
    ):
        analyze_graph(data)


def test_missing_amount_is_rejected():
    data = {
        "case_id": "CASE_BAD_AMOUNT",
        "target_entity_id": "A",
        "entities": [
            {
                "entity_id": "A",
                "name": "A",
                "entity_type": "company",
                "rfc": None,
            },
            {
                "entity_id": "B",
                "name": "B",
                "entity_type": "company",
                "rfc": None,
            },
        ],
        "accounts": [],
        "transactions": [
            {
                "transaction_id": "T_BAD",
                "timestamp": None,
                "sender_entity_id": "A",
                "receiver_entity_id": "B",
                "amount": None,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            }
        ],
    }

    with pytest.raises(
        ValueError,
        match="has no amount",
    ):
        analyze_graph(data)


def test_invalid_amount_is_rejected():
    data = {
        "case_id": "CASE_INVALID_AMOUNT",
        "target_entity_id": "A",
        "entities": [
            {
                "entity_id": "A",
                "name": "A",
                "entity_type": "company",
                "rfc": None,
            },
            {
                "entity_id": "B",
                "name": "B",
                "entity_type": "company",
                "rfc": None,
            },
        ],
        "accounts": [],
        "transactions": [
            {
                "transaction_id": "T_BAD",
                "timestamp": None,
                "sender_entity_id": "A",
                "receiver_entity_id": "B",
                "amount": "banana",
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            }
        ],
    }

    with pytest.raises(
        ValueError,
        match="has an invalid amount",
    ):
        analyze_graph(data)


def test_missing_timestamp_keeps_structural_cycle():
    data = {
        "case_id": "NO_TIME",
        "target_entity_id": "A",
        "entities": [
            {
                "entity_id": entity_id,
                "name": entity_id,
                "entity_type": "company",
                "rfc": None,
            }
            for entity_id in [
                "A",
                "B",
                "C",
            ]
        ],
        "accounts": [],
        "transactions": [
            {
                "transaction_id": "T1",
                "timestamp": None,
                "sender_entity_id": "A",
                "receiver_entity_id": "B",
                "amount": 100.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            },
            {
                "transaction_id": "T2",
                "timestamp": None,
                "sender_entity_id": "B",
                "receiver_entity_id": "C",
                "amount": 95.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            },
            {
                "transaction_id": "T3",
                "timestamp": None,
                "sender_entity_id": "C",
                "receiver_entity_id": "A",
                "amount": 90.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            },
        ],
    }

    graph = build_graph(data)

    structural_cycles = find_cycles(graph)

    temporal_cycles = find_temporal_cycles(graph)

    assert structural_cycles
    assert temporal_cycles == []


def test_self_transaction_is_counted_but_not_cycle():
    data = {
        "case_id": "SELF_LOOP",
        "target_entity_id": "A",
        "entities": [
            {
                "entity_id": "A",
                "name": "A",
                "entity_type": "company",
                "rfc": None,
            }
        ],
        "accounts": [],
        "transactions": [
            {
                "transaction_id": "SELF_1",
                "timestamp": None,
                "sender_entity_id": "A",
                "receiver_entity_id": "A",
                "amount": 1000.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            }
        ],
    }

    graph = build_graph(data)

    features = extract_graph_features(
        graph,
        "A",
    )

    cycles = find_cycles(graph)

    assert features.self_transaction_count == 1

    assert cycles == []


def test_missing_source_ref_does_not_crash():
    data = {
        "case_id": "NO_SOURCE_REF",
        "target_entity_id": "A",
        "entities": [
            {
                "entity_id": entity_id,
                "name": entity_id,
                "entity_type": "company",
                "rfc": None,
            }
            for entity_id in [
                "A",
                "B",
            ]
        ],
        "accounts": [],
        "transactions": [
            {
                "transaction_id": "T1",
                "timestamp": None,
                "sender_entity_id": "A",
                "receiver_entity_id": "B",
                "amount": 1000.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            },
            {
                "transaction_id": "T2",
                "timestamp": None,
                "sender_entity_id": "B",
                "receiver_entity_id": "A",
                "amount": 900.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            },
        ],
    }

    graph = build_graph(data)

    cycles = find_cycles(graph)

    assert cycles
    assert cycles[0].evidence_refs == []


def test_mixed_currencies_are_not_summed():
    data = {
        "case_id": "MULTI_CURRENCY",
        "target_entity_id": "B",
        "entities": [
            {
                "entity_id": entity_id,
                "name": entity_id,
                "entity_type": "company",
                "rfc": None,
            }
            for entity_id in [
                "A",
                "B",
                "C",
            ]
        ],
        "accounts": [],
        "transactions": [
            {
                "transaction_id": "MXN_1",
                "timestamp": None,
                "sender_entity_id": "A",
                "receiver_entity_id": "B",
                "amount": 10_000.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            },
            {
                "transaction_id": "USD_1",
                "timestamp": None,
                "sender_entity_id": "C",
                "receiver_entity_id": "B",
                "amount": 100.0,
                "currency": "USD",
                "description": None,
                "source_ref": None,
            },
        ],
    }

    graph = build_graph(data)

    patterns = find_fan_in(
        graph,
        entity_id="B",
        min_unique_senders=2,
    )

    assert len(patterns) == 1

    assert patterns[0].total_amount is None


def test_duplicate_transaction_id_is_rejected():
    data = {
        "case_id": "DUPLICATE_TX",
        "target_entity_id": "A",
        "entities": [
            {
                "entity_id": entity_id,
                "name": entity_id,
                "entity_type": "company",
                "rfc": None,
            }
            for entity_id in [
                "A",
                "B",
            ]
        ],
        "accounts": [],
        "transactions": [
            {
                "transaction_id": "T1",
                "timestamp": None,
                "sender_entity_id": "A",
                "receiver_entity_id": "B",
                "amount": 100.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            },
            {
                "transaction_id": "T1",
                "timestamp": None,
                "sender_entity_id": "B",
                "receiver_entity_id": "A",
                "amount": 100.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            },
        ],
    }

    with pytest.raises(
        ValueError,
        match="Duplicate transaction_id",
    ):
        analyze_graph(data)


def test_unknown_transaction_entity_is_rejected():
    data = {
        "case_id": "UNKNOWN_ENTITY",
        "target_entity_id": "A",
        "entities": [
            {
                "entity_id": "A",
                "name": "A",
                "entity_type": "company",
                "rfc": None,
            }
        ],
        "accounts": [],
        "transactions": [
            {
                "transaction_id": "T1",
                "timestamp": None,
                "sender_entity_id": "A",
                "receiver_entity_id": "B",
                "amount": 100.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            }
        ],
    }

    with pytest.raises(
        ValueError,
        match="Unknown receiver entity",
    ):
        analyze_graph(data)


def test_normal_graph_has_no_patterns():
    data = {
        "case_id": "NORMAL",
        "target_entity_id": "A",
        "entities": [
            {
                "entity_id": entity_id,
                "name": entity_id,
                "entity_type": "company",
                "rfc": None,
            }
            for entity_id in [
                "A",
                "B",
                "C",
            ]
        ],
        "accounts": [],
        "transactions": [
            {
                "transaction_id": "T1",
                "timestamp": None,
                "sender_entity_id": "A",
                "receiver_entity_id": "B",
                "amount": 100.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            },
            {
                "transaction_id": "T2",
                "timestamp": None,
                "sender_entity_id": "B",
                "receiver_entity_id": "C",
                "amount": 200.0,
                "currency": "MXN",
                "description": None,
                "source_ref": None,
            },
        ],
    }

    result = analyze_graph(data)

    assert result.patterns == []

    assert result.suspicious_entities == []