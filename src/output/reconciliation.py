from typing import Sequence, Any
from src.core.models import EvidenceRef
from src.core.estate import EstateRepository

PESO_TOLERANCE = 0.02

def reconcile_pesos(
    estate: EstateRepository,
    claimed_amount: float,
    exhibits: Sequence[EvidenceRef]
) -> dict[str, Any]:
    """
    Deterministic reusable utility matching the official validator's peso reconciliation.
    Sums amounts PER TABLE and matches against the closest table's total.

    NOTE: Peso reconciliation alone does NOT validate exhibit existence in full, nor does
    it validate that the finding passes all official validations. It solely computes the
    amount logic.
    """
    per_table: dict[str, float] = {}
    missing_records: list[str] = []

    for ex in exhibits:
        amt = estate.get_record_amount(ex.source_table, ex.record_id)
        if amt is not None:
            per_table[ex.source_table] = per_table.get(ex.source_table, 0.0) + float(amt)
        else:
            if not estate.record_exists(ex.source_table, ex.record_id):
                missing_records.append(f"{ex.source_table}.{ex.record_id}")

    if not per_table:
        return {
            "claimed_peso_amount": claimed_amount,
            "per_table_sums": {},
            "best_matching_table": None,
            "difference": None,
            "relative_difference": None,
            "tolerance_boundary": None,
            "reconciles": False,
            "missing_records": missing_records,
            "error": "No exhibit cites an amount-bearing table."
        }

    best_table = min(per_table.keys(), key=lambda t: abs(claimed_amount - per_table[t]))
    best_amt = per_table[best_table]

    diff = abs(claimed_amount - best_amt)
    tolerance_boundary = PESO_TOLERANCE * max(best_amt, 1.0)
    reconciles = diff <= tolerance_boundary

    return {
        "claimed_peso_amount": claimed_amount,
        "per_table_sums": per_table,
        "best_matching_table": best_table,
        "difference": diff,
        "relative_difference": diff / best_amt if best_amt > 0 else diff,
        "tolerance_boundary": tolerance_boundary,
        "reconciles": reconciles,
        "missing_records": missing_records,
        "error": None if reconciles else "Amount does not reconcile within 2% tolerance."
    }
