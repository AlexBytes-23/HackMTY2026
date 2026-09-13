from src.agents.challenger import (
    AlternativeHypothesis,
    ChallengerReview,
    MissingCounterevidence,
)
from src.agents.method_critic import MethodCriticResult, MethodCriticReview
from src.core.models import (
    CaseState,
    Hypothesis,
    InvestigationLoopResult,
    InvestigatorDecision,
    ProposedAction,
)
from src.investigation.review_orchestrator import run_review_orchestration


class DummyLLM:
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("LLM should be monkeypatched in orchestration tests")


class DummyEstate:
    pass


def _case(*, scheme_type="kickback", status="open"):
    return CaseState(
        case_id="CASE-1",
        lead_id="LEAD-1",
        hypotheses=[
            Hypothesis(
                hypothesis_id="H-1",
                statement="Possible improper transfer.",
                scheme_type=scheme_type,
                status=status,
            )
        ],
        status="ready_for_review",
    )


def _survives():
    return ChallengerReview(
        target_hypothesis_id="H-1",
        outcome="survives",
        reasoning_summary="No material adversarial objection remains.",
    )


def _method_clear(target="H-1"):
    return MethodCriticResult(
        review=MethodCriticReview(
            target_hypothesis_id=target,
            outcome="clear",
            reasoning_summary="Method is defensible.",
        ),
        deterministic_dependencies=[],
    )


def test_survives_and_method_clear_is_ready(monkeypatch):
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.challenge_case",
        lambda **kwargs: _survives(),
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.criticize_method",
        lambda **kwargs: _method_clear(),
    )

    result = run_review_orchestration(
        _case(), "H-1", DummyEstate(), DummyLLM()
    )

    assert result.ready_for_verification is True
    assert result.stop_reason == "ready_for_verification"
    assert result.case_state.status == "ready_for_verification"
    assert len(result.rounds) == 1
    assert result.rounds[0].method_critic_review.outcome == "clear"


def test_rejected_hypothesis_stops_before_reviews(monkeypatch):
    def fail(**kwargs):
        raise AssertionError("No reviewer should run for a rejected hypothesis")

    monkeypatch.setattr(
        "src.investigation.review_orchestrator.challenge_case", fail
    )

    result = run_review_orchestration(
        _case(status="rejected"), "H-1", DummyEstate(), DummyLLM()
    )

    assert result.ready_for_verification is False
    assert result.stop_reason == "hypothesis_rejected"
    assert result.review_rounds == 0


def test_legitimate_alternative_blocks_verification(monkeypatch):
    review = ChallengerReview(
        target_hypothesis_id="H-1",
        outcome="legitimate_alternative",
        strongest_legitimate_alternative="Documented reimbursement could explain it.",
        reasoning_summary="Legitimate explanation remains unresolved.",
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.challenge_case",
        lambda **kwargs: review,
    )

    result = run_review_orchestration(
        _case(), "H-1", DummyEstate(), DummyLLM()
    )

    assert result.ready_for_verification is False
    assert result.stop_reason == "legitimate_alternative_unresolved"
    assert result.case_state.hypotheses[0].status == "inconclusive"


def test_method_cannot_support_blocks_verification(monkeypatch):
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.challenge_case",
        lambda **kwargs: _survives(),
    )
    result_obj = MethodCriticResult(
        review=MethodCriticReview(
            target_hypothesis_id="H-1",
            outcome="cannot_support",
            material_blockers=["Entity ownership cannot be resolved from available sources."],
            reasoning_summary="Material blocker remains.",
        ),
        deterministic_dependencies=[],
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.criticize_method",
        lambda **kwargs: result_obj,
    )

    result = run_review_orchestration(
        _case(), "H-1", DummyEstate(), DummyLLM()
    )

    assert result.ready_for_verification is False
    assert result.stop_reason == "method_cannot_support"
    assert result.case_state.hypotheses[0].status == "inconclusive"


def test_missing_scheme_never_reaches_verification(monkeypatch):
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.challenge_case",
        lambda **kwargs: _survives(),
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.criticize_method",
        lambda **kwargs: _method_clear(),
    )

    result = run_review_orchestration(
        _case(scheme_type=None), "H-1", DummyEstate(), DummyLLM()
    )

    assert result.ready_for_verification is False
    assert result.stop_reason == "scheme_type_missing"


def test_challenger_more_evidence_returns_to_investigator(monkeypatch):
    calls = {"challenge": 0, "investigate": 0}

    def fake_challenge(**kwargs):
        calls["challenge"] += 1
        if calls["challenge"] == 1:
            return ChallengerReview(
                target_hypothesis_id="H-1",
                outcome="needs_more_evidence",
                missing_counterevidence=[
                    MissingCounterevidence(
                        question="Does a contract explain the transfer?",
                        why_it_matters="It could materially weaken the hypothesis.",
                    )
                ],
                proposed_actions=[
                    ProposedAction(
                        action_name="get_vendor_contracts",
                        arguments={"vendor_rfc": "AAA010101AAA"},
                        reason="Check support.",
                        question_resolved="Does a contract explain the transfer?",
                    )
                ],
                reasoning_summary="More evidence required.",
            )
        return _survives()

    def fake_investigation(**kwargs):
        calls["investigate"] += 1
        state = kwargs["case_state"].model_copy(deep=True)
        state.status = "ready_for_review"
        return InvestigationLoopResult(
            case_state=state,
            decisions=[
                InvestigatorDecision(
                    decision="request_review",
                    current_assessment="Follow-up complete.",
                    hypotheses=state.hypotheses,
                    reason="Return to review.",
                )
            ],
            iterations=1,
            stop_reason="request_review",
        )

    monkeypatch.setattr(
        "src.investigation.review_orchestrator.challenge_case", fake_challenge
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.run_investigation_loop",
        fake_investigation,
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.criticize_method",
        lambda **kwargs: _method_clear(),
    )

    result = run_review_orchestration(
        _case(),
        "H-1",
        DummyEstate(),
        DummyLLM(),
        max_review_rounds=3,
    )

    assert calls == {"challenge": 2, "investigate": 1}
    assert result.ready_for_verification is True
    assert result.stop_reason == "ready_for_verification"
    assert "Does a contract explain the transfer?" in result.case_state.unknowns
    assert len(result.rounds) == 2


def test_method_more_work_returns_to_investigator(monkeypatch):
    method_calls = {"count": 0}

    monkeypatch.setattr(
        "src.investigation.review_orchestrator.challenge_case",
        lambda **kwargs: _survives(),
    )

    def fake_method(**kwargs):
        method_calls["count"] += 1
        if method_calls["count"] == 1:
            return MethodCriticResult(
                review=MethodCriticReview(
                    target_hypothesis_id="H-1",
                    outcome="needs_more_work",
                    proposed_actions=[
                        ProposedAction(
                            action_name="get_vendor",
                            arguments={"vendor_rfc": "AAA010101AAA"},
                            reason="Resolve entity context.",
                            question_resolved="Who is the referenced vendor?",
                        )
                    ],
                    reasoning_summary="Entity context is incomplete.",
                ),
                deterministic_dependencies=[],
            )
        return _method_clear()

    def fake_investigation(**kwargs):
        state = kwargs["case_state"].model_copy(deep=True)
        state.status = "ready_for_review"
        return InvestigationLoopResult(
            case_state=state,
            decisions=[],
            iterations=1,
            stop_reason="request_review",
        )

    monkeypatch.setattr(
        "src.investigation.review_orchestrator.criticize_method", fake_method
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.run_investigation_loop",
        fake_investigation,
    )

    result = run_review_orchestration(
        _case(), "H-1", DummyEstate(), DummyLLM(), max_review_rounds=3
    )

    assert result.ready_for_verification is True
    assert method_calls["count"] == 2
    assert "Who is the referenced vendor?" in result.case_state.unknowns


def test_alternative_hypothesis_is_preserved_and_investigated(monkeypatch):
    calls = {"challenge": 0}

    def fake_challenge(**kwargs):
        calls["challenge"] += 1
        if calls["challenge"] == 1:
            return ChallengerReview(
                target_hypothesis_id="H-1",
                outcome="alternative_hypothesis",
                alternative_hypotheses=[
                    AlternativeHypothesis(
                        statement="The pattern may instead be round tripping.",
                        scheme_type="round_tripping",
                        reason="Cycle-shaped transfers are present.",
                    )
                ],
                reasoning_summary="A materially different suspicious mechanism exists.",
            )
        return ChallengerReview(
            target_hypothesis_id=kwargs["target_hypothesis_id"],
            outcome="survives",
            reasoning_summary="No material objection remains.",
        )

    def fake_investigation(**kwargs):
        state = kwargs["case_state"].model_copy(deep=True)
        # Investigator rejects the original hypothesis, leaving the alternative open.
        for hypothesis in state.hypotheses:
            if hypothesis.hypothesis_id == "H-1":
                hypothesis.status = "rejected"
        state.status = "ready_for_review"
        return InvestigationLoopResult(
            case_state=state,
            decisions=[],
            iterations=1,
            stop_reason="request_review",
        )

    monkeypatch.setattr(
        "src.investigation.review_orchestrator.challenge_case", fake_challenge
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.run_investigation_loop",
        fake_investigation,
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.criticize_method",
        lambda **kwargs: _method_clear(kwargs["target_hypothesis_id"]),
    )

    result = run_review_orchestration(
        _case(), "H-1", DummyEstate(), DummyLLM(), max_review_rounds=3
    )

    assert result.ready_for_verification is True
    assert result.target_hypothesis_id.startswith("H-1-ALT-01")
    assert any(
        h.hypothesis_id.startswith("H-1-ALT-01")
        and h.scheme_type == "round_tripping"
        for h in result.case_state.hypotheses
    )
    assert any(h.hypothesis_id == "H-1" and h.status == "rejected" for h in result.case_state.hypotheses)


def test_investigator_repeated_action_stops_orchestration(monkeypatch):
    review = ChallengerReview(
        target_hypothesis_id="H-1",
        outcome="needs_more_evidence",
        missing_counterevidence=[
            MissingCounterevidence(question="Need one more check", why_it_matters="Material")
        ],
        proposed_actions=[
            ProposedAction(
                action_name="get_vendor",
                arguments={"vendor_rfc": "AAA010101AAA"},
                reason="Check vendor",
                question_resolved="Need one more check",
            )
        ],
        reasoning_summary="More evidence required.",
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.challenge_case",
        lambda **kwargs: review,
    )

    def fake_investigation(**kwargs):
        return InvestigationLoopResult(
            case_state=kwargs["case_state"],
            decisions=[],
            iterations=1,
            stop_reason="repeated_action",
        )

    monkeypatch.setattr(
        "src.investigation.review_orchestrator.run_investigation_loop",
        fake_investigation,
    )

    result = run_review_orchestration(
        _case(), "H-1", DummyEstate(), DummyLLM()
    )

    assert result.ready_for_verification is False
    assert result.stop_reason == "investigator_repeated_action"


def test_max_review_rounds_is_hard_stop(monkeypatch):
    review = ChallengerReview(
        target_hypothesis_id="H-1",
        outcome="needs_more_evidence",
        missing_counterevidence=[
            MissingCounterevidence(question="Need another check", why_it_matters="Material")
        ],
        proposed_actions=[
            ProposedAction(
                action_name="get_vendor",
                arguments={"vendor_rfc": "AAA010101AAA"},
                reason="Check",
                question_resolved="Need another check",
            )
        ],
        reasoning_summary="More evidence required.",
    )
    monkeypatch.setattr(
        "src.investigation.review_orchestrator.challenge_case",
        lambda **kwargs: review,
    )

    def fake_investigation(**kwargs):
        state = kwargs["case_state"].model_copy(deep=True)
        state.status = "ready_for_review"
        return InvestigationLoopResult(
            case_state=state,
            decisions=[],
            iterations=1,
            stop_reason="request_review",
        )

    monkeypatch.setattr(
        "src.investigation.review_orchestrator.run_investigation_loop",
        fake_investigation,
    )

    result = run_review_orchestration(
        _case(),
        "H-1",
        DummyEstate(),
        DummyLLM(),
        max_review_rounds=2,
    )

    assert result.ready_for_verification is False
    assert result.stop_reason == "max_review_rounds_reached"
    assert result.review_rounds == 2
