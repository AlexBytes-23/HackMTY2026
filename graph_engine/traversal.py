import networkx as nx

VALID_DIRECTIONS = {"in", "out", "both"}


def get_neighbors(
    graph: nx.MultiDiGraph,
    entity_id: str,
    depth: int = 1,
    direction: str = "both",
) -> set[str]:
    if entity_id not in graph:
        raise ValueError(f"Unknown entity_id: {entity_id}")

    if depth < 1:
        raise ValueError("depth must be at least 1")

    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {VALID_DIRECTIONS}")

    visited = {entity_id}
    frontier = {entity_id}

    for _ in range(depth):
        next_frontier: set[str] = set()

        for node in frontier:
            if direction == "out":
                neighbors = set(graph.successors(node))

            elif direction == "in":
                neighbors = set(graph.predecessors(node))

            else:
                neighbors = set(graph.successors(node)) | set(graph.predecessors(node))

            next_frontier.update(neighbors - visited)

        visited.update(next_frontier)
        frontier = next_frontier

        if not frontier:
            break

    visited.remove(entity_id)

    return visited


def get_subgraph(
    graph: nx.MultiDiGraph,
    entity_id: str,
    depth: int = 1,
    direction: str = "both",
) -> nx.MultiDiGraph:
    neighbors = get_neighbors(
        graph,
        entity_id,
        depth=depth,
        direction=direction,
    )

    nodes = neighbors | {entity_id}

    return graph.subgraph(nodes).copy()
