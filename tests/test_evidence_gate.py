import pytest
from src.gates.evidence_gate import evaluate_gate
from src.core.models import CaseState, Hypothesis, EvidenceRef
from src.verifier.official_verifier import VerificationReport, VerificationCheck
from src.rules.rule_registry import RuleRegistry, RuleDefinition

@pytest.fixture
def registry():
    reg = RuleRegistry()
    reg.register(RuleDefinition(
        rule_id="TEST_RULE_001",
        title="Test Rule",
        source="Test",
        source_reference="1",
        applies_to=["phantom_vendor"] # CHANGED from kickback to phantom_vendor
    ))
    reg.register(RuleDefinition(
        rule_id="KICKBACK_RULE",
        title="Kickback Rule",
        source="Test",
        source_reference="1",
        applies_to=["round_tripping"]
    ))
    reg.register(RuleDefinition(
        rule_id="WRONG_SCHEME_RULE",
        title="Phantom Vendor Rule",
        source="Test",
        source_reference="1",
        applies_to=["phantom_vendor"]
    ))
    reg.register(RuleDefinition(
        rule_id="EMPTY_APPLIES_TO",
        title="Empty",
        source="Test",
        source_reference="1",
        applies_to=[]
    ))
    return reg

def make_base_case(scheme_type="phantom_vendor"):
    return CaseState(
        case_id="c1",
        lead_id="l1",
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                statement="test",
                scheme_type=scheme_type,
                status="open"
            )
        ]
    )

def make_base_verification():
    return VerificationReport(
        target_hypothesis_id="h1",
        checks=[
            VerificationCheck(check_id="PESO-RECONCILIATION", statement="reconciles", status="verified", critical=True),
            VerificationCheck(check_id="EXISTS-1", statement="exists", status="verified", critical=True),
            VerificationCheck(check_id="PHANTOM-EFOS-INVOICE-LINK", statement="match", status="verified", critical=True)
        ],
        resolved_exhibits=[
            EvidenceRef(source_table="invoices", record_id="1"),
            EvidenceRef(source_table="invoices", record_id="2"),
            EvidenceRef(source_table="invoices", record_id="3")
        ],
        reconciliation={"reconciles": True},
        critical_failures=[],
        unresolved_critical_checks=[]
    )

class DummyReview:
    def __init__(self, outcome):
        self.outcome = outcome

def test_fully_valid_package_authorizes_probable(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "authorize_probable"

def test_rejected_hypothesis_declines(registry):
    case = make_base_case()
    case.hypotheses[0].status = "rejected"
    decision = evaluate_gate(
        case_state=case,
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "decline_hypothesis"

def test_fewer_than_3_unique_exhibits_blocks(registry):
    verif = make_base_verification()
    verif.resolved_exhibits = [
        EvidenceRef(source_table="invoices", record_id="1"),
        EvidenceRef(source_table="invoices", record_id="1") # duplicate
    ]
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001",
        useful_actions_remain=True
    )
    assert decision.outcome == "need_more_work"
    assert any("fewer than 3" in req for req in decision.failed_requirements)

def test_challenger_needs_more_evidence_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("needs_more_evidence"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "need_more_work"

def test_challenger_legitimate_alternative_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("legitimate_alternative"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "decline_hypothesis"

def test_challenger_alternative_hypothesis_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("alternative_hypothesis"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"
    assert any("alternative hypothesis" in req for req in decision.failed_requirements)

def test_method_critic_needs_more_work_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("needs_more_work"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "need_more_work"

def test_method_critic_cannot_support_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("cannot_support"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"

def test_method_critic_clear_does_not_block(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "authorize_probable"

def test_unreconciled_peso_blocks(registry):
    verif = make_base_verification()
    verif.reconciliation = {"reconciles": False}
    verif.checks[0].status = "failed" # The PESO-RECONCILIATION check
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001",
        useful_actions_remain=True
    )
    assert decision.outcome == "need_more_work"
    assert any("peso_amount is unsupported or fails" in req for req in decision.failed_requirements)

def test_authorize_proven_impossible(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "authorize_probable"
    assert decision.authorized_confidence != "proven"

def test_wrong_scheme_applicability_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case("round_tripping"),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001" # Only applies to phantom_vendor
    )
    assert decision.outcome == "inconclusive"
    assert any("does not apply" in req for req in decision.failed_requirements)

def test_nonexistent_rule_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="NON_EXISTENT"
    )
    assert decision.outcome == "inconclusive"
    assert any("not valid" in req for req in decision.failed_requirements)

def test_empty_applies_to_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="EMPTY_APPLIES_TO"
    )
    assert decision.outcome == "inconclusive"
    assert any("empty applies_to" in req for req in decision.failed_requirements)

def test_gate_derives_scheme_type_from_hypothesis(registry):
    decision = evaluate_gate(
        case_state=make_base_case("round_tripping"),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="WRONG_SCHEME_RULE" # Only applies to phantom_vendor
    )
    assert decision.outcome == "inconclusive"

def test_target_hypothesis_missing_scheme_type_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case(None),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"
    assert any("no scheme_type" in req for req in decision.failed_requirements)

def test_missing_challenger_review_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=None,
        method_critic_review=DummyReview("clear"),
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "need_more_work"
    assert any("ChallengerReview is mandatory but missing" in req for req in decision.failed_requirements)

def test_missing_method_critic_review_blocks(registry):
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=None,
        verification_report=make_base_verification(),
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "need_more_work"
    assert any("MethodCriticReview is mandatory but missing" in req for req in decision.failed_requirements)

def test_mismatched_report_target_blocks(registry):
    verif = make_base_verification()
    verif.target_hypothesis_id = "h2" # Mismatch!
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"
    assert any("target hypothesis does not match Gate target" in req for req in decision.failed_requirements)

def test_internal_errors_non_empty_blocks(registry):
    verif = make_base_verification()
    verif.internal_errors = ["Some error occurred"]
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"
    assert any("internal errors" in req for req in decision.failed_requirements)

def test_empty_checks_blocks(registry):
    verif = make_base_verification()
    verif.checks = [] # Empty checks!
    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"
    assert any("no checks" in req for req in decision.failed_requirements)

def test_actual_failed_critical_verification_blocks_despite_summary(registry):
    verif = make_base_verification()
    verif.checks.append(VerificationCheck(check_id="BAD-CHECK", statement="", status="failed", critical=True))
    verif.critical_failures = []

    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"
    assert any("Critical check failed: BAD-CHECK" in req for req in decision.failed_requirements)

def test_actual_unresolved_critical_verification_blocks_despite_summary(registry):
    verif = make_base_verification()
    verif.checks.append(VerificationCheck(check_id="UNKNOWN-CHECK", statement="", status="unresolved", critical=True))
    verif.unresolved_critical_checks = []

    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"
    assert any("Critical check unresolved: UNKNOWN-CHECK" in req for req in decision.failed_requirements)

# --- NEW SUBSTANTIVE/CRITICAL TESTS ---

def test_unsupported_scheme_handcrafted_otherwise_valid_report_blocks(registry):
    verif = make_base_verification()
    decision = evaluate_gate(
        case_state=make_base_case("round_tripping"),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="KICKBACK_RULE"
    )
    assert decision.outcome == "inconclusive"
    assert any("No supported deterministic substantive verifier exists" in req for req in decision.failed_requirements)

def test_phantom_vendor_missing_phantom_link_blocks(registry):
    verif = make_base_verification()
    # Remove PHANTOM-EFOS-INVOICE-LINK
    verif.checks = [c for c in verif.checks if c.check_id != "PHANTOM-EFOS-INVOICE-LINK"]
    decision = evaluate_gate(
        case_state=make_base_case("phantom_vendor"),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"
    assert any("lacks a critical verified PHANTOM-EFOS-INVOICE-LINK check" in req for req in decision.failed_requirements)

def test_phantom_vendor_non_critical_phantom_link_blocks(registry):
    verif = make_base_verification()
    check = next(c for c in verif.checks if c.check_id == "PHANTOM-EFOS-INVOICE-LINK")
    check.critical = False
    decision = evaluate_gate(
        case_state=make_base_case("phantom_vendor"),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"
    assert any("lacks a critical verified PHANTOM-EFOS-INVOICE-LINK check" in req for req in decision.failed_requirements)

def test_verified_peso_reconciliation_with_critical_false_blocks(registry):
    verif = make_base_verification()
    check = next(c for c in verif.checks if c.check_id == "PESO-RECONCILIATION")
    check.critical = False
    decision = evaluate_gate(
        case_state=make_base_case("phantom_vendor"),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )
    assert decision.outcome == "inconclusive"
    assert any("lacks a critical verified PESO-RECONCILIATION check" in req for req in decision.failed_requirements)

def test_truthy_non_boolean_reconciliation_blocks(registry):
    verif = make_base_verification()
    verif.reconciliation = {"reconciles": "yes"}

    decision = evaluate_gate(
        case_state=make_base_case(),
        target_hypothesis_id="h1",
        challenger_review=DummyReview("survives"),
        method_critic_review=DummyReview("clear"),
        verification_report=verif,
        rule_registry=registry,
        requested_rule_id="TEST_RULE_001"
    )

    assert decision.outcome == "inconclusive"
    assert any(
        "peso_amount is unsupported or fails" in req
        for req in decision.failed_requirements
    )
