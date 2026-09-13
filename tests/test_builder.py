from graph_engine.builder import build_graph
from graph_engine.mock_data import MOCK_GRAPH_INPUT


def test_build_graph():
    graph = build_graph(MOCK_GRAPH_INPUT)

    assert graph.is_directed()
    assert graph.is_multigraph()

    assert graph.number_of_nodes() == 6
    assert graph.number_of_edges() == 9


def test_parallel_transactions_are_preserved():
    graph = build_graph(MOCK_GRAPH_INPUT)

    assert graph.number_of_edges("A", "B") == 2

    assert graph.has_edge(
        "A",
        "B",
        key="T001",
    )

    assert graph.has_edge(
        "A",
        "B",
        key="T008",
    )


def test_transaction_attributes_are_preserved():
    graph = build_graph(MOCK_GRAPH_INPUT)

    transaction = graph.edges[
        "A",
        "B",
        "T001",
    ]

    assert transaction["amount"] == 100_000.0
    assert transaction["currency"] == "MXN"
    assert transaction["transaction_id"] == "T001"


def test_case_metadata_is_preserved():
    graph = build_graph(MOCK_GRAPH_INPUT)

    assert graph.graph["case_id"] == "CASE_001"
    assert graph.graph["target_entity_id"] == "A"
