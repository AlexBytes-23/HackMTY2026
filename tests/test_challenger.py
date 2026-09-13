import json

import pytest

from src.agents.challenger import (
    build_challenger_prompt,
    challenge_case,
)

from src.core.models import (
    EvidenceRef,
    Hypothesis,
    Observation,
)

from src.investigation.action_bank import (
    list_available_actions,
)

from src.investigation.case_builder import (
    build_case_state,
)

from src.investigation.lead_builder import (
    build_leads,
)


class FakeLLM:
    def __init__(self, payload: dict):
        self.payload = payload

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        return json.dumps(self.payload)


def make_case():

    observation = Observation(
        observation_id="OBS-CH-001",
        detector_name="tabular_detector",
        signal_type="repeated_payments",
        entities=["RFC:TEST010101AAA"],
        statement="Repeated vendor payments detected.",
        evidence=[
            EvidenceRef(
                source_table="bank_txns",
                record_id="TXN-001",
            )
        ],
        facts={
            "payment_count": 8,
        },
        limitations=[
            "Repeated payments may have legitimate explanations."
        ],
        legitimate_alternatives=[
            "Recurring contractual payment."
        ],
        recommended_checks=[
            "Check vendor contracts."
        ],
    )

    lead = build_leads([observation])[0]

    case = build_case_state(
        lead=lead,
        observations=[observation],
    )

    case.hypotheses.append(
        Hypothesis(
            hypothesis_id="H-001",
            statement=(
                "The repeated payments may represent "
                "threshold splitting."
            ),
            scheme_type="threshold_splitting",
            status="open",
            supporting_evidence_ids=[
                "EV-OBS-CH-001",
            ],
            counter_evidence_ids=[],
            unresolved_questions=[
                "Is there a recurring contract?"
            ],
        )
    )

    return case


def test_challenger_requests_real_action():

    case = make_case()

    payload = {
        "target_hypothesis_id": "H-001",
        "strongest_legitimate_alternative": (
            "A recurring contract could explain the payments."
        ),
        "unsupported_claims": [],
        "missing_counterevidence": [
            {
                "question": "Does a recurring vendor contract exist?",
                "why_it_matters": (
                    "A contract could explain the repeated payments."
                ),
            }
        ],
        "contradictions": [],
        "proposed_actions": [
            {
                "action_name": "get_vendor_contracts",
                "arguments": {
                    "vendor_rfc": "TEST010101AAA",
                },
                "reason": (
                    "Check whether the repeated payments "
                    "have contractual support."
                ),
                "question_resolved": (
                    "Does a recurring vendor contract exist?"
                ),
            }
        ],
        "alternative_hypotheses": [],
        "outcome": "needs_more_evidence",
        "reasoning_summary": (
            "The current pattern is insufficient to distinguish "
            "threshold splitting from recurring contracted payments."
        ),
    }

    review = challenge_case(
        case_state=case,
        target_hypothesis_id="H-001",
        available_actions=list_available_actions(),
        llm_client=FakeLLM(payload),
    )

    assert review.outcome == "needs_more_evidence"

    assert (
        review.proposed_actions[0].action_name
        == "get_vendor_contracts"
    )


def test_challenger_rejects_invented_action():

    case = make_case()

    payload = {
        "target_hypothesis_id": "H-001",
        "strongest_legitimate_alternative": None,
        "unsupported_claims": [],
        "missing_counterevidence": [
            {
                "question": "Is there hidden evidence?",
                "why_it_matters": "It could change the interpretation.",
            }
        ],
        "contradictions": [],
        "proposed_actions": [
            {
                "action_name": "search_secret_sat_database",
                "arguments": {},
                "reason": "Invented tool.",
                "question_resolved": "Unknown.",
            }
        ],
        "alternative_hypotheses": [],
        "outcome": "needs_more_evidence",
        "reasoning_summary": "More evidence is required.",
    }

    with pytest.raises(
        ValueError,
        match="unavailable action",
    ):
        challenge_case(
            case_state=case,
            target_hypothesis_id="H-001",
            available_actions=list_available_actions(),
            llm_client=FakeLLM(payload),
        )


def test_challenger_can_open_alternative_hypothesis():

    case = make_case()

    payload = {
        "target_hypothesis_id": "H-001",
        "strongest_legitimate_alternative": None,
        "unsupported_claims": [],
        "missing_counterevidence": [],
        "contradictions": [],
        "proposed_actions": [],
        "alternative_hypotheses": [
            {
                "statement": (
                    "The payment pattern may instead support "
                    "a kickback hypothesis."
                ),
                "scheme_type": "kickback",
                "reason": (
                    "A different financial relationship may "
                    "better explain the evidence."
                ),
                "supporting_evidence_ids": [
                    "EV-OBS-CH-001",
                ],
            }
        ],
        "outcome": "alternative_hypothesis",
        "reasoning_summary": (
            "The original mechanism is weak, but another "
            "mechanism deserves investigation."
        ),
    }

    review = challenge_case(
        case_state=case,
        target_hypothesis_id="H-001",
        available_actions=list_available_actions(),
        llm_client=FakeLLM(payload),
    )

    assert review.outcome == "alternative_hypothesis"

    assert (
        review.alternative_hypotheses[0].scheme_type
        == "kickback"
    )


def test_challenger_rejects_fake_evidence_id():

    case = make_case()

    payload = {
        "target_hypothesis_id": "H-001",
        "strongest_legitimate_alternative": None,
        "unsupported_claims": [],
        "missing_counterevidence": [],
        "contradictions": [
            {
                "statement": "Contradictory evidence exists.",
                "evidence_ids": ["EV-DOES-NOT-EXIST"],
                "why_it_conflicts": (
                    "This supposedly conflicts with H-001."
                ),
            }
        ],
        "proposed_actions": [],
        "alternative_hypotheses": [],
        "outcome": "survives",
        "reasoning_summary": "Review completed.",
    }

    with pytest.raises(
        ValueError,
        match="nonexistent evidence",
    ):
        challenge_case(
            case_state=case,
            target_hypothesis_id="H-001",
            available_actions=list_available_actions(),
            llm_client=FakeLLM(payload),
        )


def test_challenger_prompt_has_epistemic_guardrails():

    case = make_case()

    system_prompt, user_prompt = build_challenger_prompt(
        case_state=case,
        target_hypothesis_id="H-001",
        available_actions=list_available_actions(),
    )

    assert "DO NOT INVENT LAW OR POLICY" in system_prompt

    assert "DO NOT PRESUME FRAUD OR LEGITIMACY" in system_prompt

    assert "EV-OBS-CH-001" in user_prompt

    assert "H-001" in user_prompt