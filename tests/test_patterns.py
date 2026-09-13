from graph_engine.builder import build_graph
from graph_engine.mock_data import MOCK_GRAPH_INPUT
from graph_engine.patterns import (
    find_cycles,
    find_fan_in,
    find_fan_out,
)


def test_finds_abc_cycle():
    graph = build_graph(MOCK_GRAPH_INPUT)

    cycles = find_cycles(
        graph,
        max_length=5,
    )

    found = any(
        set(pattern.entity_ids) == {"A", "B", "C"} and len(pattern.entity_ids) == 3
        for pattern in cycles
    )

    assert found


def test_cycle_contains_transaction_evidence():
    graph = build_graph(MOCK_GRAPH_INPUT)

    cycles = find_cycles(graph)

    abc_cycle = next(
        pattern
        for pattern in cycles
        if set(pattern.entity_ids) == {"A", "B", "C"} and len(pattern.entity_ids) == 3
    )

    assert "T001" in abc_cycle.transaction_ids
    assert "T002" in abc_cycle.transaction_ids
    assert "T003" in abc_cycle.transaction_ids
    assert "T008" in abc_cycle.transaction_ids


def test_finds_b_fan_in():
    graph = build_graph(MOCK_GRAPH_INPUT)

    patterns = find_fan_in(
        graph,
        min_unique_senders=3,
    )

    b_fan_in = next(pattern for pattern in patterns if pattern.entity_ids[0] == "B")

    assert set(b_fan_in.entity_ids[1:]) == {
        "A",
        "D",
        "E",
    }

    assert set(b_fan_in.transaction_ids) == {
        "T001",
        "T004",
        "T005",
        "T008",
    }

    assert b_fan_in.total_amount == 147_000.0


def test_finds_b_fan_out():
    graph = build_graph(MOCK_GRAPH_INPUT)

    patterns = find_fan_out(
        graph,
        min_unique_receivers=3,
    )

    b_fan_out = next(pattern for pattern in patterns if pattern.entity_ids[0] == "B")

    assert set(b_fan_out.entity_ids[1:]) == {
        "C",
        "D",
        "F",
    }

    assert set(b_fan_out.transaction_ids) == {
        "T002",
        "T006",
        "T007",
    }

    assert b_fan_out.total_amount == 153_000.0


def test_cycle_entity_filter():
    graph = build_graph(MOCK_GRAPH_INPUT)

    cycles = find_cycles(
        graph,
        entity_id="A",
    )

    assert cycles

    assert all("A" in pattern.entity_ids for pattern in cycles)


def test_cycle_preserves_transaction_amounts():
    graph = build_graph(MOCK_GRAPH_INPUT)

    cycles = find_cycles(graph)

    abc_cycle = next(
        pattern
        for pattern in cycles
        if set(pattern.entity_ids) == {"A", "B", "C"} and len(pattern.entity_ids) == 3
    )

    assert abc_cycle.transaction_amounts["T001"] == 100_000.0

    assert abc_cycle.transaction_amounts["T002"] == 98_000.0

    assert abc_cycle.transaction_amounts["T003"] == 97_000.0


def test_pattern_preserves_source_references():
    graph = build_graph(MOCK_GRAPH_INPUT)

    cycles = find_cycles(graph)

    abc_cycle = next(
        pattern
        for pattern in cycles
        if set(pattern.entity_ids) == {"A", "B", "C"} and len(pattern.entity_ids) == 3
    )

    rows = {ref.row for ref in abc_cycle.evidence_refs}

    assert 1 in rows
    assert 2 in rows
    assert 3 in rows
