import pytest
from pydantic import ValidationError

from graph_engine.builder import build_graph
from graph_engine.contracts import GraphPattern
from graph_engine.mock_data import MOCK_GRAPH_INPUT
from graph_engine.patterns import find_cycles


def test_invalid_pattern_is_rejected():
    with pytest.raises(ValidationError):
        GraphPattern(
            pattern_type="cycle",
            entity_ids=["A"],
            transaction_ids=[],
            start_time="banana",
            description="Invalid test pattern.",
        )


def test_pattern_can_be_serialized():
    graph = build_graph(MOCK_GRAPH_INPUT)

    pattern = find_cycles(graph)[0]

    payload = pattern.model_dump(mode="json")

    assert isinstance(payload, dict)

    assert isinstance(
        payload["pattern_type"],
        str,
    )

    assert isinstance(
        payload["entity_ids"],
        list,
    )
