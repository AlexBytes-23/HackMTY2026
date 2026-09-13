import pytest
from src.core.models import CaseState, Hypothesis, CaseEvidence, EvidenceRef
from src.core.estate import EstateRepository
from src.verifier.official_verifier import OfficialVerifier

@pytest.fixture
def mock_estate():
    class MockEstate(EstateRepository):
        def __init__(self):
            self.db_path = ":memory:"
        def record_exists(self, table: str, record_id: str) -> bool:
            return record_id != "missing_1"
        def get_record_amount(self, table: str, record_id: str) -> float | None:
            if record_id == "inv_1": return 50.0
            if record_id == "inv_2": return 50.0
            if record_id == "inv_3": return 50.0
            return None
        def get_record(self, table: str, record_id: str) -> dict | None:
            if table == "efos_list":
                if record_id == "EFOS123":
                    return {"rfc": "EFOS123"}
                if record_id == "EFOS_BAD":
                    return {"other_field": "val"}
                if record_id == "EFOS_NO_RFC":
                    return {}
                if record_id == "EFOS_NONE":
                    return {"rfc": None}
                if record_id == "EFOS_EMPTY":
                    return {"rfc": "   "}
            if table == "invoices":
                if record_id == "inv_1":
                    return {"issuer_rfc": "EFOS123"}
                if record_id == "inv_2":
                    return {"issuer_rfc": "OTHER_RFC"}
                if record_id == "inv_3":
                    return {"missing_issuer_rfc": "val"}
                if record_id == "INV_NONE":
                    return {"issuer_rfc": None}
                if record_id == "INV_EMPTY":
                    return {"issuer_rfc": ""}
            return {}

    return MockEstate()

def test_verifier_valid_exhibit(mock_estate):
    state = CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(hypothesis_id="h1", statement="", supporting_evidence_ids=["ev1"], scheme_type="kickback")
        ],
        evidence=[
            CaseEvidence(
                evidence_id="ev1",
                statement="",
                direction="for",
                produced_by="test",
                source_refs=[
                    EvidenceRef(source_table="invoices", record_id="inv_1"),
                    EvidenceRef(source_table="invoices", record_id="inv_2")
                ]
            )
        ]
    )

    verifier = OfficialVerifier()
    report = verifier.verify(state, "h1", mock_estate, claimed_amount=100.0)

    assert len(report.resolved_exhibits) == 2
    assert len(report.missing_exhibits) == 0
    assert len(report.duplicate_exhibits) == 0
    assert report.reconciliation["reconciles"] is True
    assert "PESO-RECONCILIATION" not in report.critical_failures

def test_verifier_missing_claimed_amount(mock_estate):
    state = CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(hypothesis_id="h1", statement="", supporting_evidence_ids=["ev1"], scheme_type="kickback")
        ],
        evidence=[
            CaseEvidence(
                evidence_id="ev1",
                statement="",
                direction="for",
                produced_by="test",
                source_refs=[
                    EvidenceRef(source_table="invoices", record_id="inv_1")
                ]
            )
        ]
    )

    verifier = OfficialVerifier()
    report = verifier.verify(state, "h1", mock_estate, claimed_amount=None)

    assert report.reconciliation is None
    assert "PESO-RECONCILIATION" in report.unresolved_critical_checks
    assert "PESO-RECONCILIATION" not in report.critical_failures

def test_verifier_nonexistent_exhibit(mock_estate):
    state = CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(hypothesis_id="h1", statement="", supporting_evidence_ids=["ev1"], scheme_type="kickback")
        ],
        evidence=[
            CaseEvidence(
                evidence_id="ev1",
                statement="",
                direction="for",
                produced_by="test",
                source_refs=[
                    EvidenceRef(source_table="invoices", record_id="missing_1")
                ]
            )
        ]
    )

    verifier = OfficialVerifier()
    report = verifier.verify(state, "h1", mock_estate, claimed_amount=0.0)

    assert len(report.resolved_exhibits) == 0
    assert len(report.missing_exhibits) == 1
    assert len(report.critical_failures) > 0
    assert "EXISTS-invoices-missing_1" in report.critical_failures
    assert "EXISTS-invoices-missing_1" not in report.unresolved_critical_checks

def test_verifier_duplicate_exhibit(mock_estate):
    state = CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(hypothesis_id="h1", statement="", supporting_evidence_ids=["ev1"], scheme_type="kickback")
        ],
        evidence=[
            CaseEvidence(
                evidence_id="ev1",
                statement="",
                direction="for",
                produced_by="test",
                source_refs=[
                    EvidenceRef(source_table="invoices", record_id="inv_1"),
                    EvidenceRef(source_table="invoices", record_id="inv_1")
                ]
            )
        ]
    )

    verifier = OfficialVerifier()
    report = verifier.verify(state, "h1", mock_estate, claimed_amount=50.0)

    assert len(report.resolved_exhibits) == 1
    assert len(report.duplicate_exhibits) == 1
    assert report.reconciliation["reconciles"] is True

def test_verifier_peso_reconciliation_fails(mock_estate):
    state = CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(hypothesis_id="h1", statement="", supporting_evidence_ids=["ev1"], scheme_type="kickback")
        ],
        evidence=[
            CaseEvidence(
                evidence_id="ev1",
                statement="",
                direction="for",
                produced_by="test",
                source_refs=[
                    EvidenceRef(source_table="invoices", record_id="inv_1")
                ]
            )
        ]
    )

    verifier = OfficialVerifier()
    report = verifier.verify(state, "h1", mock_estate, claimed_amount=100.0)

    assert report.reconciliation["reconciles"] is False
    assert "PESO-RECONCILIATION" in report.critical_failures

def test_verifier_integer_ledger_id_coercion(mock_estate):
    state = CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(hypothesis_id="h1", statement="", supporting_evidence_ids=["ev1"], scheme_type="kickback")
        ],
        evidence=[
            CaseEvidence(
                evidence_id="ev1",
                statement="",
                direction="for",
                produced_by="test",
                source_refs=[
                    EvidenceRef(source_table="ledger", record_id="123")
                ]
            )
        ]
    )

    verifier = OfficialVerifier()
    report = verifier.verify(state, "h1", mock_estate, claimed_amount=0.0)

    assert len(report.resolved_exhibits) == 1

def test_verifier_cross_table_not_summed(mock_estate):
    state = CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(hypothesis_id="h1", statement="", supporting_evidence_ids=["ev1"], scheme_type="kickback")
        ],
        evidence=[
            CaseEvidence(
                evidence_id="ev1",
                statement="",
                direction="for",
                produced_by="test",
                source_refs=[
                    EvidenceRef(source_table="invoices", record_id="inv_1"),
                    EvidenceRef(source_table="bank_txns", record_id="inv_2")
                ]
            )
        ]
    )

    verifier = OfficialVerifier()
    report = verifier.verify(state, "h1", mock_estate, claimed_amount=100.0)

    assert report.reconciliation["reconciles"] is False
    assert "PESO-RECONCILIATION" in report.critical_failures


# --- SUBSTANTIVE VERIFIER TESTS ---

def make_pv_state(evidence_refs):
    return CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(hypothesis_id="h1", statement="", supporting_evidence_ids=["ev1"], scheme_type="phantom_vendor")
        ],
        evidence=[
            CaseEvidence(
                evidence_id="ev1",
                statement="",
                direction="for",
                produced_by="test",
                source_refs=evidence_refs
            )
        ]
    )

def test_pv_substantive_verified(mock_estate):
    state = make_pv_state([
        EvidenceRef(source_table="efos_list", record_id="EFOS123"),
        EvidenceRef(source_table="invoices", record_id="inv_1")
    ])
    report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=50.0)
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    assert check.status == "verified"
    assert "Match found" in check.calculation

def test_pv_substantive_failed(mock_estate):
    state = make_pv_state([
        EvidenceRef(source_table="efos_list", record_id="EFOS123"),
        EvidenceRef(source_table="invoices", record_id="inv_2") # OTHER_RFC
    ])
    report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=50.0)
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    assert check.status == "failed"
    assert "No match" in check.calculation

def test_pv_substantive_missing_efos(mock_estate):
    state = make_pv_state([
        EvidenceRef(source_table="invoices", record_id="inv_1")
    ])
    report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=50.0)
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    assert check.status == "unresolved"
    assert "Missing resolved evidence" in check.calculation

def test_pv_substantive_missing_invoice(mock_estate):
    state = make_pv_state([
        EvidenceRef(source_table="efos_list", record_id="EFOS123")
    ])
    report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=0.0)
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    assert check.status == "unresolved"

def test_pv_substantive_missing_fields(mock_estate):
    state = make_pv_state([
        EvidenceRef(source_table="efos_list", record_id="EFOS123"),
        EvidenceRef(source_table="invoices", record_id="inv_3") # missing issuer_rfc field entirely
    ])
    report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=50.0)
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    assert check.status == "unresolved"

# --- NEW TESTS FOR STRICT FIELD EXTRACTION ---

def test_pv_efos_record_lacks_rfc(mock_estate):
    state = make_pv_state([
        EvidenceRef(source_table="efos_list", record_id="EFOS_NO_RFC"),
        EvidenceRef(source_table="invoices", record_id="inv_1")
    ])
    report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=50.0)
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    assert check.status == "unresolved"
    assert "safely extract required valid, non-empty RFC" in check.calculation

def test_pv_efos_rfc_none(mock_estate):
    state = make_pv_state([
        EvidenceRef(source_table="efos_list", record_id="EFOS_NONE"),
        EvidenceRef(source_table="invoices", record_id="inv_1")
    ])
    report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=50.0)
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    assert check.status == "unresolved"

def test_pv_efos_rfc_empty(mock_estate):
    state = make_pv_state([
        EvidenceRef(source_table="efos_list", record_id="EFOS_EMPTY"),
        EvidenceRef(source_table="invoices", record_id="inv_1")
    ])
    report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=50.0)
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    assert check.status == "unresolved"

def test_pv_invoice_issuer_rfc_none(mock_estate):
    state = make_pv_state([
        EvidenceRef(source_table="efos_list", record_id="EFOS123"),
        EvidenceRef(source_table="invoices", record_id="INV_NONE")
    ])
    report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=50.0)
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    assert check.status == "unresolved"

def test_pv_invoice_issuer_rfc_empty(mock_estate):
    state = make_pv_state([
        EvidenceRef(source_table="efos_list", record_id="EFOS123"),
        EvidenceRef(source_table="invoices", record_id="INV_EMPTY")
    ])
    report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=50.0)
    check = next(c for c in report.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    assert check.status == "unresolved"

def test_other_schemes_unresolved(mock_estate):
    for scheme in ["kickback", "round_tripping", "threshold_splitting", "revenue_inflation"]:
        state = CaseState(
            case_id="c1",
            lead_id="l1",
            hypotheses=[
                Hypothesis(hypothesis_id="h1", statement="", supporting_evidence_ids=["ev1"], scheme_type=scheme)
            ],
            evidence=[
                CaseEvidence(
                    evidence_id="ev1",
                    statement="",
                    direction="for",
                    produced_by="test",
                    source_refs=[EvidenceRef(source_table="invoices", record_id="inv_1")]
                )
            ]
        )
        report = OfficialVerifier().verify(state, "h1", mock_estate, claimed_amount=50.0)
        check = next(c for c in report.checks if c.check_id == "SCHEME-SUBSTANTIVE-VERIFICATION")
        assert check.status == "unresolved"
        assert "No deterministic substantive verifier implemented" in check.calculation
