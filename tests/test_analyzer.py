import pytest
from pydantic import ValidationError

from graph_engine.analyzer import (
    analyze_graph,
)
from graph_engine.contracts import (
    GraphResult,
)
from graph_engine.mock_data import (
    MOCK_GRAPH_INPUT,
)


def test_analyze_graph_returns_graph_result():
    result = analyze_graph(MOCK_GRAPH_INPUT)

    assert isinstance(
        result,
        GraphResult,
    )


def test_analyze_graph_preserves_case_id():
    result = analyze_graph(MOCK_GRAPH_INPUT)

    assert result.case_id == "CASE_001"


def test_analyze_graph_returns_patterns():
    result = analyze_graph(MOCK_GRAPH_INPUT)

    assert result.patterns

    pattern_types = {pattern.pattern_type for pattern in result.patterns}

    assert "cycle" in pattern_types
    assert "temporal_cycle" in pattern_types
    assert "fan_in" in pattern_types
    assert "fan_out" in pattern_types


def test_analyze_graph_returns_features():
    result = analyze_graph(MOCK_GRAPH_INPUT)

    assert set(result.graph_features) == {
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
    }

    assert result.graph_features["B"].incoming_transaction_count == 4


def test_suspicious_entities_come_from_patterns():
    result = analyze_graph(MOCK_GRAPH_INPUT)

    expected_entities = {
        entity_id for pattern in result.patterns for entity_id in pattern.entity_ids
    }

    assert set(result.suspicious_entities) == expected_entities


def test_gnn_is_not_enabled_yet():
    result = analyze_graph(MOCK_GRAPH_INPUT)

    assert result.gnn_score is None
    assert result.gnn_model_info is None


def test_graph_result_can_be_serialized():
    result = analyze_graph(MOCK_GRAPH_INPUT)

    payload = result.model_dump(mode="json")

    assert isinstance(payload, dict)

    assert payload["case_id"] == "CASE_001"

    assert isinstance(
        payload["patterns"],
        list,
    )

    assert isinstance(
        payload["graph_features"],
        dict,
    )


def test_invalid_graph_input_is_rejected():
    invalid_input = {
        "target_entity_id": "A",
        "entities": [],
        "transactions": [],
    }

    with pytest.raises(ValidationError):
        analyze_graph(invalid_input)
