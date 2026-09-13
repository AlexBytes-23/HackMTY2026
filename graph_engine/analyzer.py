from graph_engine.builder import build_graph
from graph_engine.contracts import (
    GraphInput,
    GraphPattern,
    GraphResult,
)
from graph_engine.features import (
    extract_all_graph_features,
)
from graph_engine.flow import (
    find_temporal_cycles,
)
from graph_engine.patterns import (
    find_cycles,
    find_fan_in,
    find_fan_out,
)


def _collect_suspicious_entities(
    patterns: list[GraphPattern],
) -> list[str]:
    entities = {entity_id for pattern in patterns for entity_id in pattern.entity_ids}

    return sorted(entities)


def analyze_graph(
    graph_input: GraphInput | dict,
) -> GraphResult:
    if isinstance(graph_input, dict):
        graph_input = GraphInput.model_validate(graph_input)

    graph = build_graph(graph_input.model_dump())

    cycles = find_cycles(
        graph,
        max_length=5,
    )

    temporal_cycles = find_temporal_cycles(
        graph,
        max_length=5,
        max_hours=24,
    )

    fan_in_patterns = find_fan_in(
        graph,
        min_unique_senders=3,
    )

    fan_out_patterns = find_fan_out(
        graph,
        min_unique_receivers=3,
    )

    patterns = [
        *cycles,
        *temporal_cycles,
        *fan_in_patterns,
        *fan_out_patterns,
    ]

    graph_features = extract_all_graph_features(
        graph,
        patterns=patterns,
    )

    suspicious_entities = _collect_suspicious_entities(patterns)

    limitations = [
        (
            "Detected graph patterns are "
            "structural observations and do "
            "not establish fraud."
        ),
        (
            "Temporal cycles establish "
            "chronological compatibility, "
            "not that the same funds moved "
            "through the full cycle."
        ),
        (
            "Results only reflect entities "
            "and transactions present in "
            "the input dataset."
        ),
        ("Account-level relationships are not yet represented in the graph."),
        ("GNN scoring is not enabled in this version."),
    ]

    return GraphResult(
        case_id=graph_input.case_id,
        patterns=patterns,
        suspicious_entities=(suspicious_entities),
        graph_features=graph_features,
        limitations=limitations,
        gnn_score=None,
        gnn_model_info=None,
    )
