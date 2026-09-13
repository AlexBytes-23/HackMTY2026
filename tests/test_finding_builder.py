import math
import pytest
from src.output.finding_builder import build_finding
from src.core.models import CaseState, Hypothesis, EvidenceRef
from src.core.estate import EstateRepository
from src.gates.evidence_gate import EvidenceGateDecision
from src.verifier.official_verifier import VerificationReport, VerificationCheck
from src.rules.rule_registry import RuleRegistry, RuleDefinition

@pytest.fixture
def mock_estate():
    class MockEstate(EstateRepository):
        def __init__(self):
            self.db_path = ":memory:"
            self.records = {
                "efos_list": {
                    "EFOS123": {"rfc": "EFOS123"},
                    "EFOS_UNRELATED": {"rfc": "OTHER_RFC"},
                    "ODD_ID": {"rfc": "VALID_RFC"},
                    "MISSING": {"other": "val"},
                    "NONE_RFC": {"rfc": None},
                    "EMPTY_RFC": {"rfc": "   "},
                    "INT_RFC": {"rfc": 12345}
                },
                "invoices": {
                    "inv_1": {"issuer_rfc": "EFOS123"},
                    "inv_2": {"issuer_rfc": "EFOS123"},
                    "inv_3": {"issuer_rfc": "EFOS123"},
                    "inv_unrelated": {"issuer_rfc": "UNRELATED_INV_RFC"},
                    "inv_valid": {"issuer_rfc": "VALID_RFC"}
                }
            }
        def get_record(self, table: str, record_id: str) -> dict | None:
            return self.records.get(table, {}).get(record_id, {})
        def record_exists(self, table: str, record_id: str) -> bool:
            return True
        def get_record_amount(self, table: str, record_id: str) -> float | None:
            return 50.0
    return MockEstate()

@pytest.fixture
def registry():
    reg = RuleRegistry()
    reg.register(RuleDefinition(
        rule_id="PV_RULE",
        title="PV Fraud",
        source="Art",
        source_reference="69-B",
        applies_to=["phantom_vendor"]
    ))
    return reg

def make_inputs(mock_estate, scheme_type="phantom_vendor", outcome="authorize_probable", confidence="probable", omit_pv_check=False, omit_peso_check=False, target_id="h1", gate_target_id="h1", exhibits=3, claimed_amount=100.0, rule_id="PV_RULE", efos_record_id="EFOS123"):
    state = CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[Hypothesis(hypothesis_id="h1", statement="", scheme_type=scheme_type)]
    )

    decision = EvidenceGateDecision(
        target_hypothesis_id=gate_target_id,
        outcome=outcome,
        authorized_confidence=confidence,
        reason=""
    )

    checks = []
    if not omit_peso_check:
        checks.append(VerificationCheck(check_id="PESO-RECONCILIATION", statement="", status="verified", critical=True))
    if not omit_pv_check:
        checks.append(VerificationCheck(check_id="PHANTOM-EFOS-INVOICE-LINK", statement="", status="verified", critical=True))

    resolved = []
    if exhibits >= 1: resolved.append(EvidenceRef(source_table="efos_list", record_id=efos_record_id, note="Test note"))
    if exhibits >= 2: resolved.append(EvidenceRef(source_table="invoices", record_id="inv_1"))
    if exhibits >= 3: resolved.append(EvidenceRef(source_table="invoices", record_id="inv_2"))
    if exhibits >= 4: resolved.append(EvidenceRef(source_table="invoices", record_id="inv_3"))

    report = VerificationReport(
        target_hypothesis_id="h1",
        checks=checks,
        resolved_exhibits=resolved,
        reconciliation={"reconciles": True}
    )

    return {
        "case_state": state,
        "target_hypothesis_id": target_id,
        "gate_decision": decision,
        "verification_report": report,
        "requested_rule_id": rule_id,
        "claimed_amount": claimed_amount,
        "estate": mock_estate
    }

def test_valid_authorized_phantom_vendor_builds_finding(registry, mock_estate):
    inputs = make_inputs(mock_estate)
    inputs["rule_registry"] = registry
    finding = build_finding(**inputs)
    assert finding.scheme_type == "phantom_vendor"

def test_gate_target_mismatch_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, target_id="h1", gate_target_id="h2")
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="Gate decision target_hypothesis_id mismatch"):
        build_finding(**inputs)

def test_report_target_mismatch_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate)
    inputs["rule_registry"] = registry
    inputs["verification_report"].target_hypothesis_id = "h2"
    with pytest.raises(ValueError, match="VerificationReport target_hypothesis_id mismatch"):
        build_finding(**inputs)

def test_gate_inconclusive_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, outcome="inconclusive", confidence=None)
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="authorize_probable"):
        build_finding(**inputs)

def test_wrong_scheme_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, scheme_type="kickback")
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="only supports phantom_vendor"):
        build_finding(**inputs)

def test_missing_phantom_link_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, omit_pv_check=True)
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="PHANTOM-EFOS-INVOICE-LINK"):
        build_finding(**inputs)

def test_substantive_check_unresolved_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate)
    inputs["rule_registry"] = registry
    inputs["verification_report"].checks[1].status = "unresolved"
    with pytest.raises(ValueError, match="PHANTOM-EFOS-INVOICE-LINK"):
        build_finding(**inputs)

def test_missing_verified_peso_reconciliation_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, omit_peso_check=True)
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="PESO-RECONCILIATION"):
        build_finding(**inputs)

def test_fewer_than_3_unique_exhibits_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, exhibits=2)
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="Requires >= 3 unique exhibits"):
        build_finding(**inputs)

def test_claimed_amount_none_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, claimed_amount=None)
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="finite positive number"):
        build_finding(**inputs)

def test_claimed_amount_zero_or_negative_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, claimed_amount=0)
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="finite positive number"):
        build_finding(**inputs)

def test_claimed_amount_nan_inf_refuses(registry, mock_estate):
    for val in [float("nan"), float("inf"), float("-inf")]:
        inputs = make_inputs(mock_estate, claimed_amount=val)
        inputs["rule_registry"] = registry
        with pytest.raises(ValueError, match="finite positive number"):
            build_finding(**inputs)

def test_missing_rule_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, rule_id="NONEXISTENT")
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="not found in registry"):
        build_finding(**inputs)

def test_wrong_rule_applicability_refuses(registry, mock_estate):
    registry.register(RuleDefinition(rule_id="BAD", title="bad", source="s", source_reference="r", applies_to=["kickback"]))
    inputs = make_inputs(mock_estate, rule_id="BAD")
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="does not apply to phantom_vendor"):
        build_finding(**inputs)

def test_deterministic_identical_output(registry, mock_estate):
    inputs1 = make_inputs(mock_estate)
    inputs1["rule_registry"] = registry
    finding1 = build_finding(**inputs1)

    inputs2 = make_inputs(mock_estate)
    inputs2["rule_registry"] = registry
    finding2 = build_finding(**inputs2)

    assert finding1.model_dump() == finding2.model_dump()

def test_efos_actual_rfc_used_instead_of_record_id(registry, mock_estate):
    inputs = make_inputs(mock_estate, efos_record_id="ODD_ID")
    inputs["rule_registry"] = registry
    inputs["verification_report"].resolved_exhibits.append(EvidenceRef(source_table="invoices", record_id="inv_valid"))
    finding = build_finding(**inputs)
    assert finding.entities == ["RFC:VALID_RFC"]

def test_efos_unrelated_rfc_is_excluded(registry, mock_estate):
    inputs = make_inputs(mock_estate)
    inputs["rule_registry"] = registry
    inputs["verification_report"].resolved_exhibits.append(EvidenceRef(source_table="efos_list", record_id="EFOS_UNRELATED"))
    inputs["verification_report"].resolved_exhibits.append(EvidenceRef(source_table="invoices", record_id="inv_unrelated"))
    finding = build_finding(**inputs)
    assert finding.entities == ["RFC:EFOS123"]

def test_efos_no_intersection_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate)
    inputs["rule_registry"] = registry
    inputs["verification_report"].resolved_exhibits = [
        EvidenceRef(source_table="efos_list", record_id="EFOS_UNRELATED"),
        EvidenceRef(source_table="invoices", record_id="inv_1"),
        EvidenceRef(source_table="invoices", record_id="inv_unrelated")
    ]
    with pytest.raises(ValueError, match="No RFC intersection found"):
        build_finding(**inputs)

def test_efos_missing_rfc_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, efos_record_id="MISSING")
    inputs["rule_registry"] = registry
    # intersection will fail
    with pytest.raises(ValueError, match="No RFC intersection found"):
        build_finding(**inputs)

def test_efos_none_rfc_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, efos_record_id="NONE_RFC")
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="No RFC intersection found"):
        build_finding(**inputs)

def test_efos_empty_rfc_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, efos_record_id="EMPTY_RFC")
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="No RFC intersection found"):
        build_finding(**inputs)

def test_efos_wrong_type_rfc_refuses(registry, mock_estate):
    inputs = make_inputs(mock_estate, efos_record_id="INT_RFC")
    inputs["rule_registry"] = registry
    with pytest.raises(ValueError, match="No RFC intersection found"):
        build_finding(**inputs)
