from __future__ import annotations

import math

from src.core.models import CaseState, EvidenceRef
from src.core.estate import EstateRepository
from src.gates.evidence_gate import EvidenceGateDecision
from src.output.formatters import with_entity_prefix
from src.verifier.official_verifier import (
    VerificationReport,
    assess_efos_status_and_timing,
)
from src.rules.rule_registry import RuleRegistry
from src.output.models import (MoneyTrailStep, SubmissionFinding,
                               SubmissionExhibit)


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

    SUPPORTED_SCHEMES = {
        "phantom_vendor": "PHANTOM-EFOS-INVOICE-LINK",
        "kickback": "KICKBACK-VENDOR-EMPLOYEE-LINK",
    }
    if hypothesis.scheme_type not in SUPPORTED_SCHEMES:
        raise ValueError(
            "FindingBuilder supports only "
            + ", ".join(sorted(SUPPORTED_SCHEMES))
            + f". Requested: {hypothesis.scheme_type}"
        )
    scheme = hypothesis.scheme_type
    required_check = SUPPORTED_SCHEMES[scheme]

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
            if check.check_id == required_check:
                phantom_link_ok = True
            elif check.check_id == "PESO-RECONCILIATION":
                peso_ok = True

    if not phantom_link_ok:
        raise ValueError(
            f"Missing critical verified {required_check} check."
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
    if scheme not in rule.applies_to:
        raise ValueError(
            f"Rule {requested_rule_id} does not apply to {scheme}."
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

    # 7. Entity attribution and the timing facts come from ONE deterministic
    # assessment -- the same one the verifier ran -- never from a second,
    # independently recomputed RFC intersection here.  Two computations of "which
    # RFCs qualify" is precisely what would let a verified check and an emitted
    # finding disagree about who is accused: an unfiltered intersection would name
    # a merely presumed vendor cited alongside a definitively listed one.
    if scheme == "phantom_vendor":

        assessment = assess_efos_status_and_timing(
            estate,
            [ref for ref in unique_refs.values() if ref.source_table == "efos_list"],
            [ref for ref in unique_refs.values() if ref.source_table == "invoices"],
        )

        if assessment.status != "verified" or not assessment.matched_rfcs:
            raise ValueError(
                "Cannot safely extract entity. The deterministic EFOS status/timing "
                "assessment does not hold for the cited records, so no RFC may be "
                f"named: {assessment.calculation}"
            )

        # On a verified assessment these name the same RFCs. If they ever diverge, the
        # assessment is not the single source of truth it is documented to be, and
        # refusing is the only safe response.
        if set(assessment.matched_rfcs) != set(assessment.publication_dates):
            raise ValueError(
                "Internal inconsistency: the verified EFOS assessment's matched RFCs "
                "and qualifying publication dates disagree, so entity attribution "
                "cannot be trusted."
            )

        matched_rfcs = sorted(assessment.matched_rfcs)
        entities = [f"RFC:{rfc}" for rfc in matched_rfcs]

        # 8. Narrative reports only the facts established by the deterministic checks.
        matched_text = ", ".join(matched_rfcs)

        published = sorted(set(assessment.publication_dates.values()))
        qualifying = assessment.qualifying_invoice_dates
        attributed = assessment.attributed_invoice_count

        # Ranges, not full lists: the official validator counts narrative words, and a
        # range cannot grow with the number of records.
        def _span(dates: list[str]) -> str:
            return dates[0] if len(dates) == 1 else f"between {dates[0]} and {dates[-1]}"

        # The timing check is existential over each issuer's own invoices -- one
        # qualifying invoice per matched RFC verifies it -- while claimed_amount sums
        # every cited invoice.  So the narrative states the count it actually
        # established and openly accounts for the rest.  Asserting that "every cited
        # invoice" postdates the listing would be falsifiable from this finding's own
        # exhibit table, which is worse than saying less.
        if len(qualifying) == attributed:
            timing_sentence = (
                "Those efos_list records carry a definitive listing status published "
                f"{_span(published)}, and all {attributed} cited invoices issued by "
                "those RFC(s) were issued on or after that publication date "
                f"({_span(qualifying)}). "
            )
        else:
            timing_sentence = (
                "Those efos_list records carry a definitive listing status published "
                f"{_span(published)}. {len(qualifying)} of the {attributed} cited "
                "invoices issued by those RFC(s) were issued on or after that "
                f"publication date ({_span(qualifying)}); the remaining "
                f"{attributed - len(qualifying)} do not postdate it and are still "
                "included in the amount below. "
            )

        narrative = (
            f"The cited invoice issuer RFC(s) {matched_text} also appear in the cited "
            "efos_list records. "
            + timing_sentence
            + f"The claimed amount of MXN {amount:,.2f} reconciles deterministically to "
            "the cited records. "
            "This finding is reported with probable confidence under the selected rule; "
            "these checks do not independently establish criminal intent."
        )

        # 9. Money trail.
        #
        # The trail is built ONLY from records the verifier already resolved, and it
        # states the one movement those exhibits actually establish: value passed
        # from the audited receiver to the listed issuer, under the cited CFDI.
        #
        # It is deliberately a single step rather than one step per invoice. The
        # official contract requires a CONNECTED trail (each step's destination is
        # the next step's source); a fan of parallel company-to-vendor payments does
        # not connect, and chaining them would draw a path the money never took.
        # Settlement legs are not added here because no bank_txns record is among
        # the authorised exhibits, and inventing an exhibit to draw a longer arrow
        # is exactly the failure this layer exists to prevent.
        money_trail: list[MoneyTrailStep] = []
        dated_invoices: list[tuple[str, SubmissionExhibit, dict]] = []
        for exhibit in exhibits:
            if exhibit.source_table != "invoices":
                continue
            record = estate.get_record("invoices", exhibit.record_id)
            if not record:
                continue
            issue_date = str(record.get("issue_date") or "").strip()
            if issue_date:
                dated_invoices.append((issue_date, exhibit, record))

        if dated_invoices:
            dated_invoices.sort(key=lambda item: (item[0], item[1].exhibit_id))
            issue_date, exhibit, record = dated_invoices[0]
            issuer = str(record.get("issuer_rfc") or "").strip()
            receiver = str(record.get("receiver_rfc") or "").strip()
            if issuer and receiver:
                money_trail = [
                    MoneyTrailStep(
                        **{
                            "from": f"RFC:{receiver}",
                            "to": f"RFC:{issuer}",
                            "amount": amount,
                            "date": issue_date,
                            "exhibit_id": exhibit.exhibit_id,
                        }
                    )
                ]

    else:
        # kickback: las entidades y el monto salen del MISMO flujo verificado,
        # no de una segunda deduccion. Si el claim no nombro a las partes, el
        # builder se niega en vez de inferirlas.
        kick = assess_kickback_link(estate, verification_report.resolved_exhibits)
        if kick.status != "verified" or not kick.vendor_rfcs or not kick.employee_ids:
            raise ValueError(
                "Cannot safely extract entities: the kickback link does not hold "
                f"for the cited records: {kick.calculation}"
            )

        entities = sorted(
            [f"RFC:{rfc}" for rfc in kick.vendor_rfcs]
            + [with_entity_prefix("EMP:", emp) for emp in kick.employee_ids]
        )

        narrative = (
            "The cited bank transfer(s) move MXN "
            + f"{amount:,.2f} from the vendor account to the personal account of "
            + "an employee who approves purchase orders for that same vendor. "
            + "Both accounts are cited and are distinct. "
            + "This is reported with probable confidence under the selected "
            + "internal control rule; the concurrence of the payment and the "
            + "approval is documentary, and these checks do not establish that "
            + "the payment caused the approval, nor any intent."
        )
    return SubmissionFinding(
        scheme_type=scheme,
        entities=entities,
        narrative=narrative,
        rule_broken=rule_broken,
        peso_amount=amount,
        money_trail=money_trail,
        exhibits=exhibits,
        confidence="probable",
    )
