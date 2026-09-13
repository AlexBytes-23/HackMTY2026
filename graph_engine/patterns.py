from typing import Any

import networkx as nx

from graph_engine.contracts import GraphPattern
from graph_engine.evidence import (
    build_graph_pattern,
)


def _get_edges_between(
    graph: nx.MultiDiGraph,
    source: str,
    target: str,
) -> list[dict[str, Any]]:
    edge_data = graph.get_edge_data(
        source,
        target,
        default={},
    )

    records: list[dict[str, Any]] = []

    for key, data in edge_data.items():
        record = dict(data)

        record["edge_key"] = key
        record["source"] = source
        record["target"] = target

        records.append(record)

    return records


def _get_total_amount(
    transactions: list[dict[str, Any]],
) -> float | None:
    currencies = {
        transaction.get("currency")
        for transaction in transactions
        if transaction.get("currency") is not None
    }

    if len(currencies) != 1:
        return None

    return sum(float(transaction["amount"]) for transaction in transactions)


def find_cycles(
    graph: nx.MultiDiGraph,
    entity_id: str | None = None,
    max_length: int = 5,
) -> list[GraphPattern]:
    if entity_id is not None and entity_id not in graph:
        raise ValueError(f"Unknown entity_id: {entity_id}")

    if max_length < 2:
        raise ValueError("max_length must be at least 2")

    patterns: list[GraphPattern] = []

    cycles = nx.simple_cycles(
        graph,
        length_bound=max_length,
    )

    for cycle in cycles:
        if len(cycle) < 2:
            continue

        if entity_id is not None and entity_id not in cycle:
            continue

        transactions: list[dict[str, Any]] = []

        for index, source in enumerate(cycle):
            target = cycle[(index + 1) % len(cycle)]

            transactions.extend(
                _get_edges_between(
                    graph,
                    source,
                    target,
                )
            )

        patterns.append(
            build_graph_pattern(
                pattern_type="cycle",
                entity_ids=cycle,
                transactions=transactions,
                description=("Directed transaction cycle detected."),
            )
        )

    return patterns


def find_fan_in(
    graph: nx.MultiDiGraph,
    entity_id: str | None = None,
    min_unique_senders: int = 3,
) -> list[GraphPattern]:
    if entity_id is not None and entity_id not in graph:
        raise ValueError(f"Unknown entity_id: {entity_id}")

    if min_unique_senders < 2:
        raise ValueError("min_unique_senders must be at least 2")

    nodes = [entity_id] if entity_id is not None else list(graph.nodes)

    patterns: list[GraphPattern] = []

    for target in nodes:
        senders = set(graph.predecessors(target))

        senders.discard(target)

        if len(senders) < min_unique_senders:
            continue

        transactions: list[dict[str, Any]] = []

        for sender in senders:
            transactions.extend(
                _get_edges_between(
                    graph,
                    sender,
                    target,
                )
            )

        total_amount = _get_total_amount(transactions)

        patterns.append(
            build_graph_pattern(
                pattern_type="fan_in",
                entity_ids=[
                    target,
                    *sorted(senders),
                ],
                transactions=transactions,
                total_amount=total_amount,
                description=(
                    f"{len(senders)} unique entities send transactions to {target}."
                ),
            )
        )

    return patterns


def find_fan_out(
    graph: nx.MultiDiGraph,
    entity_id: str | None = None,
    min_unique_receivers: int = 3,
) -> list[GraphPattern]:
    if entity_id is not None and entity_id not in graph:
        raise ValueError(f"Unknown entity_id: {entity_id}")

    if min_unique_receivers < 2:
        raise ValueError("min_unique_receivers must be at least 2")

    nodes = [entity_id] if entity_id is not None else list(graph.nodes)

    patterns: list[GraphPattern] = []

    for source in nodes:
        receivers = set(graph.successors(source))

        receivers.discard(source)

        if len(receivers) < min_unique_receivers:
            continue

        transactions: list[dict[str, Any]] = []

        for receiver in receivers:
            transactions.extend(
                _get_edges_between(
                    graph,
                    source,
                    receiver,
                )
            )

        total_amount = _get_total_amount(transactions)

        patterns.append(
            build_graph_pattern(
                pattern_type="fan_out",
                entity_ids=[
                    source,
                    *sorted(receivers),
                ],
                transactions=transactions,
                total_amount=total_amount,
                description=(
                    f"{source} sends transactions to {len(receivers)} unique entities."
                ),
            )
        )

    return patterns
