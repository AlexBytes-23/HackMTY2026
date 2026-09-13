"""EFOS status and timing must gate a phantom_vendor accusation.

``PHANTOM-EFOS-INVOICE-LINK`` used to be a bare set intersection of
``efos_list.rfc`` against ``invoices.issuer_rfc``.  That authorized an accusation
against any vendor the SAT merely *presumes* (``status='presunto'``), and against
any vendor whose invoices all predate the listing's ``publication_date`` — two
independent ways to accuse an innocent party.

The two headline fixtures here are rebuilt from a measured false accusation on a
held-out adversarial estate, not invented:

* ``GAL160126E3F`` — an innocent decoy.  ``presunto``, published 2025-12-12, five
  invoices issued 2024-02-14..2024-10-15, every one covered by an approved PO
  under a 2023 contract.  It fails on status AND on timing, independently, and
  must be blocked.
* ``BER2407128JB`` — the true positive.  ``definitivo``, published 2025-03-14,
  seven invoices issued 2025-04-09..2025-09-18, all after publication, no
  contract.  It must still authorize.

The estates are built here as fixtures on purpose: a test must not read the
held-out estate file.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.agents.challenger import ChallengerReview
from src.agents.method_critic import MethodCriticReview
from src.core.estate import ID_COLUMN, OFFICIAL_COLUMNS, EstateRepository
from src.core.models import CaseEvidence, CaseState, EvidenceRef, Hypothesis
from src.gates.evidence_gate import EvidenceGateDecision, evaluate_gate
from src.investigation.claim_builder import build_phantom_vendor_claim
from src.llm.runtime import InstrumentedLLMClient
from src.output.finding_builder import build_finding
from src.rules.default_rules import (
    RULE_CFF_69B,
    build_default_registry,
    default_rule_id_for_scheme,
)
from src.run_audit import main as run_audit_main
from src.verifier.official_verifier import (
    EFOS_STATUS_DEFINITIVE,
    EFOS_STATUS_PRESUMED,
    OfficialVerifier,
    VerificationCheck,
    VerificationReport,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFIER_SOURCE = REPO_ROOT / "src" / "verifier" / "official_verifier.py"

# The innocent decoy and the true positive, exactly as the held-out estate has them.
INNOCENT_RFC = "GAL160126E3F"
INNOCENT_PUBLICATION = "2025-12-12"
INNOCENT_INVOICES = [
    ("INV-G01", "2024-02-14", "501352.00"),
    ("INV-G02", "2024-04-22", "501352.00"),
    ("INV-G03", "2024-06-28", "501352.00"),
    ("INV-G04", "2024-08-19", "501352.00"),
    ("INV-G05", "2024-10-15", "501352.00"),
]
INNOCENT_TOTAL = 2_506_760.00

GUILTY_RFC = "BER2407128JB"
GUILTY_PUBLICATION = "2025-03-14"
GUILTY_INVOICES = [
    ("INV-B01", "2025-04-09", "400000.00"),
    ("INV-B02", "2025-05-12", "399000.00"),
    ("INV-B03", "2025-06-03", "401000.00"),
    ("INV-B04", "2025-07-11", "398600.00"),
    ("INV-B05", "2025-08-01", "400500.00"),
    ("INV-B06", "2025-08-27", "399500.00"),
    ("INV-B07", "2025-09-18", "397000.00"),
]
GUILTY_TOTAL = 2_795_600.00

COMPANY_RFC = "ACME010101AA1"


# ==========================================================================
# ESTATE FIXTURES
# ==========================================================================

def _create_tables(conn: sqlite3.Connection) -> None:
    for table, columns in OFFICIAL_COLUMNS.items():
        id_column = ID_COLUMN[table]
        rest = ",".join(
            f'"{column}"' for column in sorted(columns) if column != id_column
        )
        conn.execute(
            f'CREATE TABLE {table} ("{id_column}" TEXT PRIMARY KEY'
            + (f",{rest}" if rest else "")
            + ")"
        )


def _insert_innocent_vendor(conn: sqlite3.Connection) -> None:
    """The decoy: presumed only, listed long after every invoice, fully documented."""

    conn.execute(
        "INSERT INTO efos_list (rfc, legal_name, status, publication_date) "
        "VALUES (?, ?, ?, ?)",
        (INNOCENT_RFC, "Galvanizados del Norte SA de CV", EFOS_STATUS_PRESUMED,
         INNOCENT_PUBLICATION),
    )
    conn.execute(
        "INSERT INTO vendors (rfc, legal_name, category) VALUES (?, ?, ?)",
        (INNOCENT_RFC, "Galvanizados del Norte SA de CV", "insumos"),
    )
    conn.execute(
        "INSERT INTO contracts (contract_id, vendor_rfc, start_date, value) "
        "VALUES (?, ?, ?, ?)",
        ("CTR-G01", INNOCENT_RFC, "2023-01-15", "3000000.00"),
    )
    for index, (uuid_value, issue_date, total) in enumerate(INNOCENT_INVOICES, start=1):
        conn.execute(
            "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid_value, INNOCENT_RFC, COMPANY_RFC, total, issue_date),
        )
        conn.execute(
            "INSERT INTO purchase_orders "
            '(po_id, vendor_rfc, "date", amount, requester, approver, description) '
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"PO-G{index:02d}", INNOCENT_RFC, issue_date, total,
             "A. Ejemplo", "B. Ejemplo", "Suministro de insumos bajo CTR-G01"),
        )


def _insert_guilty_vendor(conn: sqlite3.Connection) -> None:
    """The true positive: definitively listed, every invoice after publication."""

    conn.execute(
        "INSERT INTO efos_list (rfc, legal_name, status, publication_date) "
        "VALUES (?, ?, ?, ?)",
        (GUILTY_RFC, "Bernal Servicios Integrales SA de CV",
         EFOS_STATUS_DEFINITIVE, GUILTY_PUBLICATION),
    )
    conn.execute(
        "INSERT INTO vendors (rfc, legal_name, category) VALUES (?, ?, ?)",
        (GUILTY_RFC, "Bernal Servicios Integrales SA de CV", "servicios"),
    )
    for uuid_value, issue_date, total in GUILTY_INVOICES:
        conn.execute(
            "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid_value, GUILTY_RFC, COMPANY_RFC, total, issue_date),
        )


def _build_estate(db_path: Path, *, innocent: bool = True, guilty: bool = True) -> None:
    conn = sqlite3.connect(db_path)
    _create_tables(conn)
    if innocent:
        _insert_innocent_vendor(conn)
    if guilty:
        _insert_guilty_vendor(conn)
    conn.commit()
    conn.close()


@pytest.fixture
def estate(tmp_path):
    db_path = tmp_path / "adversarial_estate.db"
    _build_estate(db_path)
    repository = EstateRepository(db_path)
    yield repository
    repository.close()


# ==========================================================================
# DETERMINISTIC PATH HELPERS
# ==========================================================================

def _case_state_for(rfc: str, invoices: list[tuple[str, str, str]]) -> CaseState:
    target = f"H-{rfc}"
    evidence = [
        CaseEvidence(
            evidence_id="ev-efos",
            statement=(
                f"RFC {rfc} appears in the efos_list table supplied with the estate."
            ),
            direction="for",
            produced_by="test",
            source_refs=[
                EvidenceRef(
                    source_table="efos_list",
                    record_id=rfc,
                    note="EFOS row for the issuer RFC.",
                )
            ],
        )
    ]
    for index, (uuid_value, _issue_date, _total) in enumerate(invoices, start=1):
        evidence.append(
            CaseEvidence(
                evidence_id=f"ev-inv-{index:03d}",
                statement=f"Invoice {uuid_value} was issued by {rfc}.",
                direction="for",
                produced_by="test",
                source_refs=[
                    EvidenceRef(
                        source_table="invoices",
                        record_id=uuid_value,
                        note="CFDI issued by the listed RFC.",
                    )
                ],
            )
        )

    return CaseState(
        case_id=f"case-{rfc}",
        lead_id=f"lead-{rfc}",
        status="ready_for_verification",
        hypotheses=[
            Hypothesis(
                hypothesis_id=target,
                statement=f"RFC {rfc} issued CFDI to the audited company.",
                scheme_type="phantom_vendor",
                status="supported",
                supporting_evidence_ids=[e.evidence_id for e in evidence],
            )
        ],
        evidence=evidence,
    )


def _run_deterministic_path(estate: EstateRepository, rfc, invoices):
    """Drive claim_builder -> OfficialVerifier -> EvidenceGate for one vendor.

    Only the two adversarial reviews are synthesised; every monetary and
    documentary decision below is deterministic Python.
    """

    case_state = _case_state_for(rfc, invoices)
    target = case_state.hypotheses[0].hypothesis_id
    registry = build_default_registry()
    rule_id = default_rule_id_for_scheme("phantom_vendor")

    claim = build_phantom_vendor_claim(case_state, target, estate)
    report = OfficialVerifier().verify(
        case_state, target, estate, claimed_amount=claim.claimed_amount
    )
    decision = evaluate_gate(
        case_state=case_state,
        target_hypothesis_id=target,
        challenger_review=ChallengerReview(
            target_hypothesis_id=target,
            outcome="survives",
            reasoning_summary="Synthesised: this test measures the deterministic path.",
            survives_challenge=True,
        ),
        method_critic_review=MethodCriticReview(
            target_hypothesis_id=target,
            outcome="clear",
            reasoning_summary="Synthesised for the same reason.",
        ),
        verification_report=report,
        rule_registry=registry,
        requested_rule_id=rule_id,
    )
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    return case_state, claim, report, decision, check, registry, rule_id


# ==========================================================================
# THE MODULE-LEVEL CONSTANT
# ==========================================================================

def test_qualifying_status_is_a_named_module_level_constant():
    # The official estate schema enumerates exactly these two lowercase values.
    assert EFOS_STATUS_DEFINITIVE == "definitivo"
    assert EFOS_STATUS_PRESUMED == "presunto"

    # Resolved from this file, not from the cwd: a cwd-relative path would make
    # the assertion silently vacuous when pytest runs from another directory.
    source = VERIFIER_SOURCE.read_text(encoding="utf-8")
    assert f'EFOS_STATUS_DEFINITIVE = "{EFOS_STATUS_DEFINITIVE}"' in source
    assert f'EFOS_STATUS_PRESUMED = "{EFOS_STATUS_PRESUMED}"' in source


# ==========================================================================
# THE MEASURED FALSE ACCUSATION
# ==========================================================================

def test_presumed_decoy_listed_after_its_invoices_is_blocked(estate):
    _, claim, report, decision, check, _, _ = _run_deterministic_path(
        estate, INNOCENT_RFC, INNOCENT_INVOICES
    )

    # The amount that used to be accused. The scope rule still computes it; the
    # verifier is what now refuses to stand behind it.
    assert claim.claimed_amount == pytest.approx(INNOCENT_TOTAL)

    assert check.status == "unresolved"
    # Not established, never disproved: the subject must stay reportable as a lead.
    assert "PHANTOM-EFOS-INVOICE-LINK" in report.unresolved_critical_checks
    assert "PHANTOM-EFOS-INVOICE-LINK" not in report.critical_failures

    assert decision.outcome != "authorize_probable"
    assert decision.authorized_confidence is None
    assert any(
        "Critical check unresolved: PHANTOM-EFOS-INVOICE-LINK" in requirement
        for requirement in decision.failed_requirements
    )

    # The calculation must be quotable by the case file.
    assert EFOS_STATUS_PRESUMED in check.calculation
    assert INNOCENT_RFC in check.calculation


def test_true_positive_listed_before_its_invoices_still_authorizes(estate):
    case_state, claim, report, decision, check, registry, rule_id = (
        _run_deterministic_path(estate, GUILTY_RFC, GUILTY_INVOICES)
    )

    assert claim.claimed_amount == pytest.approx(GUILTY_TOTAL)
    assert check.status == "verified"
    assert decision.outcome == "authorize_probable"
    assert decision.authorized_confidence == "probable"

    assert GUILTY_PUBLICATION in check.calculation
    assert EFOS_STATUS_DEFINITIVE in check.calculation
    assert "2025-04-09" in check.calculation

    finding = build_finding(
        case_state=case_state,
        target_hypothesis_id=case_state.hypotheses[0].hypothesis_id,
        gate_decision=decision,
        verification_report=report,
        rule_registry=registry,
        requested_rule_id=rule_id,
        claimed_amount=claim.claimed_amount,
        estate=estate,
    )

    assert finding.entities == [f"RFC:{GUILTY_RFC}"]
    assert finding.peso_amount == pytest.approx(GUILTY_TOTAL)


def test_the_two_vendors_are_decided_differently_on_the_same_estate(estate):
    """A fix that blocks both, or authorizes both, is a failure."""

    _, _, _, innocent_decision, _, _, _ = _run_deterministic_path(
        estate, INNOCENT_RFC, INNOCENT_INVOICES
    )
    _, _, _, guilty_decision, _, _, _ = _run_deterministic_path(
        estate, GUILTY_RFC, GUILTY_INVOICES
    )

    assert innocent_decision.outcome != "authorize_probable"
    assert guilty_decision.outcome == "authorize_probable"


# ==========================================================================
# THE SUB-CONDITIONS, ONE AT A TIME
# ==========================================================================

class _MockEstate(EstateRepository):
    """Minimal estate seam: one efos_list row and one invoice row."""

    def __init__(self, efos: dict, invoice: dict):
        self.db_path = ":memory:"
        self._efos = efos
        self._invoice = invoice

    def record_exists(self, table: str, record_id: str) -> bool:
        return True

    def get_record(self, table: str, record_id: str) -> dict | None:
        if table == "efos_list":
            return dict(self._efos)
        if table == "invoices":
            return dict(self._invoice)
        return {}

    def get_record_amount(self, table: str, record_id: str) -> float | None:
        return 100.0


def _check_for(efos: dict, invoice: dict):
    state = CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                statement="",
                scheme_type="phantom_vendor",
                supporting_evidence_ids=["ev1"],
            )
        ],
        evidence=[
            CaseEvidence(
                evidence_id="ev1",
                statement="",
                direction="for",
                produced_by="test",
                source_refs=[
                    EvidenceRef(source_table="efos_list", record_id="E1"),
                    EvidenceRef(source_table="invoices", record_id="I1"),
                ],
            )
        ],
    )
    report = OfficialVerifier().verify(
        state, "h1", _MockEstate(efos, invoice), claimed_amount=100.0
    )
    return next(
        c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK"
    )


def _efos(**overrides) -> dict:
    record = {
        "rfc": "TST010101AA1",
        "status": EFOS_STATUS_DEFINITIVE,
        "publication_date": "2025-03-14",
    }
    record.update(overrides)
    return record


def _invoice(**overrides) -> dict:
    record = {"issuer_rfc": "TST010101AA1", "issue_date": "2025-04-09"}
    record.update(overrides)
    return record


def test_definitive_status_and_invoice_after_publication_is_verified():
    check = _check_for(_efos(), _invoice())
    assert check.status == "verified"
    assert "2025-03-14" in check.calculation
    assert "2025-04-09" in check.calculation


def test_invoice_issued_exactly_on_the_publication_date_is_verified():
    # "on or after": the boundary day counts, so the comparison is >=, not >.
    check = _check_for(_efos(), _invoice(issue_date="2025-03-14"))
    assert check.status == "verified"


def test_presumed_status_alone_is_unresolved_not_failed():
    check = _check_for(_efos(status=EFOS_STATUS_PRESUMED), _invoice())
    assert check.status == "unresolved"
    assert EFOS_STATUS_PRESUMED in check.calculation


def test_definitive_status_with_every_invoice_predating_publication_is_unresolved():
    check = _check_for(_efos(), _invoice(issue_date="2025-03-13"))
    assert check.status == "unresolved"
    assert "2025-03-13" in check.calculation


def test_missing_publication_date_is_unresolved():
    for value in (None, "", "   "):
        check = _check_for(_efos(publication_date=value), _invoice())
        assert check.status == "unresolved", value


def test_unparseable_publication_date_is_unresolved():
    for value in ("12/12/2025", "2025-13-45", "2025-02-30", "2025-3-1", 20250314):
        check = _check_for(_efos(publication_date=value), _invoice())
        assert check.status == "unresolved", value


def test_missing_status_is_unresolved():
    for value in (None, "", "   ", "vigente", 1):
        check = _check_for(_efos(status=value), _invoice())
        assert check.status == "unresolved", value


def test_status_comparison_is_case_insensitive_and_whitespace_tolerant():
    for value in ("Definitivo", " DEFINITIVO ", "\tdefinitivo\n"):
        check = _check_for(_efos(status=value), _invoice())
        assert check.status == "verified", value


def test_missing_or_unparseable_invoice_issue_date_is_unresolved():
    for value in (None, "", "2025/04/09", "09-04-2025"):
        check = _check_for(_efos(), _invoice(issue_date=value))
        assert check.status == "unresolved", value


def test_absent_publication_date_column_is_unresolved():
    efos = _efos()
    del efos["publication_date"]
    assert _check_for(efos, _invoice()).status == "unresolved"


def test_absent_status_column_is_unresolved():
    efos = _efos()
    del efos["status"]
    assert _check_for(efos, _invoice()).status == "unresolved"


# ==========================================================================
# REGRESSION: BUNDLING A PRESUNTO VENDOR WITH A DEFINITIVO ONE
# ==========================================================================
#
# The status condition is UNIVERSAL over the matched RFCs, not existential. A
# finding's entities and its claimed_amount span every matched RFC, so "at least
# one is definitivo" would let a merely presumed vendor be named -- and its
# invoices counted -- on the strength of a different vendor's listing. That is
# the same false accusation this module exists to prevent, reached by bundling.

MIXED_DEF_RFC = "DEF010101AA1"
MIXED_PRE_RFC = "PRE010101AA1"


@pytest.fixture
def bundled_estate(tmp_path):
    """One definitivo vendor and one presunto vendor, cited by the same case."""

    db_path = tmp_path / "bundled.db"
    conn = sqlite3.connect(db_path)
    _create_tables(conn)
    conn.execute(
        "INSERT INTO efos_list (rfc, status, publication_date) VALUES (?, ?, ?)",
        (MIXED_DEF_RFC, EFOS_STATUS_DEFINITIVE, "2025-03-14"),
    )
    conn.execute(
        "INSERT INTO efos_list (rfc, status, publication_date) VALUES (?, ?, ?)",
        (MIXED_PRE_RFC, EFOS_STATUS_PRESUMED, "2025-12-12"),
    )
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
        "VALUES (?, ?, ?, ?, ?)",
        ("INV-D01", MIXED_DEF_RFC, COMPANY_RFC, "100000.00", "2025-04-09"),
    )
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
        "VALUES (?, ?, ?, ?, ?)",
        ("INV-P01", MIXED_PRE_RFC, COMPANY_RFC, "900000.00", "2025-01-20"),
    )
    conn.commit()
    conn.close()

    repository = EstateRepository(db_path)
    yield repository
    repository.close()


def _bundled_case() -> CaseState:
    target = "H-bundled"
    evidence = [
        CaseEvidence(
            evidence_id="ev-efos-def",
            statement=f"RFC {MIXED_DEF_RFC} appears in efos_list.",
            direction="for",
            produced_by="test",
            source_refs=[EvidenceRef(source_table="efos_list",
                                     record_id=MIXED_DEF_RFC, note="EFOS row.")],
        ),
        CaseEvidence(
            evidence_id="ev-efos-pre",
            statement=f"RFC {MIXED_PRE_RFC} appears in efos_list.",
            direction="for",
            produced_by="test",
            source_refs=[EvidenceRef(source_table="efos_list",
                                     record_id=MIXED_PRE_RFC, note="EFOS row.")],
        ),
        CaseEvidence(
            evidence_id="ev-inv-def",
            statement="Invoice INV-D01 exists.",
            direction="for",
            produced_by="test",
            source_refs=[EvidenceRef(source_table="invoices",
                                     record_id="INV-D01", note="CFDI.")],
        ),
        CaseEvidence(
            evidence_id="ev-inv-pre",
            statement="Invoice INV-P01 exists.",
            direction="for",
            produced_by="test",
            source_refs=[EvidenceRef(source_table="invoices",
                                     record_id="INV-P01", note="CFDI.")],
        ),
    ]
    return CaseState(
        case_id="case-bundled",
        lead_id="lead-bundled",
        status="ready_for_verification",
        hypotheses=[
            Hypothesis(
                hypothesis_id=target,
                statement="Two listed issuers invoiced the audited company.",
                scheme_type="phantom_vendor",
                status="supported",
                supporting_evidence_ids=[e.evidence_id for e in evidence],
            )
        ],
        evidence=evidence,
    )


def test_bundling_a_presunto_vendor_does_not_authorize(bundled_estate):
    case_state = _bundled_case()
    target = case_state.hypotheses[0].hypothesis_id
    registry = build_default_registry()
    rule_id = default_rule_id_for_scheme("phantom_vendor")

    claim = build_phantom_vendor_claim(case_state, target, bundled_estate)
    report = OfficialVerifier().verify(
        case_state, target, bundled_estate, claimed_amount=claim.claimed_amount
    )
    check = next(
        c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK"
    )

    assert check.status == "unresolved"
    assert MIXED_PRE_RFC in check.calculation

    decision = evaluate_gate(
        case_state=case_state,
        target_hypothesis_id=target,
        challenger_review=ChallengerReview(
            target_hypothesis_id=target, outcome="survives",
            reasoning_summary="Synthesised.", survives_challenge=True),
        method_critic_review=MethodCriticReview(
            target_hypothesis_id=target, outcome="clear",
            reasoning_summary="Synthesised."),
        verification_report=report,
        rule_registry=registry,
        requested_rule_id=rule_id,
    )
    assert decision.outcome != "authorize_probable"


def test_builder_never_names_a_presunto_rfc_even_on_a_forged_verified_report(
    bundled_estate,
):
    """Defense in depth: the builder must not trust a report it did not produce.

    This is the property, not the symptom: whatever upstream claims, no RFC may
    reach ``entities`` unless the deterministic assessment qualifies it.
    """

    case_state = _bundled_case()
    target = case_state.hypotheses[0].hypothesis_id
    registry = build_default_registry()

    forged = VerificationReport(
        target_hypothesis_id=target,
        checks=[
            VerificationCheck(check_id="PESO-RECONCILIATION", statement="",
                              status="verified", critical=True),
            VerificationCheck(check_id="PHANTOM-EFOS-INVOICE-LINK", statement="",
                              status="verified", critical=True),
        ],
        resolved_exhibits=[
            EvidenceRef(source_table="efos_list", record_id=MIXED_DEF_RFC),
            EvidenceRef(source_table="efos_list", record_id=MIXED_PRE_RFC),
            EvidenceRef(source_table="invoices", record_id="INV-D01"),
            EvidenceRef(source_table="invoices", record_id="INV-P01"),
        ],
        reconciliation={"reconciles": True},
    )
    decision = EvidenceGateDecision(
        target_hypothesis_id=target,
        outcome="authorize_probable",
        authorized_confidence="probable",
        reason="forged for this test",
    )

    with pytest.raises(ValueError, match="Cannot safely extract entity"):
        build_finding(
            case_state=case_state,
            target_hypothesis_id=target,
            gate_decision=decision,
            verification_report=forged,
            rule_registry=registry,
            requested_rule_id=default_rule_id_for_scheme("phantom_vendor"),
            claimed_amount=1_000_000.00,
            estate=bundled_estate,
        )


def test_entities_are_never_recomputed_from_a_raw_rfc_intersection():
    """The builder must consume the assessment, not re-derive who qualifies.

    Two independent computations of "which RFCs qualify" is what let a verified
    check and an emitted finding disagree in the first place.
    """

    source = (REPO_ROOT / "src" / "output" / "finding_builder.py").read_text(
        encoding="utf-8"
    )
    assert "assessment.matched_rfcs" in source
    assert ".intersection(" not in source


# ==========================================================================
# REGRESSION: THE NARRATIVE MAY NOT OVERSTATE THE TIMING CHECK
# ==========================================================================
#
# The timing condition is EXISTENTIAL (one qualifying invoice verifies it) while
# claimed_amount sums EVERY cited invoice. A narrative asserting that every cited
# invoice postdates the listing would therefore be falsifiable from the finding's
# own exhibit table.

MIXED_TIMING_RFC = "MIX010101AA1"
MIXED_TIMING_INVOICES = [
    ("INV-T01", "2024-01-10", "1000000.00"),   # predates publication
    ("INV-T02", "2024-06-10", "1000000.00"),   # predates publication
    ("INV-T03", "2025-04-09", "500000.00"),    # postdates publication
]
MIXED_TIMING_PUBLICATION = "2025-03-14"


@pytest.fixture
def mixed_timing_estate(tmp_path):
    db_path = tmp_path / "mixed_timing.db"
    conn = sqlite3.connect(db_path)
    _create_tables(conn)
    conn.execute(
        "INSERT INTO efos_list (rfc, status, publication_date) VALUES (?, ?, ?)",
        (MIXED_TIMING_RFC, EFOS_STATUS_DEFINITIVE, MIXED_TIMING_PUBLICATION),
    )
    for uuid_value, issue_date, total in MIXED_TIMING_INVOICES:
        conn.execute(
            "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid_value, MIXED_TIMING_RFC, COMPANY_RFC, total, issue_date),
        )
    conn.commit()
    conn.close()

    repository = EstateRepository(db_path)
    yield repository
    repository.close()


def _finding_for(estate: EstateRepository, rfc, invoices):
    case_state, claim, report, decision, _, registry, rule_id = (
        _run_deterministic_path(estate, rfc, invoices)
    )
    assert decision.outcome == "authorize_probable"
    return claim, build_finding(
        case_state=case_state,
        target_hypothesis_id=case_state.hypotheses[0].hypothesis_id,
        gate_decision=decision,
        verification_report=report,
        rule_registry=registry,
        requested_rule_id=rule_id,
        claimed_amount=claim.claimed_amount,
        estate=estate,
    )


def _assert_narrative_matches_the_exhibits(finding, estate, publication):
    """The property: the narrative's timing claim must hold for the cited invoices.

    Recomputed from the finding's own exhibit table -- exactly what a judge would
    do to falsify it.
    """

    issue_dates = []
    for exhibit in finding.exhibits:
        if exhibit.source_table != "invoices":
            continue
        record = estate.get_record("invoices", exhibit.record_id)
        issue_dates.append(record["issue_date"])

    postdating = [d for d in issue_dates if d >= publication]
    narrative = finding.narrative

    if len(postdating) == len(issue_dates):
        assert f"all {len(issue_dates)} cited invoices" in narrative
    else:
        # A universal claim would be falsifiable from the exhibits above.
        assert "all " not in narrative
        assert f"{len(postdating)} of the {len(issue_dates)} cited invoices" in narrative
        assert str(len(issue_dates) - len(postdating)) in narrative
        assert "included in the amount" in narrative
    return issue_dates, postdating


def test_narrative_does_not_claim_every_invoice_postdates_publication(
    mixed_timing_estate,
):
    claim, finding = _finding_for(
        mixed_timing_estate, MIXED_TIMING_RFC, MIXED_TIMING_INVOICES
    )

    # The claim still covers every cited invoice, including the two that predate
    # the listing: narrowing it would break the per-table peso reconciliation.
    assert claim.claimed_amount == pytest.approx(2_500_000.00)
    assert finding.peso_amount == pytest.approx(2_500_000.00)

    issue_dates, postdating = _assert_narrative_matches_the_exhibits(
        finding, mixed_timing_estate, MIXED_TIMING_PUBLICATION
    )
    assert len(issue_dates) == 3
    assert len(postdating) == 1
    assert len(finding.narrative.split()) <= 150


def test_narrative_may_say_all_only_when_every_cited_invoice_postdates(estate):
    _claim, finding = _finding_for(estate, GUILTY_RFC, GUILTY_INVOICES)
    _assert_narrative_matches_the_exhibits(finding, estate, GUILTY_PUBLICATION)
    assert f"all {len(GUILTY_INVOICES)} cited invoices" in finding.narrative


def test_check_calculation_reports_the_prepublication_invoices(mixed_timing_estate):
    _, _, _, _, check, _, _ = _run_deterministic_path(
        mixed_timing_estate, MIXED_TIMING_RFC, MIXED_TIMING_INVOICES
    )
    assert check.status == "verified"
    assert "1 of 3 cited invoice(s)" in check.calculation
    assert "2 PREDATE publication" in check.calculation
    assert "2024-01-10" in check.calculation


# ==========================================================================
# NARRATIVE
# ==========================================================================

def test_narrative_states_the_timing_relationship(estate):
    case_state, claim, report, decision, _, registry, rule_id = (
        _run_deterministic_path(estate, GUILTY_RFC, GUILTY_INVOICES)
    )
    finding = build_finding(
        case_state=case_state,
        target_hypothesis_id=case_state.hypotheses[0].hypothesis_id,
        gate_decision=decision,
        verification_report=report,
        rule_registry=registry,
        requested_rule_id=rule_id,
        claimed_amount=claim.claimed_amount,
        estate=estate,
    )

    lower = finding.narrative.lower()
    assert "definitive" in lower
    assert GUILTY_PUBLICATION in finding.narrative
    assert "on or after that publication date" in lower
    # The official validator counts words and rejects anything over 150.
    assert len(finding.narrative.split()) <= 150


def test_narrative_stays_under_150_words_for_a_wide_multi_rfc_finding(tmp_path):
    """Worst realistic case: many listed issuers, many invoices, mixed timing.

    Mixed timing on purpose: it selects the LONGER narrative branch (the one that
    must account for the invoices predating publication), so this measures the
    real ceiling and not the short branch.
    """

    db_path = tmp_path / "wide.db"
    conn = sqlite3.connect(db_path)
    _create_tables(conn)

    rfcs = [f"WID01010{index}AA{index}" for index in range(1, 6)]
    invoices: list[tuple[str, str, str]] = []
    for r_index, rfc in enumerate(rfcs, start=1):
        conn.execute(
            "INSERT INTO efos_list (rfc, legal_name, status, publication_date) "
            "VALUES (?, ?, ?, ?)",
            (rfc, f"Wide Vendor {r_index}", EFOS_STATUS_DEFINITIVE,
             f"2025-0{r_index}-01"),
        )
        for i_index in range(1, 9):
            uuid_value = f"INV-W{r_index}{i_index:02d}"
            # Half predate the 2025 publication dates, half postdate them, so every
            # RFC has a qualifying invoice AND the narrative must account for the
            # rest -- the longer of the two branches.
            issue_date = (
                f"2024-0{i_index}-{i_index:02d}"
                if i_index % 2
                else f"2025-1{i_index % 2}-{i_index:02d}"
            )
            conn.execute(
                "INSERT INTO invoices "
                "(uuid, issuer_rfc, receiver_rfc, total, issue_date) "
                "VALUES (?, ?, ?, ?, ?)",
                (uuid_value, rfc, COMPANY_RFC, "123456.78", issue_date),
            )
            invoices.append((uuid_value, "", ""))
    conn.commit()
    conn.close()

    repository = EstateRepository(db_path)
    try:
        target = "H-wide"
        evidence = [
            CaseEvidence(
                evidence_id=f"ev-efos-{index}",
                statement=f"RFC {rfc} appears in efos_list.",
                direction="for",
                produced_by="test",
                source_refs=[
                    EvidenceRef(source_table="efos_list", record_id=rfc,
                                note="EFOS row.")
                ],
            )
            for index, rfc in enumerate(rfcs, start=1)
        ]
        for index, (uuid_value, _d, _t) in enumerate(invoices, start=1):
            evidence.append(
                CaseEvidence(
                    evidence_id=f"ev-inv-{index:03d}",
                    statement=f"Invoice {uuid_value} exists.",
                    direction="for",
                    produced_by="test",
                    source_refs=[
                        EvidenceRef(source_table="invoices",
                                    record_id=uuid_value, note="CFDI.")
                    ],
                )
            )
        case_state = CaseState(
            case_id="case-wide",
            lead_id="lead-wide",
            status="ready_for_verification",
            hypotheses=[
                Hypothesis(
                    hypothesis_id=target,
                    statement="Five listed issuers invoiced the audited company.",
                    scheme_type="phantom_vendor",
                    status="supported",
                    supporting_evidence_ids=[e.evidence_id for e in evidence],
                )
            ],
            evidence=evidence,
        )

        registry = build_default_registry()
        rule_id = default_rule_id_for_scheme("phantom_vendor")
        claim = build_phantom_vendor_claim(case_state, target, repository)
        report = OfficialVerifier().verify(
            case_state, target, repository, claimed_amount=claim.claimed_amount
        )
        decision = evaluate_gate(
            case_state=case_state,
            target_hypothesis_id=target,
            challenger_review=ChallengerReview(
                target_hypothesis_id=target, outcome="survives",
                reasoning_summary="Synthesised.", survives_challenge=True),
            method_critic_review=MethodCriticReview(
                target_hypothesis_id=target, outcome="clear",
                reasoning_summary="Synthesised."),
            verification_report=report,
            rule_registry=registry,
            requested_rule_id=rule_id,
        )
        assert decision.outcome == "authorize_probable"

        finding = build_finding(
            case_state=case_state,
            target_hypothesis_id=target,
            gate_decision=decision,
            verification_report=report,
            rule_registry=registry,
            requested_rule_id=rule_id,
            claimed_amount=claim.claimed_amount,
            estate=repository,
        )
        assert len(finding.narrative.split()) <= 150
        # The longer branch really was taken.
        assert "included in the amount" in finding.narrative
    finally:
        repository.close()


def test_a_listed_rfc_may_not_ride_in_on_another_rfcs_timing(tmp_path):
    """Condition 3 is universal over the matched RFCs, not just over the set.

    Two definitively listed vendors; only one has an invoice postdating its own
    listing. Naming both would attribute the second vendor's exposure to a
    listing that demonstrably does not reach any of its cited invoices.
    """

    db_path = tmp_path / "ride_along.db"
    conn = sqlite3.connect(db_path)
    _create_tables(conn)
    for rfc, published in (("AAA010101AA1", "2025-03-14"), ("BBB010101BB2", "2025-06-01")):
        conn.execute(
            "INSERT INTO efos_list (rfc, status, publication_date) VALUES (?, ?, ?)",
            (rfc, EFOS_STATUS_DEFINITIVE, published),
        )
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
        "VALUES (?, ?, ?, ?, ?)",
        ("INV-A01", "AAA010101AA1", COMPANY_RFC, "100000.00", "2025-04-09"),
    )
    # Every cited invoice of BBB predates ITS OWN publication date.
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
        "VALUES (?, ?, ?, ?, ?)",
        ("INV-B01", "BBB010101BB2", COMPANY_RFC, "900000.00", "2025-01-20"),
    )
    conn.commit()
    conn.close()

    repository = EstateRepository(db_path)
    try:
        target = "H-ride"
        evidence = [
            CaseEvidence(
                evidence_id=f"ev-{table}-{record_id}",
                statement="",
                direction="for",
                produced_by="test",
                source_refs=[EvidenceRef(source_table=table, record_id=record_id,
                                         note="Record.")],
            )
            for table, record_id in (
                ("efos_list", "AAA010101AA1"),
                ("efos_list", "BBB010101BB2"),
                ("invoices", "INV-A01"),
                ("invoices", "INV-B01"),
            )
        ]
        case_state = CaseState(
            case_id="case-ride",
            lead_id="lead-ride",
            status="ready_for_verification",
            hypotheses=[
                Hypothesis(
                    hypothesis_id=target,
                    statement="",
                    scheme_type="phantom_vendor",
                    status="supported",
                    supporting_evidence_ids=[e.evidence_id for e in evidence],
                )
            ],
            evidence=evidence,
        )
        report = OfficialVerifier().verify(
            case_state, target, repository, claimed_amount=1_000_000.00
        )
        check = next(
            c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK"
        )
        assert check.status == "unresolved"
        assert "BBB010101BB2" in check.calculation
    finally:
        repository.close()


# ==========================================================================
# RULE REGISTRY
# ==========================================================================

def test_rule_exceptions_name_the_two_real_statuses_only():
    exceptions = " ".join(RULE_CFF_69B.exceptions).lower()

    # "desvirtuado" is not a value the estate schema's status column can hold.
    assert "estatus de desvirtuado" not in exceptions
    assert EFOS_STATUS_DEFINITIVE in exceptions
    assert EFOS_STATUS_PRESUMED in exceptions
    assert any(
        EFOS_STATUS_PRESUMED in exception.lower()
        and "definitiva" in exception.lower()
        for exception in RULE_CFF_69B.exceptions
    )


# ==========================================================================
# END TO END: A PRESUMED VENDOR BECOMES A LEAD, NEVER A FINDING
# ==========================================================================

class _ScriptedLLM:
    """Deterministic stand-in: decides what to look at, never what is true."""

    provider = "scripted"
    model = "scripted-1"

    def __init__(self) -> None:
        self.calls = 0
        self.last_usage = None

    @staticmethod
    def _payload(user_prompt: str) -> dict:
        start = user_prompt.find("{")
        payload, _ = json.JSONDecoder().raw_decode(user_prompt[start:])
        return payload

    @staticmethod
    def _hypothesis_id(payload: dict) -> str:
        if isinstance(payload.get("target_hypothesis_id"), str):
            return payload["target_hypothesis_id"]
        target = payload.get("target_hypothesis")
        if isinstance(target, dict) and "hypothesis_id" in target:
            return target["hypothesis_id"]
        case_state = payload.get("case_state", payload)
        hypotheses = case_state.get("hypotheses", [])
        if hypotheses:
            return hypotheses[0]["hypothesis_id"]
        return "H-001"

    @staticmethod
    def _subject_rfc(payload: dict) -> str | None:
        case_state = payload.get("case_state", payload)
        for entity in case_state.get("subject_entities", []):
            if entity.startswith("RFC:"):
                return entity[len("RFC:"):]
        return None

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        payload = self._payload(user_prompt)

        if "You are the Challenger" in system_prompt:
            return json.dumps({
                "target_hypothesis_id": self._hypothesis_id(payload),
                "outcome": "survives",
                "reasoning_summary": "No unresolved material objection was found.",
            })

        if "You are the Method Critic" in system_prompt:
            return json.dumps({
                "target_hypothesis_id": self._hypothesis_id(payload),
                "outcome": "clear",
                "reasoning_summary": "Record matching against the supplied estate.",
            })

        case_state = payload["case_state"]
        rfc = self._subject_rfc(payload)
        hypothesis_id = self._hypothesis_id(payload)
        already = {r["action_name"] for r in case_state.get("actions_taken", [])}
        hypothesis = {
            "hypothesis_id": hypothesis_id,
            "statement": f"Invoices issued by {rfc} may correspond to simulated operations.",
            "scheme_type": "phantom_vendor",
            "status": "supported",
            "supporting_evidence_ids": [],
            "counter_evidence_ids": [],
            "unresolved_questions": [],
        }

        if rfc and "get_vendor_invoices" not in already:
            return json.dumps({
                "decision": "investigate",
                "current_assessment": "The estate links this RFC to the EFOS table.",
                "hypotheses": [hypothesis],
                "next_action": {
                    "action_name": "get_vendor_invoices",
                    "arguments": {"vendor_rfc": rfc},
                    "reason": "Establish which invoices this RFC actually issued.",
                    "question_resolved": "Which invoices does this RFC have?",
                },
                "new_unknowns": [],
                "resolved_unknowns": [],
                "reason": "Invoice records are needed before any interpretation.",
            })

        return json.dumps({
            "decision": "request_review",
            "current_assessment": "The documentary core is assembled.",
            "hypotheses": [hypothesis],
            "next_action": None,
            "new_unknowns": [],
            "resolved_unknowns": [],
            "reason": "The hypothesis needs adversarial review before verification.",
        })


def test_cli_reports_a_presumed_vendor_as_a_lead_not_a_finding(tmp_path):
    db_path = tmp_path / "presunto_only.db"
    _build_estate(db_path, innocent=True, guilty=False)
    out_path = tmp_path / "submission.json"

    exit_code = run_audit_main(
        [
            "--estate", str(db_path),
            "--out", str(out_path),
            "--seed", "4242",
        ],
        llm_client=InstrumentedLLMClient(_ScriptedLLM()),
        replay_mode=True,
    )

    assert exit_code == 0
    submission = json.loads(out_path.read_text(encoding="utf-8"))

    accused = {
        entity
        for finding in submission["findings"]
        for entity in finding["entities"]
    }
    assert f"RFC:{INNOCENT_RFC}" not in accused

    leads = [
        lead
        for lead in submission["leads_not_pursued"]
        if lead["entity"] == f"RFC:{INNOCENT_RFC}"
    ]
    assert leads, "the blocked vendor must survive as a lead_not_pursued entry"

    lead = leads[0]
    assert lead["closed_by"] == "validator"
    # A specific documentary reason, not a generic phrase.
    assert "efos_list" in lead["reason"] or "invoices." in lead["reason"]
    assert "PHANTOM-EFOS-INVOICE-LINK" in lead["reason"]
    assert lead["tool_calls_made"]
