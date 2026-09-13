import pytest

from graph_engine.builder import build_graph
from graph_engine.mock_data import MOCK_GRAPH_INPUT
from graph_engine.traversal import get_neighbors, get_subgraph


def test_get_outgoing_neighbors():
    graph = build_graph(MOCK_GRAPH_INPUT)

    neighbors = get_neighbors(
        graph,
        "B",
        depth=1,
        direction="out",
    )

    assert neighbors == {"C", "D", "F"}


def test_get_incoming_neighbors():
    graph = build_graph(MOCK_GRAPH_INPUT)

    neighbors = get_neighbors(
        graph,
        "B",
        depth=1,
        direction="in",
    )

    assert neighbors == {"A", "D", "E"}


def test_get_neighbors_both_directions():
    graph = build_graph(MOCK_GRAPH_INPUT)

    neighbors = get_neighbors(
        graph,
        "B",
        depth=1,
        direction="both",
    )

    assert neighbors == {"A", "C", "D", "E", "F"}


def test_depth_two_expands_network():
    graph = build_graph(MOCK_GRAPH_INPUT)

    depth_one = get_neighbors(
        graph,
        "A",
        depth=1,
    )

    depth_two = get_neighbors(
        graph,
        "A",
        depth=2,
    )

    assert depth_one < depth_two


def test_unknown_entity_raises_error():
    graph = build_graph(MOCK_GRAPH_INPUT)

    with pytest.raises(ValueError):
        get_neighbors(
            graph,
            "UNKNOWN",
        )


def test_invalid_depth_raises_error():
    graph = build_graph(MOCK_GRAPH_INPUT)

    with pytest.raises(ValueError):
        get_neighbors(
            graph,
            "A",
            depth=0,
        )


def test_subgraph_contains_target():
    graph = build_graph(MOCK_GRAPH_INPUT)

    subgraph = get_subgraph(
        graph,
        "A",
        depth=1,
    )

    assert "A" in subgraph


def test_subgraph_preserves_parallel_edges():
    graph = build_graph(MOCK_GRAPH_INPUT)

    subgraph = get_subgraph(
        graph,
        "A",
        depth=1,
    )

    assert (
        subgraph.number_of_edges(
            "A",
            "B",
        )
        == 2
    )
