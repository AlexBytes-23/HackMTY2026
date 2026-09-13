import pytest
from unittest.mock import MagicMock

from src.gates.evidence_gate import EvidenceGate, GateContext, EvidenceGateDecision
from src.rules.rule_registry import RuleRegistry, RuleDefinition
from src.core.models import VerifiedFact, EvidenceRef
from src.core.estate import EstateRepository

@pytest.fixture
def registry():
    reg = RuleRegistry()
    reg.register(RuleDefinition(
        rule_id="TEST_RULE_001",
        title="Test Rule",
        source="Test",
        source_reference="Art 1",
        applies_to=["invoices"],
        supports=[],
        does_not_prove=[],
        requirements=[],
        exceptions=[]
    ))
    return reg

@pytest.fixture
def mock_estate():
    estate = MagicMock(spec=EstateRepository)
    estate.record_exists.return_value = True
    return estate

def test_defensible_case_authorizes_probable(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        critical_verified_facts=[
            VerifiedFact(fact_id="f1", statement="s1", verified=True)
        ],
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
            EvidenceRef(source_table="bank_txns", record_id="btx_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001"
    )
    decision = gate.decide(context)
    assert decision.outcome == "authorize_probable"
    assert decision.authorized_confidence == "probable"
    # high detector/signal count cannot cause proven;
    # without an explicit scheme-specific proven standard, authorize_proven is impossible;

def test_challenger_needs_more_evidence_blocks(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
            EvidenceRef(source_table="bank_txns", record_id="btx_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001",
        challenger_needs_more_evidence=True,
        useful_actions_remain=True
    )
    decision = gate.decide(context)
    assert decision.outcome == "need_more_work"
    assert "Challenger outcome is needs_more_evidence" in decision.failed_requirements

def test_failed_critical_fact_blocks(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        critical_verified_facts=[
            VerifiedFact(fact_id="f1", statement="s1", verified=False)
        ],
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
            EvidenceRef(source_table="bank_txns", record_id="btx_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001",
        useful_actions_remain=False
    )
    decision = gate.decide(context)
    assert decision.outcome == "inconclusive"
    assert any("critical VerifiedFact failed verification" in req for req in decision.failed_requirements)

def test_failed_non_critical_fact_does_not_block(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        critical_verified_facts=[
            VerifiedFact(fact_id="f1", statement="s1", verified=True)
        ],
        auxiliary_verified_facts=[
            VerifiedFact(fact_id="f2", statement="s2", verified=False)
        ],
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
            EvidenceRef(source_table="bank_txns", record_id="btx_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001"
    )
    decision = gate.decide(context)
    assert decision.outcome == "authorize_probable"

def test_fewer_than_3_exhibits_blocks(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001",
        useful_actions_remain=True
    )
    decision = gate.decide(context)
    assert decision.outcome == "need_more_work"
    assert "fewer than 3 valid exhibits exist" in decision.failed_requirements

def test_nonexistent_exhibit_blocks(registry, mock_estate):
    mock_estate.record_exists.return_value = False
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
            EvidenceRef(source_table="bank_txns", record_id="btx_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001",
        useful_actions_remain=True
    )
    decision = gate.decide(context)
    assert decision.outcome == "need_more_work"
    assert any("an exhibit points to a record that does not exist" in req for req in decision.failed_requirements)

def test_resolved_legitimate_alternative_declines(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        resolved_legitimate_alternative=True,
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
            EvidenceRef(source_table="bank_txns", record_id="btx_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001"
    )
    decision = gate.decide(context)
    assert decision.outcome == "decline_hypothesis"

def test_missing_data_actions_remain_need_more_work(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001",
        useful_actions_remain=True
    )
    decision = gate.decide(context)
    assert decision.outcome == "need_more_work"

def test_missing_data_no_actions_inconclusive(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001",
        useful_actions_remain=False
    )
    decision = gate.decide(context)
    assert decision.outcome == "inconclusive"

def test_unresolved_method_critic_objection_blocks(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        material_method_objections=["biased sample"],
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
            EvidenceRef(source_table="bank_txns", record_id="btx_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001",
        useful_actions_remain=True
    )
    decision = gate.decide(context)
    assert decision.outcome == "need_more_work"
    assert "material Method Critic objection remains" in decision.failed_requirements

def test_unknown_rule_id_rejected(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type="kickback",
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
            EvidenceRef(source_table="bank_txns", record_id="btx_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="NONEXISTENT_RULE",
        useful_actions_remain=False
    )
    decision = gate.decide(context)
    assert decision.outcome == "inconclusive"
    assert "requested rule_id is not valid in the Rule Registry" in decision.failed_requirements

def test_hypothesis_missing_or_rejected(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        target_hypothesis_exists=False,
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
            EvidenceRef(source_table="bank_txns", record_id="btx_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001",
        useful_actions_remain=False
    )
    decision = gate.decide(context)
    assert "target hypothesis does not exist" in decision.failed_requirements
    
    context.target_hypothesis_exists = True
    context.target_hypothesis_rejected = True
    decision2 = gate.decide(context)
    assert decision2.outcome == "decline_hypothesis"
    assert "target hypothesis is already rejected" in decision2.failed_requirements

def test_missing_scheme_type(registry, mock_estate):
    gate = EvidenceGate(mock_estate, registry)
    context = GateContext(
        target_hypothesis_id="hyp_001",
        scheme_type=None,
        proposed_exhibits=[
            EvidenceRef(source_table="invoices", record_id="inv_1"),
            EvidenceRef(source_table="ledger", record_id="led_1"),
            EvidenceRef(source_table="bank_txns", record_id="btx_1"),
        ],
        claimed_peso_amount=100.0,
        reconciled_peso_amount=100.0,
        requested_rule_id="TEST_RULE_001",
        useful_actions_remain=False
    )
    decision = gate.decide(context)
    assert "scheme_type is missing" in decision.failed_requirements
