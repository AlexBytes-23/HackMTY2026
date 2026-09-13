from __future__ import annotations

import math

import pytest

from src.agents.challenger import ChallengerReview
from src.agents.method_critic import MethodCriticReview
from src.core.models import CaseState, InvestigationLoopResult
from src.gates.evidence_gate import EvidenceGateDecision
from src.investigation.postverification_pipeline import (
    PostVerificationBoundaryError,
    run_postverification_pipeline,
)
from src.investigation.preverification_pipeline import PreVerificationResult
from src.investigation.review_orchestrator import (
    ReviewOrchestrationResult,
    ReviewRound,
)
from src.verifier.official_verifier import VerificationReport
import src.investigation.postverification_pipeline as postverification


def _case() -> CaseState:
    return CaseState.model_construct(
        case_id="CASE-1",
        hypotheses=[],
        evidence=[],
    )


def _initial(case_state: CaseState) -> InvestigationLoopResult:
    return InvestigationLoopResult.model_construct(
        case_state=case_state,
        decisions=[],
        iterations=0,
        stop_reason="request_review",
    )


def _challenger() -> ChallengerReview:
    return ChallengerReview.model_construct(outcome="survives")


def _critic() -> MethodCriticReview:
    return MethodCriticReview.model_construct(outcome="clear")


def _round(
    number: int,
    target: str,
    *,
    challenger: ChallengerReview | None = None,
    critic: MethodCriticReview | None = None,
) -> ReviewRound:
    return ReviewRound.model_construct(
        round_number=number,
        target_hypothesis_id=target,
        challenger_review=challenger if challenger is not None else _challenger(),
        method_critic_review=critic,
        investigator_result=None,
    )


def _preverification(
    *,
    ready: bool,
    target: str | None,
    rounds: list[ReviewRound] | None = None,
    review_target: str | None = None,
    review_ready: bool | None = None,
    stop_reason: str | None = None,
) -> PreVerificationResult:
    case_state = _case()
    review = None
    if rounds is not None:
        resolved_review_target = review_target if review_target is not None else target
        review = ReviewOrchestrationResult.model_construct(
            case_state=case_state,
            target_hypothesis_id=resolved_review_target,
            rounds=rounds,
            review_rounds=len(rounds),
            ready_for_verification=ready if review_ready is None else review_ready,
            stop_reason=(
                "ready_for_verification"
                if (ready if review_ready is None else review_ready)
                else "max_review_rounds_reached"
            ),
        )

    return PreVerificationResult.model_construct(
        case_state=case_state,
        initial_investigation=_initial(case_state),
        review_result=review,
        target_hypothesis_id=target,
        ready_for_verification=ready,
        stop_reason=stop_reason or (
            "ready_for_verification" if ready else "review_blocked"
        ),
        detail=None,
    )


def test_not_ready_returns_blocked_without_calling_verifier_or_gate(monkeypatch):
    pre = _preverification(ready=False, target="H-1", rounds=None)

    class MustNotInstantiate:
        def __init__(self):
            raise AssertionError("Verifier must not run for blocked preverification.")

    monkeypatch.setattr(postverification, "OfficialVerifier", MustNotInstantiate)
    monkeypatch.setattr(
        postverification,
        "evaluate_gate",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("Gate must not run for blocked preverification.")
        ),
    )

    result = run_postverification_pipeline(
        pre,
        estate=object(),
        rule_registry=object(),
        claimed_amount=None,
        requested_rule_id=None,
    )

    assert result.status == "preverification_blocked"
    assert result.verification_report is None
    assert result.gate_decision is None


def test_ready_without_target_is_a_visible_contract_error():
    pre = _preverification(
        ready=True,
        target=None,
        rounds=[],
        review_target=None,
    )

    with pytest.raises(PostVerificationBoundaryError, match="target_hypothesis_id"):
        run_postverification_pipeline(
            pre,
            estate=object(),
            rule_registry=object(),
        )


def test_ready_with_inconsistent_stop_reason_is_a_visible_contract_error():
    pre = _preverification(
        ready=True,
        target="H-1",
        rounds=[_round(1, "H-1", critic=_critic())],
        stop_reason="review_blocked",
    )

    with pytest.raises(PostVerificationBoundaryError, match="stop_reason"):
        run_postverification_pipeline(
            pre,
            estate=object(),
            rule_registry=object(),
        )


def test_ready_without_review_result_is_a_visible_contract_error():
    pre = _preverification(ready=True, target="H-1", rounds=None)

    with pytest.raises(PostVerificationBoundaryError, match="review_result"):
        run_postverification_pipeline(
            pre,
            estate=object(),
            rule_registry=object(),
        )


def test_review_target_must_match_final_preverification_target():
    pre = _preverification(
        ready=True,
        target="H-1",
        rounds=[_round(1, "H-2", critic=_critic())],
        review_target="H-2",
    )

    with pytest.raises(PostVerificationBoundaryError, match="does not match"):
        run_postverification_pipeline(
            pre,
            estate=object(),
            rule_registry=object(),
        )


def test_matching_round_requires_method_critic_review():
    pre = _preverification(
        ready=True,
        target="H-1",
        rounds=[_round(1, "H-1", critic=None)],
    )

    with pytest.raises(PostVerificationBoundaryError, match="No reviewed round"):
        run_postverification_pipeline(
            pre,
            estate=object(),
            rule_registry=object(),
        )


@pytest.mark.parametrize("amount", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_invalid_claimed_amount_is_rejected_as_input_contract_error(amount):
    pre = _preverification(
        ready=True,
        target="H-1",
        rounds=[_round(1, "H-1", critic=_critic())],
    )

    with pytest.raises(PostVerificationBoundaryError, match="finite and strictly positive"):
        run_postverification_pipeline(
            pre,
            estate=object(),
            rule_registry=object(),
            claimed_amount=amount,
        )


def test_pipeline_selects_latest_matching_review_and_forwards_explicit_inputs(monkeypatch):
    first_challenger = _challenger()
    first_critic = _critic()
    final_challenger = _challenger()
    final_critic = _critic()

    rounds = [
        _round(
            1,
            "H-1",
            challenger=first_challenger,
            critic=first_critic,
        ),
        _round(2, "H-2", critic=_critic()),
        _round(
            3,
            "H-1",
            challenger=final_challenger,
            critic=final_critic,
        ),
    ]
    pre = _preverification(
        ready=True,
        target="H-1",
        rounds=rounds,
        review_target="H-1",
    )

    captured = {}

    report = VerificationReport.model_construct(
        target_hypothesis_id="H-1",
        checks=[],
        resolved_exhibits=[],
        missing_exhibits=[],
        duplicate_exhibits=[],
        reconciliation=None,
        critical_failures=[],
        unresolved_critical_checks=[],
        internal_errors=[],
    )

    class FakeVerifier:
        def verify(self, **kwargs):
            captured["verify"] = kwargs
            return report

    gate_decision = EvidenceGateDecision.model_construct(
        target_hypothesis_id="H-1",
        outcome="inconclusive",
        authorized_confidence=None,
        satisfied_requirements=[],
        failed_requirements=["test"],
        unresolved_material_questions=[],
        reason="test",
    )

    def fake_gate(**kwargs):
        captured["gate"] = kwargs
        return gate_decision

    monkeypatch.setattr(postverification, "OfficialVerifier", FakeVerifier)
    monkeypatch.setattr(postverification, "evaluate_gate", fake_gate)

    result = run_postverification_pipeline(
        pre,
        estate="ESTATE",
        rule_registry="RULES",
        claimed_amount=1250.50,
        requested_rule_id="RULE-1",
        useful_actions_remain=True,
    )

    assert result.status == "evaluated"
    assert result.selected_review_round.round_number == 3
    assert result.claimed_amount == 1250.50
    assert result.requested_rule_id == "RULE-1"
    assert result.verification_report is report
    assert result.gate_decision is gate_decision

    assert captured["verify"]["case_state"] is pre.case_state
    assert captured["verify"]["target_hypothesis_id"] == "H-1"
    assert captured["verify"]["estate"] == "ESTATE"
    assert captured["verify"]["claimed_amount"] == 1250.50

    assert captured["gate"]["case_state"] is pre.case_state
    assert captured["gate"]["target_hypothesis_id"] == "H-1"
    assert captured["gate"]["challenger_review"] is final_challenger
    assert captured["gate"]["method_critic_review"] is final_critic
    assert captured["gate"]["verification_report"] is report
    assert captured["gate"]["rule_registry"] == "RULES"
    assert captured["gate"]["requested_rule_id"] == "RULE-1"
    assert captured["gate"]["useful_actions_remain"] is True


def test_missing_claim_and_rule_are_forwarded_without_being_invented(monkeypatch):
    pre = _preverification(
        ready=True,
        target="H-1",
        rounds=[_round(1, "H-1", critic=_critic())],
    )
    captured = {}

    report = VerificationReport.model_construct(
        target_hypothesis_id="H-1",
        checks=[],
        resolved_exhibits=[],
        missing_exhibits=[],
        duplicate_exhibits=[],
        reconciliation=None,
        critical_failures=[],
        unresolved_critical_checks=["PESO-RECONCILIATION"],
        internal_errors=[],
    )

    class FakeVerifier:
        def verify(self, **kwargs):
            captured["amount"] = kwargs["claimed_amount"]
            return report

    def fake_gate(**kwargs):
        captured["rule"] = kwargs["requested_rule_id"]
        return EvidenceGateDecision.model_construct(
            target_hypothesis_id="H-1",
            outcome="inconclusive",
            authorized_confidence=None,
            satisfied_requirements=[],
            failed_requirements=["missing claim/rule"],
            unresolved_material_questions=[],
            reason="missing",
        )

    monkeypatch.setattr(postverification, "OfficialVerifier", FakeVerifier)
    monkeypatch.setattr(postverification, "evaluate_gate", fake_gate)

    result = run_postverification_pipeline(
        pre,
        estate=object(),
        rule_registry=object(),
        claimed_amount=None,
        requested_rule_id=None,
    )

    assert captured["amount"] is None
    assert captured["rule"] is None
    assert result.claimed_amount is None
    assert result.requested_rule_id is None
    assert result.gate_decision.outcome == "inconclusive"
