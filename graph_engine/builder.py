from typing import Any

import networkx as nx


def build_graph(
    data: dict[str, Any],
) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()

    graph.graph["case_id"] = data["case_id"]
    graph.graph["target_entity_id"] = data.get("target_entity_id")

    entity_ids: set[str] = set()

    for entity in data["entities"]:
        entity_id = entity["entity_id"]

        if entity_id in entity_ids:
            raise ValueError(f"Duplicate entity_id: {entity_id}")

        entity_ids.add(entity_id)

        graph.add_node(
            entity_id,
            name=entity.get("name"),
            entity_type=entity.get("entity_type"),
            rfc=entity.get("rfc"),
            metadata=entity.get("metadata"),
        )

    target_entity_id = data.get("target_entity_id")

    if target_entity_id is not None and target_entity_id not in entity_ids:
        raise ValueError(f"Unknown target entity: {target_entity_id}")

    transaction_ids: set[str] = set()

    for transaction in data["transactions"]:
        transaction_id = transaction["transaction_id"]

        if transaction_id in transaction_ids:
            raise ValueError(f"Duplicate transaction_id: {transaction_id}")

        transaction_ids.add(transaction_id)

        sender_id = transaction["sender_entity_id"]

        receiver_id = transaction["receiver_entity_id"]

        if sender_id not in entity_ids:
            raise ValueError(f"Unknown sender entity: {sender_id}")

        if receiver_id not in entity_ids:
            raise ValueError(f"Unknown receiver entity: {receiver_id}")

        amount = transaction.get("amount")

        if amount is None:
            raise ValueError(f"Transaction {transaction_id} has no amount")

        if isinstance(amount, bool) or not isinstance(
            amount,
            (int, float),
        ):
            raise ValueError(  # noqa: TRY004
                f"Transaction {transaction_id} has an invalid amount"
            )

        graph.add_edge(
            sender_id,
            receiver_id,
            key=transaction_id,
            transaction_id=transaction_id,
            timestamp=transaction.get("timestamp"),
            amount=float(amount),
            currency=transaction.get("currency"),
            description=transaction.get("description"),
            source_ref=transaction.get("source_ref"),
        )

    return graph
