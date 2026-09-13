import re
from datetime import date
from typing import Literal, Any, Iterable
from pydantic import BaseModel, Field
from src.core.models import EvidenceRef, CaseState
from src.core.estate import EstateRepository
from src.output.reconciliation import reconcile_pesos

VerificationStatus = Literal["verified", "failed", "unresolved"]

# ``efos_list.status`` in the official estate schema takes exactly two lowercase
# values.  Only the DEFINITIVE listing supports the imputation of Articulo 69-B:
# a ``presunto`` row records a presumption the taxpayer may still rebut, so it is
# a real signal that has not matured, not an established fact.
EFOS_STATUS_DEFINITIVE = "definitivo"
EFOS_STATUS_PRESUMED = "presunto"

# ``efos_list.publication_date`` and ``invoices.issue_date`` are ISO-8601
# ``YYYY-MM-DD`` strings: fixed width, zero padded, most significant component
# first.  A plain lexicographic string comparison therefore orders them exactly
# like calendar dates, which is why this module compares them as strings.  Do not
# "fix" that into a datetime comparison; the regex below validates shape only.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalized_efos_status(value: Any) -> str:
    """Normalize an ``efos_list.status`` value for comparison.

    Lowercased and whitespace-stripped: the official enum is lowercase, but a
    judge's estate may not be, and a casing difference must never decide whether
    an accusation is authorized.  A non-string returns the empty string, which
    matches no qualifying status.
    """

    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _iso_date_or_none(value: Any) -> str | None:
    """Return ``value`` as an ISO-8601 ``YYYY-MM-DD`` string, or ``None``.

    Missing, empty, wrongly shaped and impossible calendar dates all return
    ``None``.  Callers turn that into ``unresolved`` -- never into ``verified``.
    """

    if not isinstance(value, str):
        return None

    text = value.strip()
    if not _ISO_DATE_RE.match(text):
        return None

    try:
        # Validity only (rejects 2025-02-30 and friends). The ordering comparison
        # itself stays lexicographic on the original strings.
        date.fromisoformat(text)
    except ValueError:
        return None

    return text


class EfosTimingAssessment(BaseModel):
    """Outcome of the deterministic EFOS status/timing assessment.

    ``status`` is the verification status for ``PHANTOM-EFOS-INVOICE-LINK``.  The
    remaining fields are the facts the case file and the narrative may quote, so
    that no consumer has to re-derive them by parsing ``calculation`` -- and, more
    importantly, so that no consumer computes its own answer to "which RFCs
    qualify".  Two independent computations of that question are exactly what let
    a verified check and an emitted finding disagree about who is accused.

    On a ``verified`` assessment ``matched_rfcs`` is the complete accusable set:
    every matched RFC is DEFINITIVE and carries a usable publication date, so
    ``matched_rfcs`` and ``publication_dates`` name the same RFCs.
    """

    status: VerificationStatus
    calculation: str
    matched_rfcs: list[str] = Field(default_factory=list)
    # rfc -> publication_date of its qualifying (definitive) efos_list row.
    publication_dates: dict[str, str] = Field(default_factory=dict)
    # Sorted issue_date of every cited invoice issued on/after its publication date.
    qualifying_invoice_dates: list[str] = Field(default_factory=list)
    # Sorted issue_date of every cited invoice from a matched issuer that PREDATES
    # its publication date.  These still belong to the claim, so the narrative has
    # to account for them instead of implying they are not there.
    prepublication_invoice_dates: list[str] = Field(default_factory=list)
    # Cited invoices from a matched issuer carrying no usable ISO-8601 issue_date.
    undated_invoice_count: int = 0

    @property
    def attributed_invoice_count(self) -> int:
        """How many cited invoices were issued by a matched (listed) RFC."""

        return (
            len(self.qualifying_invoice_dates)
            + len(self.prepublication_invoice_dates)
            + self.undated_invoice_count
        )


def assess_efos_status_and_timing(
    estate: EstateRepository,
    efos_refs: Iterable[EvidenceRef],
    invoice_refs: Iterable[EvidenceRef],
) -> EfosTimingAssessment:
    """Decide whether the cited EFOS/invoice records establish the phantom link.

    Three deterministic conditions, in order:

    1. An ``invoices.issuer_rfc`` matches an ``efos_list.rfc`` exactly.
    2. EVERY matched ``efos_list`` row carries the DEFINITIVE status and a usable
       ``publication_date``.
    3. EVERY matched RFC has at least one cited invoice issued on or after its own
       ``publication_date``.

    Conditions 2 and 3 are deliberately universal over the matched RFCs.  A
    finding's ``entities`` and its ``claimed_amount`` span every matched RFC, so
    authorizing on "at least one qualifies" lets a merely PRESUMED vendor -- or a
    listed one whose cited invoices all predate its listing -- be named, and its
    invoices counted, on the strength of a different vendor's record.  If a case
    bundles a qualifying and a non-qualifying vendor, the honest answer is to
    refuse the bundle and investigate them separately.

    Condition 3 stays existential over an individual issuer's own invoices: a
    definitively listed issuer's earlier invoices are still within the scope of
    the imputation, so one post-publication invoice establishes that the listing
    reaches this relationship.  It does NOT establish that every cited invoice
    postdates the listing, and callers must not say that it does --
    ``prepublication_invoice_dates`` records the ones that do not.

    Only all three together yield ``verified``.  Failing 2 or 3 yields
    ``unresolved``, not ``failed``: the listing is a real signal whose imputation
    is simply not established on the supplied records, and the subject must stay
    reportable as a lead rather than disappear from the output.
    """

    efos_records: list[tuple[str, dict]] = []
    invoice_records: list[tuple[str, dict]] = []

    efos_refs = list(efos_refs)
    invoice_refs = list(invoice_refs)

    if not efos_refs or not invoice_refs:
        return EfosTimingAssessment(
            status="unresolved",
            calculation="Missing resolved evidence for either efos_list or invoices.",
        )

    missing_fields = False

    for ref in efos_refs:
        rec = estate.get_record(ref.source_table, str(ref.record_id))
        if rec and "rfc" in rec:
            val = rec["rfc"]
            if isinstance(val, str) and val.strip():
                efos_records.append((val, rec))
            else:
                missing_fields = True
        else:
            missing_fields = True

    for ref in invoice_refs:
        rec = estate.get_record(ref.source_table, str(ref.record_id))
        if rec and "issuer_rfc" in rec:
            val = rec["issuer_rfc"]
            if isinstance(val, str) and val.strip():
                invoice_records.append((val, rec))
            else:
                missing_fields = True
        else:
            missing_fields = True

    efos_rfcs = {rfc for rfc, _ in efos_records}
    invoice_issuer_rfcs = {rfc for rfc, _ in invoice_records}

    if missing_fields or not efos_rfcs or not invoice_issuer_rfcs:
        return EfosTimingAssessment(
            status="unresolved",
            calculation=(
                "Cannot safely extract required valid, non-empty RFC strings from "
                "all provided exhibits."
            ),
        )

    matched = sorted(efos_rfcs.intersection(invoice_issuer_rfcs))

    if not matched:
        return EfosTimingAssessment(
            status="failed",
            calculation=(
                "No match. EFOS RFCs: "
                + ", ".join(sorted(efos_rfcs))
                + ". Invoice issuer_rfcs: "
                + ", ".join(sorted(invoice_issuer_rfcs))
                + "."
            ),
        )

    match_text = "Match found for RFC(s): " + ", ".join(matched) + "."

    # --- Condition 2: EVERY matched RFC must be DEFINITIVE and dated ---------
    #
    # Universal, not existential. ``entities`` and ``claimed_amount`` cover every
    # matched RFC, so one qualifying vendor must never carry a non-qualifying one
    # into a finding alongside it.
    observed_statuses: dict[str, str] = {}
    publication_dates: dict[str, str] = {}
    non_definitive_rfcs: list[str] = []
    undated_rfcs: list[str] = []

    for rfc, rec in sorted(efos_records, key=lambda item: item[0]):
        if rfc not in matched:
            continue

        normalized = _normalized_efos_status(rec.get("status"))
        observed_statuses[rfc] = normalized or "<missing>"
        if normalized != EFOS_STATUS_DEFINITIVE:
            if rfc not in non_definitive_rfcs:
                non_definitive_rfcs.append(rfc)
            continue

        published = _iso_date_or_none(rec.get("publication_date"))
        if published is None:
            if rfc not in undated_rfcs:
                undated_rfcs.append(rfc)
            continue

        # ``efos_list.rfc`` is the primary key, so at most one row per RFC is
        # expected. Should an estate supply more, keep the LATEST publication
        # date: that is the conservative choice, since it is the hardest for an
        # invoice to postdate.
        current = publication_dates.get(rfc)
        if current is None or published > current:
            publication_dates[rfc] = published

    observed_text = ", ".join(
        f"{rfc}={observed_statuses[rfc]}" for rfc in sorted(observed_statuses)
    )

    if non_definitive_rfcs:
        return EfosTimingAssessment(
            status="unresolved",
            calculation=(
                f"{match_text} Not every matched efos_list record carries status "
                f"'{EFOS_STATUS_DEFINITIVE}': "
                + ", ".join(sorted(non_definitive_rfcs))
                + f" does not. Observed efos_list.status: {observed_text or '<none>'}. "
                f"A '{EFOS_STATUS_PRESUMED}' or absent status records a presumption "
                "that has not matured, and a qualifying issuer cited alongside it "
                "cannot establish it; the imputation is not established on the "
                "supplied records, and it is not disproved either."
            ),
            matched_rfcs=matched,
        )

    if undated_rfcs:
        return EfosTimingAssessment(
            status="unresolved",
            calculation=(
                f"{match_text} Status '{EFOS_STATUS_DEFINITIVE}' is present for "
                "every matched RFC, but "
                + ", ".join(sorted(undated_rfcs))
                + " carries no usable ISO-8601 (YYYY-MM-DD) publication_date, so "
                "the invoices cannot be placed relative to the listing."
            ),
            matched_rfcs=matched,
        )

    # --- Condition 3: EVERY matched RFC needs an invoice on/after publication -
    #
    # Existential over an issuer's own invoices -- one post-publication invoice
    # shows the listing reaches this relationship -- but universal over the
    # issuers, for the same reason condition 2 is: every matched RFC is named in
    # ``entities``, so none may ride in on another issuer's timing.
    qualifying_by_rfc: dict[str, list[str]] = {rfc: [] for rfc in publication_dates}
    prepublication_dates: list[str] = []
    undated_invoices = 0

    for rfc, rec in invoice_records:
        published = publication_dates.get(rfc)
        if published is None:
            # Issued by an RFC that is not in the matched set: not attributable to
            # any listing, so it is not counted either way.
            continue
        issued = _iso_date_or_none(rec.get("issue_date"))
        if issued is None:
            undated_invoices += 1
            continue
        # ISO-8601 YYYY-MM-DD: lexicographic >= is calendar >=. See _ISO_DATE_RE.
        if issued >= published:
            qualifying_by_rfc[rfc].append(issued)
        else:
            prepublication_dates.append(issued)

    publication_text = ", ".join(
        f"{rfc}={publication_dates[rfc]}" for rfc in sorted(publication_dates)
    )
    prepublication_sorted = sorted(prepublication_dates)
    qualifying_dates = [
        issued for dates in qualifying_by_rfc.values() for issued in dates
    ]
    unreached_rfcs = sorted(
        rfc for rfc, dates in qualifying_by_rfc.items() if not dates
    )

    if unreached_rfcs:
        detail = (
            "Cited invoice issue_date(s) predating publication: "
            + (", ".join(prepublication_sorted) or "<none usable>")
            + "."
        )
        if undated_invoices:
            detail += (
                f" {undated_invoices} cited invoice(s) carry no usable "
                "ISO-8601 issue_date."
            )
        return EfosTimingAssessment(
            status="unresolved",
            calculation=(
                f"{match_text} Status '{EFOS_STATUS_DEFINITIVE}' published "
                f"{publication_text}, but no cited invoice issued by "
                + ", ".join(unreached_rfcs)
                + " was issued on or after that RFC's publication_date. "
                f"{detail} The listing therefore does not reach these invoices on "
                "the supplied records; that is not established, not disproved."
            ),
            matched_rfcs=matched,
            publication_dates=publication_dates,
            prepublication_invoice_dates=prepublication_sorted,
            undated_invoice_count=undated_invoices,
        )

    qualifying_sorted = sorted(qualifying_dates)
    attributed = len(qualifying_sorted) + len(prepublication_sorted) + undated_invoices

    calculation = (
        f"{match_text} efos_list.status='{EFOS_STATUS_DEFINITIVE}' for every "
        "matched RFC; publication_date: "
        f"{publication_text}. {len(qualifying_sorted)} of {attributed} cited "
        "invoice(s) from those issuers were issued on or after publication: "
        + ", ".join(qualifying_sorted)
        + "."
    )
    if prepublication_sorted:
        # Stated explicitly: these invoices are inside claimed_amount, and no
        # consumer of this check may imply that they are not there.
        calculation += (
            f" {len(prepublication_sorted)} PREDATE publication: "
            + ", ".join(prepublication_sorted)
            + "."
        )
    if undated_invoices:
        calculation += (
            f" {undated_invoices} carry no usable ISO-8601 issue_date."
        )

    return EfosTimingAssessment(
        status="verified",
        calculation=calculation,
        matched_rfcs=matched,
        publication_dates=publication_dates,
        qualifying_invoice_dates=qualifying_sorted,
        prepublication_invoice_dates=prepublication_sorted,
        undated_invoice_count=undated_invoices,
    )


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

            assessment = assess_efos_status_and_timing(
                estate, efos_exhibits, invoice_exhibits
            )

            report.checks.append(VerificationCheck(
                check_id="PHANTOM-EFOS-INVOICE-LINK",
                statement=(
                    "An invoice issuer_rfc exactly matches an efos_list RFC; every "
                    f"matched RFC carries status '{EFOS_STATUS_DEFINITIVE}' with a "
                    "usable publication_date; and every matched RFC has at least "
                    "one cited invoice issued on or after that publication_date."
                ),
                status=assessment.status,
                calculation=assessment.calculation,
                critical=True
            ))
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
