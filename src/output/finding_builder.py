from __future__ import annotations
from typing import Any
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
    estate: EstateRepository
) -> SubmissionFinding:

    # 1. Check Authorization Requirement
    if gate_decision.target_hypothesis_id != target_hypothesis_id:
        raise ValueError("Gate decision target_hypothesis_id mismatch.")

    if gate_decision.outcome != "authorize_probable" or gate_decision.authorized_confidence != "probable":
        raise ValueError("Gate decision must be authorize_probable.")

    # 2. Check Scheme Scope
    hypothesis = next((h for h in case_state.hypotheses if h.hypothesis_id == target_hypothesis_id), None)
    if not hypothesis:
        raise ValueError("Target hypothesis not found in CaseState.")

    if hypothesis.scheme_type != "phantom_vendor":
        raise ValueError(f"FindingBuilder only supports phantom_vendor. Requested: {hypothesis.scheme_type}")

    # 3. Check Substantive Verification Requirement
    if verification_report.target_hypothesis_id != target_hypothesis_id:
        raise ValueError("VerificationReport target_hypothesis_id mismatch.")

    phantom_link_ok = False
    peso_ok = False

    for check in verification_report.checks:
        if check.critical and check.status == "verified":
            if check.check_id == "PHANTOM-EFOS-INVOICE-LINK":
                phantom_link_ok = True
            elif check.check_id == "PESO-RECONCILIATION":
                peso_ok = True

    if not phantom_link_ok:
        raise ValueError("Missing critical verified PHANTOM-EFOS-INVOICE-LINK check.")
    if not peso_ok:
        raise ValueError("Missing critical verified PESO-RECONCILIATION check.")

    # 4. Check Rule Requirement
    rule = rule_registry.get_rule(requested_rule_id)
    if not rule:
        raise ValueError(f"Rule {requested_rule_id} not found in registry.")
    if "phantom_vendor" not in rule.applies_to:
        raise ValueError(f"Rule {requested_rule_id} does not apply to phantom_vendor.")

    rule_broken = f"{rule.source} {rule.source_reference} - {rule.title}"

    # 5. Peso Amount
    if claimed_amount is None or not math.isfinite(claimed_amount) or claimed_amount <= 0:
        raise ValueError(f"claimed_amount must be a finite positive number. Got: {claimed_amount}")

    # 6. Exhibits
    unique_refs: dict[tuple[str, str], EvidenceRef] = {}
    for ref in verification_report.resolved_exhibits:
        key = (ref.source_table, str(ref.record_id))
        if key not in unique_refs:
            unique_refs[key] = ref

    if len(unique_refs) < 3:
        raise ValueError(f"Requires >= 3 unique exhibits, found {len(unique_refs)}.")

    exhibits = []
    for i, (key, ref) in enumerate(unique_refs.items(), 1):
        exhibits.append(SubmissionExhibit(
            exhibit_id=f"EX-{i:03d}",
            source_table=ref.source_table,
            record_id=str(ref.record_id),
            note=ref.note if ref.note and ref.note.strip() else f"Verified {ref.source_table} record {ref.record_id}"
        ))

    # 7. Entities (Strict attribution to the verified link)
    efos_rfcs = set()
    invoice_rfcs = set()

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
        raise ValueError("Cannot safely extract entity. No RFC intersection found between efos_list and invoices despite verified status.")

    # Sort deterministically
    entities = [f"RFC:{rfc}" for rfc in sorted(list(matched_rfcs))]

    # 8. Narrative
    narrative = (
        "The invoice issuer RFC matches an RFC present in the efos_list. "
        "The claimed peso amount reconciles deterministically to the cited records. "
        "The finding is authorized as probable by the evidence gate."
    )

    # Build Finding
    finding = SubmissionFinding(
        scheme_type="phantom_vendor",
        entities=entities,
        narrative=narrative,
        rule_broken=rule_broken,
        peso_amount=claimed_amount,
        money_trail=[],
        exhibits=exhibits,
        confidence="probable"
    )

    return finding
