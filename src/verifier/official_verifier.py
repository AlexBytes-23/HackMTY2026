from typing import Literal, Any
from pydantic import BaseModel, Field
from src.core.models import EvidenceRef, CaseState
from src.core.estate import EstateRepository
from src.output.reconciliation import reconcile_pesos

VerificationStatus = Literal["verified", "failed", "unresolved"]

class VerificationCheck(BaseModel):
    check_id: str
    statement: str
    status: VerificationStatus
    evidence: list[EvidenceRef] = Field(default_factory=list)
    calculation: str | None = None
    critical: bool = True

class VerificationReport(BaseModel):
    target_hypothesis_id: str
    checks: list[VerificationCheck] = Field(default_factory=list)
    resolved_exhibits: list[EvidenceRef] = Field(default_factory=list)
    missing_exhibits: list[EvidenceRef] = Field(default_factory=list)
    duplicate_exhibits: list[EvidenceRef] = Field(default_factory=list)
    reconciliation: dict[str, Any] | None = None
    critical_failures: list[str] = Field(default_factory=list)
    unresolved_critical_checks: list[str] = Field(default_factory=list)
    internal_errors: list[str] = Field(default_factory=list)

class OfficialVerifier:
    def verify(
        self,
        case_state: CaseState,
        target_hypothesis_id: str,
        estate: EstateRepository,
        claimed_amount: float | None = None
    ) -> VerificationReport:

        hypothesis = next((h for h in case_state.hypotheses if h.hypothesis_id == target_hypothesis_id), None)
        if not hypothesis:
            return VerificationReport(
                target_hypothesis_id=target_hypothesis_id,
                internal_errors=["Target hypothesis does not exist in CaseState."]
            )

        report = VerificationReport(target_hypothesis_id=target_hypothesis_id)

        # 1. Gather exhibits from the hypothesis supporting evidence
        proposed_exhibits = []
        for case_ev in case_state.evidence:
            if case_ev.evidence_id in hypothesis.supporting_evidence_ids:
                proposed_exhibits.extend(case_ev.source_refs)

        # 2. Check existence & duplicates
        seen = set()
        exhibit_checks = []
        for ref in proposed_exhibits:
            key = (ref.source_table, str(ref.record_id))
            if key in seen:
                report.duplicate_exhibits.append(ref)
                continue

            seen.add(key)

            exists = estate.record_exists(ref.source_table, str(ref.record_id))
            if exists:
                report.resolved_exhibits.append(ref)
                exhibit_checks.append(VerificationCheck(
                    check_id=f"EXISTS-{ref.source_table}-{ref.record_id}",
                    statement=f"Record {ref.record_id} exists in {ref.source_table}",
                    status="verified",
                    evidence=[ref],
                    critical=True
                ))
            else:
                report.missing_exhibits.append(ref)
                exhibit_checks.append(VerificationCheck(
                    check_id=f"EXISTS-{ref.source_table}-{ref.record_id}",
                    statement=f"Record {ref.record_id} exists in {ref.source_table}",
                    status="failed",
                    evidence=[ref],
                    critical=True
                ))

        report.checks.extend(exhibit_checks)

        # 3. Substantive Scheme Verification
        if hypothesis.scheme_type == "phantom_vendor":
            efos_exhibits = [ref for ref in report.resolved_exhibits if ref.source_table == "efos_list"]
            invoice_exhibits = [ref for ref in report.resolved_exhibits if ref.source_table == "invoices"]

            check = VerificationCheck(
                check_id="PHANTOM-EFOS-INVOICE-LINK",
                statement="An invoice issuer_rfc exactly matches an efos_list RFC.",
                status="unresolved",
                critical=True
            )

            if not efos_exhibits or not invoice_exhibits:
                check.status = "unresolved"
                check.calculation = "Missing resolved evidence for either efos_list or invoices."
            else:
                missing_fields = False

                # Extract EFOS RFCs
                efos_rfcs = set()
                for ref in efos_exhibits:
                    rec = estate.get_record(ref.source_table, str(ref.record_id))
                    if rec and "rfc" in rec:
                        val = rec["rfc"]
                        if isinstance(val, str) and val.strip():
                            efos_rfcs.add(val)
                        else:
                            missing_fields = True
                    else:
                        missing_fields = True

                # Extract Invoice issuer_rfcs
                invoice_issuer_rfcs = set()
                for ref in invoice_exhibits:
                    rec = estate.get_record(ref.source_table, str(ref.record_id))
                    if rec and "issuer_rfc" in rec:
                        val = rec["issuer_rfc"]
                        if isinstance(val, str) and val.strip():
                            invoice_issuer_rfcs.add(val)
                        else:
                            missing_fields = True
                    else:
                        missing_fields = True

                if missing_fields or not efos_rfcs or not invoice_issuer_rfcs:
                    check.status = "unresolved"
                    check.calculation = "Cannot safely extract required valid, non-empty RFC strings from all provided exhibits."
                else:
                    # Deterministic check
                    intersection = efos_rfcs.intersection(invoice_issuer_rfcs)
                    if intersection:
                        check.status = "verified"
                        check.calculation = f"Match found for RFC(s): {', '.join(intersection)}"
                    else:
                        check.status = "failed"
                        check.calculation = f"No match. EFOS RFCs: {efos_rfcs}. Invoice issuer_rfcs: {invoice_issuer_rfcs}."

            report.checks.append(check)
        else:
            # Fallback for unsupported schemes
            report.checks.append(VerificationCheck(
                check_id="SCHEME-SUBSTANTIVE-VERIFICATION",
                statement=f"Substantive verification for scheme: {hypothesis.scheme_type}",
                status="unresolved",
                calculation=f"No deterministic substantive verifier implemented for scheme_type {hypothesis.scheme_type}.",
                critical=True
            ))


        # 4. Peso Reconciliation
        if claimed_amount is None:
            # DO NOT infer one from exhibits. Mark as unresolved.
            report.checks.append(VerificationCheck(
                check_id="PESO-RECONCILIATION",
                statement="Peso amount reconciles deterministically.",
                status="unresolved",
                calculation="No claimed_amount provided to Verifier.",
                critical=True
            ))
        else:
            report.reconciliation = reconcile_pesos(estate, claimed_amount, report.resolved_exhibits)
            if not report.reconciliation.get("reconciles", False):
                report.checks.append(VerificationCheck(
                    check_id="PESO-RECONCILIATION",
                    statement="Peso amount reconciles deterministically.",
                    status="failed",
                    calculation=str(report.reconciliation),
                    critical=True
                ))
            else:
                report.checks.append(VerificationCheck(
                    check_id="PESO-RECONCILIATION",
                    statement="Peso amount reconciles deterministically.",
                    status="verified",
                    calculation=str(report.reconciliation),
                    critical=True
                ))

        # Collect critical failures and unresolved checks
        report.critical_failures = [c.check_id for c in report.checks if c.critical and c.status == "failed"]
        report.unresolved_critical_checks = [c.check_id for c in report.checks if c.critical and c.status == "unresolved"]

        return report
