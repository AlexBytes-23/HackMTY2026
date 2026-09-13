from graph_engine.builder import build_graph
from graph_engine.flow import (
    find_money_paths,
    find_temporal_cycles,
)
from graph_engine.mock_data import (
    MOCK_GRAPH_INPUT,
)


def test_finds_temporal_abc_cycle():
    graph = build_graph(MOCK_GRAPH_INPUT)

    cycles = find_temporal_cycles(
        graph,
        max_length=5,
        max_hours=24,
    )

    found = any(
        set(cycle.entity_ids) == {"A", "B", "C"}
        and {
            "T001",
            "T002",
            "T003",
        }.issubset(set(cycle.transaction_ids))
        for cycle in cycles
    )

    assert found


def test_temporal_cycle_respects_time_limit():
    graph = build_graph(MOCK_GRAPH_INPUT)

    cycles = find_temporal_cycles(
        graph,
        entity_id="A",
        max_length=3,
        max_hours=1,
    )

    abc_cycles = [cycle for cycle in cycles if set(cycle.entity_ids) == {"A", "B", "C"}]

    assert abc_cycles == []


def test_finds_path_from_a_to_f():
    graph = build_graph(MOCK_GRAPH_INPUT)

    paths = find_money_paths(
        graph,
        source_id="A",
        target_id="F",
        max_depth=3,
    )

    assert any(path["entity_path"] == ["A", "B", "F"] for path in paths)


def test_money_paths_respect_direction():
    graph = build_graph(MOCK_GRAPH_INPUT)

    paths = find_money_paths(
        graph,
        source_id="A",
        target_id="F",
        max_depth=1,
    )

    assert paths == []


def test_path_preserves_parallel_transactions():
    graph = build_graph(MOCK_GRAPH_INPUT)

    paths = find_money_paths(
        graph,
        source_id="A",
        target_id="B",
        max_depth=1,
    )

    direct_path = next(path for path in paths if path["entity_path"] == ["A", "B"])

    transaction_ids = {
        transaction["transaction_id"]
        for transaction in direct_path["hops"][0]["transactions"]
    }

    assert transaction_ids == {
        "T001",
        "T008",
    }


def test_money_paths_do_not_duplicate_parallel_edges():
    graph = build_graph(MOCK_GRAPH_INPUT)

    paths = find_money_paths(
        graph,
        source_id="A",
        target_id="B",
        max_depth=1,
    )

    direct_paths = [path for path in paths if path["entity_path"] == ["A", "B"]]

    assert len(direct_paths) == 1

    transaction_ids = {
        transaction["transaction_id"]
        for transaction in direct_paths[0]["hops"][0]["transactions"]
    }

    assert transaction_ids == {
        "T001",
        "T008",
    }
