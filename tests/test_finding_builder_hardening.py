import pytest

from src.core.estate import EstateRepository
from src.core.models import CaseState, Hypothesis, EvidenceRef
from src.gates.evidence_gate import EvidenceGateDecision
from src.output.finding_builder import build_finding
from src.rules.rule_registry import RuleRegistry, RuleDefinition
from src.verifier.official_verifier import VerificationReport, VerificationCheck


class MockEstate(EstateRepository):
    def __init__(self):
        self.db_path = ":memory:"
        # The efos_list rows carry a definitive status and a publication_date the
        # invoices postdate: build_finding now names entities from the same
        # deterministic assessment the verifier runs, and a bare rfc qualifies
        # nobody.
        self.records = {
            "efos_list": {
                "EFOS123": {
                    "rfc": "EFOS123",
                    "status": "definitivo",
                    "publication_date": "2025-03-01",
                },
                "EFOS_UNRELATED": {
                    "rfc": "OTHER_RFC",
                    "status": "definitivo",
                    "publication_date": "2025-03-01",
                },
            },
            "invoices": {
                "inv_1": {"issuer_rfc": "EFOS123", "issue_date": "2025-04-01"},
                "inv_2": {"issuer_rfc": "EFOS123", "issue_date": "2025-04-05"},
                "inv_3": {"issuer_rfc": "EFOS123", "issue_date": "2025-04-09"},
                "inv_unrelated": {
                    "issuer_rfc": "UNRELATED_INV_RFC",
                    "issue_date": "2025-04-11",
                },
            },
        }

    def get_record(self, table: str, record_id: str) -> dict | None:
        return self.records.get(table, {}).get(record_id, {})

    def record_exists(self, table: str, record_id: str) -> bool:
        return record_id in self.records.get(table, {})

    def get_record_amount(self, table: str, record_id: str) -> float | None:
        return 50.0


def _registry() -> RuleRegistry:
    registry = RuleRegistry()
    registry.register(
        RuleDefinition(
            rule_id="PV_RULE",
            title="PV Fraud",
            source="Art",
            source_reference="69-B",
            applies_to=["phantom_vendor"],
        )
    )
    return registry


def _inputs(*, claimed_amount=100.0):
    estate = MockEstate()
    case_state = CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                statement="",
                scheme_type="phantom_vendor",
            )
        ],
    )
    gate = EvidenceGateDecision(
        target_hypothesis_id="h1",
        outcome="authorize_probable",
        authorized_confidence="probable",
        reason="authorized for test",
    )
    report = VerificationReport(
        target_hypothesis_id="h1",
        checks=[
            VerificationCheck(
                check_id="PESO-RECONCILIATION",
                statement="",
                status="verified",
                critical=True,
            ),
            VerificationCheck(
                check_id="PHANTOM-EFOS-INVOICE-LINK",
                statement="",
                status="verified",
                critical=True,
            ),
        ],
        resolved_exhibits=[
            EvidenceRef(
                source_table="efos_list",
                record_id="EFOS123",
                note="Test note",
            ),
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="invoices", record_id="inv_2"),
        ],
        reconciliation={"reconciles": True},
    )
    return {
        "case_state": case_state,
        "target_hypothesis_id": "h1",
        "gate_decision": gate,
        "verification_report": report,
        "rule_registry": _registry(),
        "requested_rule_id": "PV_RULE",
        "claimed_amount": claimed_amount,
        "estate": estate,
    }


def test_claimed_amount_bool_refuses():
    inputs = _inputs(claimed_amount=True)
    with pytest.raises(ValueError, match="finite positive number"):
        build_finding(**inputs)


def test_internal_verifier_error_refuses():
    inputs = _inputs()
    inputs["verification_report"].internal_errors = ["synthetic verifier error"]
    with pytest.raises(ValueError, match="internal errors"):
        build_finding(**inputs)


def test_critical_failure_summary_refuses():
    inputs = _inputs()
    inputs["verification_report"].critical_failures = ["SYNTHETIC-FAIL"]
    with pytest.raises(ValueError, match="critical failures"):
        build_finding(**inputs)


def test_unresolved_critical_summary_refuses():
    inputs = _inputs()
    inputs["verification_report"].unresolved_critical_checks = ["SYNTHETIC-UNKNOWN"]
    with pytest.raises(ValueError, match="unresolved critical"):
        build_finding(**inputs)


def test_reconciliation_flag_must_be_explicitly_true():
    inputs = _inputs()
    inputs["verification_report"].reconciliation = {"reconciles": False}
    with pytest.raises(ValueError, match="successful deterministic peso reconciliation"):
        build_finding(**inputs)


def test_narrative_is_factual_and_does_not_expose_internal_gate():
    finding = build_finding(**_inputs())

    lower = finding.narrative.lower()
    assert "evidence gate" not in lower
    assert "criminal intent" in lower
    assert "efos123" in lower
    assert "mxn 100.00" in lower
    assert "fraud" not in lower
    assert "fake invoice" not in lower


def test_default_exhibit_note_does_not_overstate_verification():
    finding = build_finding(**_inputs())

    fallback_notes = [
        exhibit.note
        for exhibit in finding.exhibits
        if exhibit.source_table == "invoices"
    ]
    assert fallback_notes
    assert all(note.startswith("Resolved invoices record") for note in fallback_notes)
    assert all("record existence verified" in note for note in fallback_notes)


def test_exhibit_order_is_stable_across_equivalent_input_order():
    first = _inputs()
    second = _inputs()
    second["verification_report"].resolved_exhibits = list(
        reversed(second["verification_report"].resolved_exhibits)
    )

    finding_a = build_finding(**first)
    finding_b = build_finding(**second)

    assert [exhibit.model_dump() for exhibit in finding_a.exhibits] == [
        exhibit.model_dump() for exhibit in finding_b.exhibits
    ]
