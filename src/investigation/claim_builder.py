"""Deterministic producer of the monetary claim a finding asserts.

``claimed_amount`` is an input to the Verifier and the Evidence Gate, never an
output of them.  This module exists so the runtime has a *legitimate* producer for
it instead of leaving the caller to invent a number.

The distinction that matters:

- Forbidden: searching the cited exhibits for whichever per-table total happens to
  reconcile best.  That is reverse-engineering an amount from the answer key of the
  reconciliation check, and it would make the check vacuous.
- Done here: applying one scheme-defined scope rule, fixed in advance, to the
  records the hypothesis already cites, and recording the formula so a reader can
  recompute it by hand.

For ``phantom_vendor`` the scope rule is: the value of the invoices that the
hypothesis cites and that were issued by an RFC the hypothesis also cites in
``efos_list``.  No other table is consulted, and no alternative scope is tried if
the first one fails to reconcile.

If the cited exhibit set is ambiguous, this module returns no amount and says why.
An honest refusal is preferable to a number the Verifier will reject with a
confusing message.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from src.core.estate import EstateRepository
from src.core.models import CaseState, EvidenceRef


class AmountClaim(BaseModel):
    """Auditable monetary claim, or an explicit refusal to state one."""

    target_hypothesis_id: str
    scheme_type: str | None = None

    claimed_amount: float | None = None

    scope_rule: str
    formula: str | None = None

    source_table: str | None = None
    contributing_record_ids: list[str] = Field(default_factory=list)
    matched_entities: list[str] = Field(default_factory=list)

    excluded_record_ids: list[str] = Field(default_factory=list)

    errors: list[str] = Field(default_factory=list)


PHANTOM_VENDOR_SCOPE_RULE = (
    "sum(invoices.total) over the invoices cited by this hypothesis whose "
    "issuer_rfc appears in the efos_list records cited by this same hypothesis"
)


def _cited_refs(
    case_state: CaseState,
    target_hypothesis_id: str,
) -> list[EvidenceRef]:
    """Collect the exhibit refs the hypothesis cites, in stable order.

    Deliberately identical in scope to what ``OfficialVerifier.verify`` gathers, so
    the claim and the check describe the same exhibit set.
    """

    hypothesis = next(
        (
            h
            for h in case_state.hypotheses
            if h.hypothesis_id == target_hypothesis_id
        ),
        None,
    )
    if hypothesis is None:
        return []

    refs: list[EvidenceRef] = []
    seen: set[tuple[str, str]] = set()

    for case_evidence in case_state.evidence:
        if case_evidence.evidence_id not in hypothesis.supporting_evidence_ids:
            continue
        for ref in case_evidence.source_refs:
            key = (ref.source_table, str(ref.record_id))
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)

    return refs


def build_phantom_vendor_claim(
    case_state: CaseState,
    target_hypothesis_id: str,
    estate: EstateRepository,
) -> AmountClaim:
    """Compute the phantom_vendor monetary claim from the hypothesis' own exhibits."""

    claim = AmountClaim(
        target_hypothesis_id=target_hypothesis_id,
        scope_rule=PHANTOM_VENDOR_SCOPE_RULE,
        source_table="invoices",
    )

    hypothesis = next(
        (
            h
            for h in case_state.hypotheses
            if h.hypothesis_id == target_hypothesis_id
        ),
        None,
    )
    if hypothesis is None:
        claim.errors.append(
            f"Hypothesis {target_hypothesis_id!r} does not exist in the CaseState."
        )
        return claim

    claim.scheme_type = hypothesis.scheme_type

    if hypothesis.scheme_type != "phantom_vendor":
        claim.errors.append(
            "This scope rule only applies to phantom_vendor. Requested scheme_type: "
            f"{hypothesis.scheme_type}."
        )
        return claim

    refs = _cited_refs(case_state, target_hypothesis_id)

    # 1. The RFCs the hypothesis actually cites as listed in efos_list.
    efos_rfcs: set[str] = set()
    for ref in refs:
        if ref.source_table != "efos_list":
            continue
        record = estate.get_record("efos_list", str(ref.record_id))
        if not record:
            continue
        value = record.get("rfc")
        if isinstance(value, str) and value.strip():
            efos_rfcs.add(value.strip())

    if not efos_rfcs:
        claim.errors.append(
            "No cited efos_list record yields a usable RFC, so the phantom_vendor "
            "scope is undefined."
        )
        return claim

    claim.matched_entities = sorted(f"RFC:{rfc}" for rfc in efos_rfcs)

    # 2. The cited invoices issued by one of those RFCs.
    in_scope: list[tuple[str, float]] = []
    out_of_scope: list[str] = []

    for ref in refs:
        if ref.source_table != "invoices":
            continue

        record_id = str(ref.record_id)
        record = estate.get_record("invoices", record_id)
        if not record:
            out_of_scope.append(record_id)
            continue

        issuer = record.get("issuer_rfc")
        issuer = issuer.strip() if isinstance(issuer, str) else None

        if issuer not in efos_rfcs:
            out_of_scope.append(record_id)
            continue

        amount = estate.get_record_amount("invoices", record_id)
        if amount is None:
            claim.errors.append(
                f"Cited invoice {record_id} has no usable total, so the claim "
                "cannot be stated."
            )
            return claim

        in_scope.append((record_id, float(amount)))

    claim.excluded_record_ids = sorted(out_of_scope)

    if not in_scope:
        claim.errors.append(
            "No cited invoice was issued by a cited efos_list RFC, so there is no "
            "phantom_vendor amount to claim."
        )
        return claim

    # 3. A mixed exhibit set makes the scope ambiguous. The official reconciliation
    #    sums EVERY cited invoice, so an amount covering only part of them would be
    #    rejected anyway; say so plainly instead of emitting a number that fails.
    if out_of_scope:
        claim.errors.append(
            "The cited exhibits mix invoices from issuers outside the cited EFOS "
            f"RFCs ({', '.join(claim.excluded_record_ids)}), so a scope-defined "
            "amount cannot be stated for this exhibit set."
        )
        return claim

    in_scope.sort()
    total = math.fsum(amount for _, amount in in_scope)

    if not math.isfinite(total) or total <= 0:
        claim.errors.append(
            f"The computed scope total is not a usable positive amount: {total!r}."
        )
        return claim

    claim.contributing_record_ids = [record_id for record_id, _ in in_scope]
    claim.claimed_amount = total
    claim.formula = (
        "sum(invoices.total) over ["
        + ", ".join(
            f"{record_id}={amount:.2f}" for record_id, amount in in_scope
        )
        + f"] issued by {', '.join(sorted(efos_rfcs))} = {total:.2f}"
    )

    return claim
