import pytest

from graph_engine.builder import build_graph
from graph_engine.features import (
    extract_all_graph_features,
    extract_graph_features,
)
from graph_engine.flow import (
    find_temporal_cycles,
)
from graph_engine.mock_data import (
    MOCK_GRAPH_INPUT,
)
from graph_engine.patterns import find_cycles


def test_extracts_b_transaction_features():
    graph = build_graph(MOCK_GRAPH_INPUT)

    features = extract_graph_features(
        graph,
        "B",
    )

    assert features.incoming_transaction_count == 4

    assert features.outgoing_transaction_count == 3

    assert features.unique_senders == 3
    assert features.unique_receivers == 3

    assert features.unique_counterparties == 5


def test_extracts_b_amount_features():
    graph = build_graph(MOCK_GRAPH_INPUT)

    features = extract_graph_features(
        graph,
        "B",
    )

    assert features.incoming_amount_total == 147_000.0

    assert features.outgoing_amount_total == 153_000.0

    assert features.average_incoming_amount == 36_750.0

    assert features.average_outgoing_amount == 51_000.0

    assert features.net_flow == -6_000.0


def test_extracts_b_fan_degrees():
    graph = build_graph(MOCK_GRAPH_INPUT)

    features = extract_graph_features(
        graph,
        "B",
    )

    assert features.fan_in_degree == 3
    assert features.fan_out_degree == 3


def test_extracts_pattern_counts():
    graph = build_graph(MOCK_GRAPH_INPUT)

    cycles = find_cycles(graph)

    temporal_cycles = find_temporal_cycles(graph)

    patterns = [
        *cycles,
        *temporal_cycles,
    ]

    features = extract_graph_features(
        graph,
        "B",
        patterns=patterns,
    )

    expected_cycles = sum(1 for pattern in cycles if "B" in pattern.entity_ids)

    expected_temporal_cycles = sum(
        1 for pattern in temporal_cycles if "B" in pattern.entity_ids
    )

    assert features.cycle_count == expected_cycles

    assert features.temporal_cycle_count == expected_temporal_cycles


import networkx as nx


def test_zero_transaction_averages():
    graph = nx.MultiDiGraph()

    graph.add_node("Z")

    features = extract_graph_features(
        graph,
        "Z",
    )

    assert features.incoming_transaction_count == 0

    assert features.outgoing_transaction_count == 0

    assert features.average_incoming_amount == 0.0

    assert features.average_outgoing_amount == 0.0

    assert features.net_flow == 0.0


def test_unknown_entity_raises_error():
    graph = build_graph(MOCK_GRAPH_INPUT)

    with pytest.raises(ValueError):
        extract_graph_features(
            graph,
            "DOES_NOT_EXIST",
        )


def test_extracts_features_for_all_entities():
    graph = build_graph(MOCK_GRAPH_INPUT)

    features = extract_all_graph_features(graph)

    assert set(features) == {
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
    }

    assert features["B"].entity_id == "B"
