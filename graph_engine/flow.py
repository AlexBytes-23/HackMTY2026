from datetime import timedelta
from itertools import product
from typing import Any

import networkx as nx

from graph_engine.contracts import GraphPattern
from graph_engine.evidence import build_graph_pattern


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

    transactions: list[dict[str, Any]] = []

    for key, data in edge_data.items():
        transaction = dict(data)

        transaction["edge_key"] = key
        transaction["source"] = source
        transaction["target"] = target

        transactions.append(transaction)

    return transactions


def _cycle_rotations(
    cycle: list[str],
) -> list[list[str]]:
    return [cycle[index:] + cycle[:index] for index in range(len(cycle))]


def find_temporal_cycles(
    graph: nx.MultiDiGraph,
    entity_id: str | None = None,
    max_length: int = 5,
    max_hours: float = 24,
) -> list[GraphPattern]:
    if entity_id is not None and entity_id not in graph:
        raise ValueError(f"Unknown entity_id: {entity_id}")

    if max_length < 2:
        raise ValueError("max_length must be at least 2")

    if max_hours <= 0:
        raise ValueError("max_hours must be greater than 0")

    structural_cycles = nx.simple_cycles(
        graph,
        length_bound=max_length,
    )

    results: list[GraphPattern] = []

    seen_transaction_sets: set[frozenset[str]] = set()

    max_duration = timedelta(hours=max_hours)

    for cycle in structural_cycles:
        if len(cycle) < 2:
            continue

        if entity_id is not None and entity_id not in cycle:
            continue

        for rotated_cycle in _cycle_rotations(cycle):
            edge_options: list[list[dict[str, Any]]] = []

            valid_cycle = True

            for index, source in enumerate(rotated_cycle):
                target = rotated_cycle[(index + 1) % len(rotated_cycle)]

                transactions = _get_edges_between(
                    graph,
                    source,
                    target,
                )

                if not transactions:
                    valid_cycle = False
                    break

                edge_options.append(transactions)

            if not valid_cycle:
                continue

            for combination in product(*edge_options):
                timestamps = [
                    transaction.get("timestamp") for transaction in combination
                ]

                if any(timestamp is None for timestamp in timestamps):
                    continue

                if timestamps != sorted(timestamps):
                    continue

                start_time = timestamps[0]
                end_time = timestamps[-1]

                if end_time - start_time > max_duration:
                    continue

                transaction_ids = [
                    transaction["transaction_id"] for transaction in combination
                ]

                transaction_set = frozenset(transaction_ids)

                if transaction_set in seen_transaction_sets:
                    continue

                seen_transaction_sets.add(transaction_set)

                results.append(
                    build_graph_pattern(
                        pattern_type="temporal_cycle",
                        entity_ids=rotated_cycle,
                        transactions=list(combination),
                        description=(
                            "Directed cycle formed by "
                            "chronologically ordered "
                            "transactions within "
                            f"{max_hours} hours."
                        ),
                    )
                )

    return results


def find_money_paths(
    graph: nx.MultiDiGraph,
    source_id: str,
    target_id: str | None = None,
    max_depth: int = 4,
) -> list[dict[str, Any]]:
    if source_id not in graph:
        raise ValueError(f"Unknown source entity: {source_id}")

    if target_id is not None and target_id not in graph:
        raise ValueError(f"Unknown target entity: {target_id}")

    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")

    simple_graph = nx.DiGraph(graph)

    if target_id is not None:
        targets = [target_id]
    else:
        targets = [node for node in simple_graph.nodes if node != source_id]

    results: list[dict[str, Any]] = []

    for target in targets:
        paths = nx.all_simple_paths(
            simple_graph,
            source=source_id,
            target=target,
            cutoff=max_depth,
        )

        for node_path in paths:
            hops: list[dict[str, Any]] = []

            for index in range(len(node_path) - 1):
                source = node_path[index]
                receiver = node_path[index + 1]

                transactions = _get_edges_between(
                    graph,
                    source,
                    receiver,
                )

                hops.append(
                    {
                        "source": source,
                        "target": receiver,
                        "transactions": transactions,
                    }
                )

            results.append(
                {
                    "source_entity_id": source_id,
                    "target_entity_id": target,
                    "entity_path": node_path,
                    "hop_count": len(node_path) - 1,
                    "hops": hops,
                }
            )

    return results
