from __future__ import annotations

import pytest

from src.core.models import CaseState, Hypothesis, InvestigationLoopResult
from src.investigation.preverification_pipeline import run_preverification_pipeline
from src.investigation.review_orchestrator import ReviewOrchestrationResult


class DummyEstate:
    pass


class DummyLLM:
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("DummyLLM should not be called in patched pipeline tests")


def _case(*, status: str = "open", hypotheses: list[Hypothesis] | None = None) -> CaseState:
    return CaseState(
        case_id="CASE-1",
        lead_id="LEAD-1",
        hypotheses=(
            hypotheses
            if hypotheses is not None
            else [
                Hypothesis(
                    hypothesis_id="H-1",
                    statement="Possible kickback",
                    scheme_type="kickback",
                    status="open",
                )
            ]
        ),
        status=status,
    )


def _investigation_result(case: CaseState, stop_reason: str) -> InvestigationLoopResult:
    return InvestigationLoopResult(
        case_state=case,
        decisions=[],
        iterations=1,
        stop_reason=stop_reason,
    )


def _review_result(case: CaseState, ready: bool, stop_reason: str = "ready_for_verification"):
    if ready:
        case = case.model_copy(deep=True)
        case.status = "ready_for_verification"
    return ReviewOrchestrationResult(
        case_state=case,
        target_hypothesis_id="H-1",
        rounds=[],
        review_rounds=1,
        ready_for_verification=ready,
        stop_reason=stop_reason,
    )


def test_request_review_flows_into_review_orchestrator(monkeypatch):
    state = _case(status="ready_for_review")
    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_investigation_loop",
        lambda **kwargs: _investigation_result(state, "request_review"),
    )
    seen = {}

    def fake_review(**kwargs):
        seen["target"] = kwargs["target_hypothesis_id"]
        return _review_result(kwargs["case_state"], True)

    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_review_orchestration",
        fake_review,
    )

    result = run_preverification_pipeline(_case(), DummyEstate(), DummyLLM())

    assert seen["target"] == "H-1"
    assert result.ready_for_verification is True
    assert result.stop_reason == "ready_for_verification"
    assert result.review_result is not None


def test_investigator_ready_for_verification_cannot_bypass_reviews(monkeypatch):
    state = _case(status="ready_for_verification")
    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_investigation_loop",
        lambda **kwargs: _investigation_result(state, "ready_for_verification"),
    )
    called = {"review": False}

    def fake_review(**kwargs):
        called["review"] = True
        assert kwargs["case_state"].status == "ready_for_review"
        return _review_result(kwargs["case_state"], True)

    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_review_orchestration",
        fake_review,
    )

    result = run_preverification_pipeline(_case(), DummyEstate(), DummyLLM())

    assert called["review"] is True
    assert result.ready_for_verification is True


@pytest.mark.parametrize(
    ("investigator_stop", "expected"),
    [
        ("close_inconclusive", "investigator_closed_inconclusive"),
        ("repeated_action", "investigator_repeated_action"),
        ("max_steps_reached", "investigator_budget_exhausted"),
    ],
)
def test_investigator_terminal_stops_do_not_call_review(
    monkeypatch, investigator_stop, expected
):
    state = _case(status="closed" if investigator_stop == "close_inconclusive" else "investigating")
    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_investigation_loop",
        lambda **kwargs: _investigation_result(state, investigator_stop),
    )
    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_review_orchestration",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("review should not run")),
    )

    result = run_preverification_pipeline(_case(), DummyEstate(), DummyLLM())

    assert result.ready_for_verification is False
    assert result.stop_reason == expected
    assert result.review_result is None


def test_no_live_hypothesis_stops_before_review(monkeypatch):
    state = _case(
        status="ready_for_review",
        hypotheses=[
            Hypothesis(
                hypothesis_id="H-1",
                statement="Rejected theory",
                scheme_type="kickback",
                status="rejected",
            )
        ],
    )
    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_investigation_loop",
        lambda **kwargs: _investigation_result(state, "request_review"),
    )
    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_review_orchestration",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("review should not run")),
    )

    result = run_preverification_pipeline(state, DummyEstate(), DummyLLM())

    assert result.stop_reason == "no_reviewable_hypothesis"
    assert result.ready_for_verification is False


def test_supported_hypothesis_is_preferred_over_open(monkeypatch):
    state = _case(
        status="ready_for_review",
        hypotheses=[
            Hypothesis(hypothesis_id="H-open", statement="Open", status="open"),
            Hypothesis(
                hypothesis_id="H-supported",
                statement="Supported",
                scheme_type="phantom_vendor",
                status="supported",
            ),
        ],
    )
    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_investigation_loop",
        lambda **kwargs: _investigation_result(state, "request_review"),
    )

    def fake_review(**kwargs):
        assert kwargs["target_hypothesis_id"] == "H-supported"
        case = kwargs["case_state"].model_copy(deep=True)
        case.status = "ready_for_verification"
        return ReviewOrchestrationResult(
            case_state=case,
            target_hypothesis_id="H-supported",
            rounds=[],
            review_rounds=1,
            ready_for_verification=True,
            stop_reason="ready_for_verification",
        )

    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_review_orchestration",
        fake_review,
    )

    result = run_preverification_pipeline(state, DummyEstate(), DummyLLM())

    assert result.target_hypothesis_id == "H-supported"
    assert result.ready_for_verification is True


def test_preferred_hypothesis_must_be_live(monkeypatch):
    state = _case(
        status="ready_for_review",
        hypotheses=[
            Hypothesis(
                hypothesis_id="H-1",
                statement="Rejected",
                scheme_type="kickback",
                status="rejected",
            )
        ],
    )
    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_investigation_loop",
        lambda **kwargs: _investigation_result(state, "request_review"),
    )

    with pytest.raises(ValueError, match="not an open/supported hypothesis"):
        run_preverification_pipeline(
            state,
            DummyEstate(),
            DummyLLM(),
            preferred_hypothesis_id="H-1",
        )


def test_review_blocker_is_preserved_as_detail(monkeypatch):
    state = _case(status="ready_for_review")
    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_investigation_loop",
        lambda **kwargs: _investigation_result(state, "request_review"),
    )
    monkeypatch.setattr(
        "src.investigation.preverification_pipeline.run_review_orchestration",
        lambda **kwargs: _review_result(
            kwargs["case_state"], False, "method_cannot_support"
        ),
    )

    result = run_preverification_pipeline(_case(), DummyEstate(), DummyLLM())

    assert result.ready_for_verification is False
    assert result.stop_reason == "review_blocked"
    assert result.detail == "method_cannot_support"
