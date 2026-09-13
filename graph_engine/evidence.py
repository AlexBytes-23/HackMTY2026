from typing import Any

from graph_engine.contracts import (
    EvidenceRef,
    GraphPattern,
)


def get_time_bounds(
    transactions: list[dict[str, Any]],
) -> tuple[Any | None, Any | None]:
    timestamps = [
        transaction["timestamp"]
        for transaction in transactions
        if transaction.get("timestamp") is not None
    ]

    if not timestamps:
        return None, None

    return min(timestamps), max(timestamps)


def get_evidence_refs(
    transactions: list[dict[str, Any]],
) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    seen: set[tuple] = set()

    for transaction in transactions:
        source_ref = transaction.get("source_ref")

        if source_ref is None:
            continue

        ref = EvidenceRef.model_validate(source_ref)

        identity = (
            ref.file,
            ref.row,
            ref.xpath,
        )

        if identity in seen:
            continue

        seen.add(identity)
        refs.append(ref)

    return refs


def build_graph_pattern(
    pattern_type: str,
    entity_ids: list[str],
    transactions: list[dict[str, Any]],
    description: str,
    total_amount: float | None = None,
) -> GraphPattern:
    transaction_ids: list[str] = []
    transaction_amounts: dict[str, float] = {}

    seen_transactions: set[str] = set()

    for transaction in transactions:
        transaction_id = transaction["transaction_id"]

        if transaction_id in seen_transactions:
            continue

        seen_transactions.add(transaction_id)

        transaction_ids.append(transaction_id)

        amount = transaction.get("amount")

        if amount is not None:
            transaction_amounts[transaction_id] = float(amount)

    start_time, end_time = get_time_bounds(transactions)

    evidence_refs = get_evidence_refs(transactions)

    return GraphPattern(
        pattern_type=pattern_type,
        entity_ids=entity_ids,
        transaction_ids=transaction_ids,
        transaction_amounts=transaction_amounts,
        total_amount=total_amount,
        start_time=start_time,
        end_time=end_time,
        evidence_refs=evidence_refs,
        description=description,
    )
