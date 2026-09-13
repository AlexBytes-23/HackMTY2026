import json
import pytest
from src.core.models import CaseState, Hypothesis, CaseEvidence, EvidenceRef, ProposedAction, ActionRecord, Observation
from src.agents.method_critic import (
    MethodCriticReview,
    MethodCriticOutcome,
    criticize_method,
    find_evidence_dependencies,
    build_method_critic_prompt,
    METHOD_CRITIC_PROMPT
)
from src.investigation.investigator import LLMClient

class FakeLLMClient(LLMClient):
    def __init__(self, response_text: str):
        self.response_text = response_text
    
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self.response_text

@pytest.fixture
def base_case_state():
    return CaseState(
        case_id="case_1",
        lead_id="lead_1",
        lead_reason="Test",
        hypotheses=[
            Hypothesis(hypothesis_id="hyp_1", statement="Test statement", scheme_type="kickback", status="open")
        ],
        evidence=[
            CaseEvidence(
                evidence_id="ev_1",
                statement="E1",
                direction="for",
                produced_by="det_1",
                source_refs=[EvidenceRef(source_table="invoices", record_id="inv_1")]
            ),
            CaseEvidence(
                evidence_id="ev_2",
                statement="E2",
                direction="for",
                produced_by="det_2",
                source_refs=[EvidenceRef(source_table="invoices", record_id="inv_1")]
            )
        ],
        actions_taken=[
            ActionRecord(step=1, action_name="get_vendor", reason="", result_summary="")
        ]
    )

@pytest.fixture
def available_actions():
    return [
        {"name": "get_vendor", "required_arguments": ["vendor_rfc"]}
    ]

def test_clean_case_returns_clear(base_case_state, available_actions):
    response = MethodCriticReview(
        target_hypothesis_id="hyp_1",
        outcome="clear",
        reasoning_summary="Everything looks methodologically sound.",
        referenced_evidence_ids=["ev_1"],
        referenced_action_steps=[1]
    ).model_dump_json()
    
    client = FakeLLMClient(response)
    review = criticize_method(client, base_case_state, "hyp_1", available_actions)
    
    assert review.outcome == "clear"
    # clear does NOT produce fraud/probable/proven authorization fields
    assert not hasattr(review, "authorized_confidence")

def test_evidence_dependencies_detected_deterministically(base_case_state):
    dependencies = find_evidence_dependencies(base_case_state)
    assert len(dependencies) == 1
    dep = dependencies[0]
    assert "ev_1" in dep["evidence_ids_involved"]
    assert "ev_2" in dep["evidence_ids_involved"]
    assert len(dep["shared_source_refs"]) == 1
    assert dep["shared_source_refs"][0]["source_table"] == "invoices"
    assert dep["shared_source_refs"][0]["record_id"] == "inv_1"
    assert "warning" in dep

def test_needs_more_work_requires_proposed_action():
    with pytest.raises(ValueError, match="needs_more_work requires at least one proposed_actions item"):
        MethodCriticReview(
            target_hypothesis_id="hyp_1",
            outcome="needs_more_work",
            reasoning_summary="Missing contract."
        )

def test_invented_action_name_rejected(base_case_state, available_actions):
    response = MethodCriticReview(
        target_hypothesis_id="hyp_1",
        outcome="needs_more_work",
        reasoning_summary="Check this.",
        proposed_actions=[ProposedAction(action_name="invented_action", arguments={}, reason="1", question_resolved="2")]
    ).model_dump_json()
    
    client = FakeLLMClient(response)
    with pytest.raises(ValueError, match="requested an unavailable action"):
        criticize_method(client, base_case_state, "hyp_1", available_actions)

def test_missing_required_arguments_rejected(base_case_state, available_actions):
    response = MethodCriticReview(
        target_hypothesis_id="hyp_1",
        outcome="needs_more_work",
        reasoning_summary="Check this.",
        proposed_actions=[ProposedAction(action_name="get_vendor", arguments={}, reason="1", question_resolved="2")]
    ).model_dump_json()
    
    client = FakeLLMClient(response)
    with pytest.raises(ValueError, match="missing required arguments"):
        criticize_method(client, base_case_state, "hyp_1", available_actions)

def test_nonexistent_evidence_id_rejected(base_case_state, available_actions):
    response = MethodCriticReview(
        target_hypothesis_id="hyp_1",
        outcome="clear",
        reasoning_summary="Ok.",
        referenced_evidence_ids=["fake_ev"]
    ).model_dump_json()
    
    client = FakeLLMClient(response)
    with pytest.raises(ValueError, match="Hallucinated evidence ID: fake_ev"):
        criticize_method(client, base_case_state, "hyp_1", available_actions)

def test_absence_in_estate_completeness_risk(base_case_state, available_actions):
    response = MethodCriticReview(
        target_hypothesis_id="hyp_1",
        outcome="needs_more_work",
        source_completeness_risks=["No contract in estate therefore no contract exists is invalid."],
        proposed_actions=[ProposedAction(action_name="get_vendor", arguments={"vendor_rfc": "123"}, reason="1", question_resolved="2")],
        reasoning_summary="Review."
    ).model_dump_json()
    
    client = FakeLLMClient(response)
    review = criticize_method(client, base_case_state, "hyp_1", available_actions)
    assert len(review.source_completeness_risks) == 1

def test_misuse_of_anomaly_score_risk(base_case_state, available_actions):
    response = MethodCriticReview(
        target_hypothesis_id="hyp_1",
        outcome="cannot_support",
        rule_misuse=["0.87 anomaly score treated as 87% fraud prob"],
        reasoning_summary="Bad assumption."
    ).model_dump_json()
    
    client = FakeLLMClient(response)
    review = criticize_method(client, base_case_state, "hyp_1", available_actions)
    assert review.outcome == "cannot_support"

def test_prompt_contains_no_invented_statutes(base_case_state, available_actions):
    prompt = build_method_critic_prompt(base_case_state, "hyp_1", available_actions)
    assert "NO INVENTED LAW" in prompt
    assert "absence in estate != absence in reality" in prompt

def test_target_hypothesis_id_must_exist(base_case_state, available_actions):
    response = MethodCriticReview(
        target_hypothesis_id="fake_hyp",
        outcome="clear",
        reasoning_summary="."
    ).model_dump_json()
    
    client = FakeLLMClient(response)
    with pytest.raises(ValueError, match="not found in case state"):
        criticize_method(client, base_case_state, "fake_hyp", available_actions)

def test_silent_switch_rejected(base_case_state, available_actions):
    # Valid hypothesis, but review returns a different one that is valid? Or just different than requested.
    base_case_state.hypotheses.append(Hypothesis(hypothesis_id="hyp_2", statement=".", scheme_type="kickback", status="open"))
    
    response = MethodCriticReview(
        target_hypothesis_id="hyp_2",
        outcome="clear",
        reasoning_summary="."
    ).model_dump_json()
    
    client = FakeLLMClient(response)
    with pytest.raises(ValueError, match="reviewed a different hypothesis than requested"):
        criticize_method(client, base_case_state, "hyp_1", available_actions)
