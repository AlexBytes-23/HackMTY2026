from __future__ import annotations

import math

from src.core.models import CaseState, EvidenceRef
from src.core.estate import EstateRepository
from src.gates.evidence_gate import EvidenceGateDecision
from src.verifier.official_verifier import VerificationReport
from src.rules.rule_registry import RuleRegistry
from src.output.models import SubmissionFinding, SubmissionExhibit


def build_finding(
    case_state: CaseState,
    target_hypothesis_id: str,
    gate_decision: EvidenceGateDecision,
    verification_report: VerificationReport,
    rule_registry: RuleRegistry,
    requested_rule_id: str,
    claimed_amount: float | None,
    estate: EstateRepository,
) -> SubmissionFinding:
    """Build one official finding only from an already-authorized target.

    This builder does not investigate, repair evidence, infer an amount, or
    upgrade confidence.  It performs defensive consistency checks and then
    translates verified/authorized state into the official output model.
    """

    # 1. Authorization must refer to this exact target.
    if gate_decision.target_hypothesis_id != target_hypothesis_id:
        raise ValueError("Gate decision target_hypothesis_id mismatch.")

    if (
        gate_decision.outcome != "authorize_probable"
        or gate_decision.authorized_confidence != "probable"
    ):
        raise ValueError("Gate decision must be authorize_probable.")

    # 2. Scope is intentionally narrow.
    hypothesis = next(
        (
            h
            for h in case_state.hypotheses
            if h.hypothesis_id == target_hypothesis_id
        ),
        None,
    )
    if not hypothesis:
        raise ValueError("Target hypothesis not found in CaseState.")

    if hypothesis.scheme_type != "phantom_vendor":
        raise ValueError(
            "FindingBuilder only supports phantom_vendor. "
            f"Requested: {hypothesis.scheme_type}"
        )

    # 3. Verification report must be internally coherent and closed.
    if verification_report.target_hypothesis_id != target_hypothesis_id:
        raise ValueError("VerificationReport target_hypothesis_id mismatch.")

    if verification_report.internal_errors:
        raise ValueError(
            "VerificationReport contains internal errors and cannot produce a finding."
        )
    if verification_report.critical_failures:
        raise ValueError(
            "VerificationReport contains critical failures and cannot produce a finding."
        )
    if verification_report.unresolved_critical_checks:
        raise ValueError(
            "VerificationReport contains unresolved critical checks and cannot produce a finding."
        )

    phantom_link_ok = False
    peso_ok = False
    for check in verification_report.checks:
        if check.critical and check.status == "verified":
            if check.check_id == "PHANTOM-EFOS-INVOICE-LINK":
                phantom_link_ok = True
            elif check.check_id == "PESO-RECONCILIATION":
                peso_ok = True

    if not phantom_link_ok:
        raise ValueError(
            "Missing critical verified PHANTOM-EFOS-INVOICE-LINK check."
        )
    if not peso_ok:
        raise ValueError(
            "Missing critical verified PESO-RECONCILIATION check."
        )

    if (
        not verification_report.reconciliation
        or verification_report.reconciliation.get("reconciles") is not True
    ):
        raise ValueError(
            "VerificationReport does not contain a successful deterministic peso reconciliation."
        )

    # 4. The selected formal rule must exist and apply to this scheme.
    rule = rule_registry.get_rule(requested_rule_id)
    if not rule:
        raise ValueError(f"Rule {requested_rule_id} not found in registry.")
    if "phantom_vendor" not in rule.applies_to:
        raise ValueError(
            f"Rule {requested_rule_id} does not apply to phantom_vendor."
        )

    rule_broken = f"{rule.source} {rule.source_reference} - {rule.title}"

    # 5. The amount is an upstream claim, never derived here.
    if isinstance(claimed_amount, bool):
        raise ValueError(
            f"claimed_amount must be a finite positive number. Got: {claimed_amount}"
        )
    if (
        claimed_amount is None
        or not math.isfinite(float(claimed_amount))
        or float(claimed_amount) <= 0
    ):
        raise ValueError(
            f"claimed_amount must be a finite positive number. Got: {claimed_amount}"
        )
    amount = float(claimed_amount)

    # 6. Exhibits come only from records already resolved by the verifier.
    unique_refs: dict[tuple[str, str], EvidenceRef] = {}
    for ref in verification_report.resolved_exhibits:
        key = (ref.source_table, str(ref.record_id))
        if key not in unique_refs:
            unique_refs[key] = ref

    if len(unique_refs) < 3:
        raise ValueError(
            f"Requires >= 3 unique exhibits, found {len(unique_refs)}."
        )

    exhibits: list[SubmissionExhibit] = []
    for i, key in enumerate(sorted(unique_refs), start=1):
        ref = unique_refs[key]
        note = (
            ref.note.strip()
            if ref.note and ref.note.strip()
            else (
                f"Resolved {ref.source_table} record {ref.record_id}; "
                "record existence verified."
            )
        )
        exhibits.append(
            SubmissionExhibit(
                exhibit_id=f"EX-{i:03d}",
                source_table=ref.source_table,
                record_id=str(ref.record_id),
                note=note,
            )
        )

    # 7. Entity attribution is limited to RFCs participating in the same factual
    # EFOS/invoice relation that the current verifier checks.  This reconstruction
    # is temporary debt until VerificationCheck.evidence carries the exact matched
    # records directly.
    efos_rfcs: set[str] = set()
    invoice_rfcs: set[str] = set()

    for ref in unique_refs.values():
        if ref.source_table == "efos_list":
            rec = estate.get_record(ref.source_table, str(ref.record_id))
            if rec and "rfc" in rec:
                val = rec["rfc"]
                if isinstance(val, str) and val.strip():
                    efos_rfcs.add(val.strip())
        elif ref.source_table == "invoices":
            rec = estate.get_record(ref.source_table, str(ref.record_id))
            if rec and "issuer_rfc" in rec:
                val = rec["issuer_rfc"]
                if isinstance(val, str) and val.strip():
                    invoice_rfcs.add(val.strip())

    matched_rfcs = efos_rfcs.intersection(invoice_rfcs)
    if not matched_rfcs:
        raise ValueError(
            "Cannot safely extract entity. No RFC intersection found between "
            "efos_list and invoices despite verified status."
        )

    entities = [f"RFC:{rfc}" for rfc in sorted(matched_rfcs)]

    # 8. Narrative reports only the facts established by the deterministic checks.
    matched_text = ", ".join(sorted(matched_rfcs))
    narrative = (
        f"The cited invoice issuer RFC(s) {matched_text} also appear in the cited "
        "efos_list records. "
        f"The claimed amount of MXN {amount:,.2f} reconciles deterministically to "
        "the cited records. "
        "This finding is reported with probable confidence under the selected rule; "
        "these checks do not independently establish criminal intent."
    )

    return SubmissionFinding(
        scheme_type="phantom_vendor",
        entities=entities,
        narrative=narrative,
        rule_broken=rule_broken,
        peso_amount=amount,
        money_trail=[],
        exhibits=exhibits,
        confidence="probable",
    )
