import networkx as nx

from graph_engine.contracts import (
    GraphFeatures,
    GraphPattern,
)


def _sum_amounts(
    edges: list[tuple],
) -> float:
    return sum(float(data.get("amount", 0) or 0) for *_, data in edges)


def _count_patterns(
    patterns: list[GraphPattern],
    entity_id: str,
    pattern_type: str,
) -> int:
    return sum(
        1
        for pattern in patterns
        if (pattern.pattern_type == pattern_type and entity_id in pattern.entity_ids)
    )


def extract_graph_features(
    graph: nx.MultiDiGraph,
    entity_id: str,
    patterns: list[GraphPattern] | None = None,
) -> GraphFeatures:
    if entity_id not in graph:
        raise ValueError(f"Unknown entity_id: {entity_id}")

    if patterns is None:
        patterns = []

    incoming_edges = list(
        graph.in_edges(
            entity_id,
            keys=True,
            data=True,
        )
    )

    outgoing_edges = list(
        graph.out_edges(
            entity_id,
            keys=True,
            data=True,
        )
    )

    incoming_transaction_count = len(incoming_edges)

    outgoing_transaction_count = len(outgoing_edges)

    senders = set(graph.predecessors(entity_id))

    receivers = set(graph.successors(entity_id))

    senders.discard(entity_id)
    receivers.discard(entity_id)

    counterparties = senders | receivers

    incoming_amount_total = _sum_amounts(incoming_edges)

    outgoing_amount_total = _sum_amounts(outgoing_edges)

    if incoming_transaction_count:
        average_incoming_amount = incoming_amount_total / incoming_transaction_count
    else:
        average_incoming_amount = 0.0

    if outgoing_transaction_count:
        average_outgoing_amount = outgoing_amount_total / outgoing_transaction_count
    else:
        average_outgoing_amount = 0.0

    net_flow = incoming_amount_total - outgoing_amount_total

    self_transaction_count = graph.number_of_edges(
        entity_id,
        entity_id,
    )

    cycle_count = _count_patterns(
        patterns,
        entity_id,
        "cycle",
    )

    temporal_cycle_count = _count_patterns(
        patterns,
        entity_id,
        "temporal_cycle",
    )

    return GraphFeatures(
        entity_id=entity_id,
        incoming_transaction_count=(incoming_transaction_count),
        outgoing_transaction_count=(outgoing_transaction_count),
        unique_senders=len(senders),
        unique_receivers=len(receivers),
        unique_counterparties=len(counterparties),
        incoming_amount_total=(incoming_amount_total),
        outgoing_amount_total=(outgoing_amount_total),
        average_incoming_amount=(average_incoming_amount),
        average_outgoing_amount=(average_outgoing_amount),
        net_flow=net_flow,
        self_transaction_count=(self_transaction_count),
        cycle_count=cycle_count,
        temporal_cycle_count=(temporal_cycle_count),
        fan_in_degree=len(senders),
        fan_out_degree=len(receivers),
    )


def extract_all_graph_features(
    graph: nx.MultiDiGraph,
    patterns: list[GraphPattern] | None = None,
) -> dict[str, GraphFeatures]:
    return {
        entity_id: extract_graph_features(
            graph,
            entity_id,
            patterns=patterns,
        )
        for entity_id in graph.nodes
    }
