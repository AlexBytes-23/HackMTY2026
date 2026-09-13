from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.agents.challenger import ChallengerReview, challenge_case
from src.agents.method_critic import MethodCriticReview, criticize_method
from src.core.estate import EstateRepository
from src.core.models import CaseState, Hypothesis, InvestigationLoopResult
from src.investigation.action_bank import list_available_actions
from src.investigation.investigator import LLMClient
from src.investigation.runner import run_investigation_loop


ReviewStopReason = Literal[
    "ready_for_verification",
    "legitimate_alternative_unresolved",
    "method_cannot_support",
    "hypothesis_rejected",
    "no_reviewable_hypothesis",
    "scheme_type_missing",
    "investigator_closed_inconclusive",
    "investigator_repeated_action",
    "investigator_budget_exhausted",
    "max_review_rounds_reached",
]


class ReviewRound(BaseModel):
    round_number: int
    target_hypothesis_id: str
    challenger_review: ChallengerReview
    method_critic_review: MethodCriticReview | None = None
    investigator_result: InvestigationLoopResult | None = None


class ReviewOrchestrationResult(BaseModel):
    case_state: CaseState
    target_hypothesis_id: str
    rounds: list[ReviewRound] = Field(default_factory=list)
    review_rounds: int
    ready_for_verification: bool
    stop_reason: ReviewStopReason


def _find_hypothesis(case_state: CaseState, hypothesis_id: str) -> Hypothesis:
    for hypothesis in case_state.hypotheses:
        if hypothesis.hypothesis_id == hypothesis_id:
            return hypothesis
    raise ValueError(
        f"Hypothesis {hypothesis_id!r} does not exist in case {case_state.case_id!r}."
    )


def _append_unknown(case_state: CaseState, question: str | None) -> None:
    if not question:
        return
    normalized = question.strip()
    if normalized and normalized not in case_state.unknowns:
        case_state.unknowns.append(normalized)


def _absorb_challenger_questions(
    case_state: CaseState,
    review: ChallengerReview,
) -> None:
    for item in review.missing_counterevidence:
        _append_unknown(case_state, item.question)
    for action in review.proposed_actions:
        _append_unknown(case_state, action.question_resolved)


def _absorb_method_questions(
    case_state: CaseState,
    review: MethodCriticReview,
) -> None:
    for action in review.proposed_actions:
        _append_unknown(case_state, action.question_resolved)


def _mark_hypothesis_inconclusive(
    case_state: CaseState,
    hypothesis_id: str,
) -> None:
    hypothesis = _find_hypothesis(case_state, hypothesis_id)
    if hypothesis.status != "rejected":
        hypothesis.status = "inconclusive"


def _add_alternative_hypotheses(
    case_state: CaseState,
    target_hypothesis_id: str,
    review: ChallengerReview,
) -> list[str]:
    existing_ids = {h.hypothesis_id for h in case_state.hypotheses}
    created_ids: list[str] = []

    for idx, alt in enumerate(review.alternative_hypotheses, start=1):
        base_id = f"{target_hypothesis_id}-ALT-{idx:02d}"
        candidate = base_id
        suffix = 2
        while candidate in existing_ids:
            candidate = f"{base_id}-{suffix}"
            suffix += 1

        case_state.hypotheses.append(
            Hypothesis(
                hypothesis_id=candidate,
                statement=alt.statement,
                scheme_type=alt.scheme_type,
                status="open",
                supporting_evidence_ids=alt.supporting_evidence_ids.copy(),
                unresolved_questions=[],
            )
        )
        existing_ids.add(candidate)
        created_ids.append(candidate)

    return created_ids


def _select_review_target(
    case_state: CaseState,
    preferred_id: str,
    alternative_ids: list[str],
) -> str | None:
    preferred = _find_hypothesis(case_state, preferred_id)
    if preferred.status in {"open", "supported"}:
        return preferred_id

    by_id = {h.hypothesis_id: h for h in case_state.hypotheses}
    for hypothesis_id in alternative_ids:
        hypothesis = by_id.get(hypothesis_id)
        if hypothesis and hypothesis.status in {"open", "supported"}:
            return hypothesis_id

    for hypothesis in case_state.hypotheses:
        if hypothesis.status in {"open", "supported"}:
            return hypothesis.hypothesis_id

    return None


def _run_followup_investigation(
    case_state: CaseState,
    estate: EstateRepository,
    llm_client: LLMClient,
    max_investigation_steps: int,
) -> InvestigationLoopResult:
    case_state.status = "investigating"
    return run_investigation_loop(
        case_state=case_state,
        estate=estate,
        llm_client=llm_client,
        max_steps=max_investigation_steps,
    )


def _stop_from_investigator(
    result: InvestigationLoopResult,
) -> ReviewStopReason | None:
    if result.stop_reason == "close_inconclusive":
        return "investigator_closed_inconclusive"
    if result.stop_reason == "repeated_action":
        return "investigator_repeated_action"
    if result.stop_reason == "max_steps_reached":
        return "investigator_budget_exhausted"
    return None


def run_review_orchestration(
    case_state: CaseState,
    target_hypothesis_id: str,
    estate: EstateRepository,
    llm_client: LLMClient,
    *,
    max_review_rounds: int = 3,
    max_investigation_steps: int = 3,
    rule_context: list | None = None,
) -> ReviewOrchestrationResult:
    """
    Coordinate Challenger -> follow-up investigation -> Method Critic.

    This layer does not verify facts and does not authorize fraud findings.
    It only decides whether one hypothesis is ready to be handed to the
    deterministic verification stage.
    """
    if max_review_rounds < 1:
        raise ValueError("max_review_rounds must be at least 1.")
    if max_investigation_steps < 1:
        raise ValueError("max_investigation_steps must be at least 1.")

    current = case_state.model_copy(deep=True)
    active_target = target_hypothesis_id
    _find_hypothesis(current, active_target)

    available_actions = list_available_actions()
    rounds: list[ReviewRound] = []

    for round_number in range(1, max_review_rounds + 1):
        target = _find_hypothesis(current, active_target)
        if target.status == "rejected":
            return ReviewOrchestrationResult(
                case_state=current,
                target_hypothesis_id=active_target,
                rounds=rounds,
                review_rounds=len(rounds),
                ready_for_verification=False,
                stop_reason="hypothesis_rejected",
            )

        challenger_review = challenge_case(
            case_state=current,
            target_hypothesis_id=active_target,
            available_actions=available_actions,
            llm_client=llm_client,
        )

        review_round = ReviewRound(
            round_number=round_number,
            target_hypothesis_id=active_target,
            challenger_review=challenger_review,
        )
        rounds.append(review_round)

        if challenger_review.outcome == "legitimate_alternative":
            _mark_hypothesis_inconclusive(current, active_target)
            current.status = "investigating"
            return ReviewOrchestrationResult(
                case_state=current,
                target_hypothesis_id=active_target,
                rounds=rounds,
                review_rounds=len(rounds),
                ready_for_verification=False,
                stop_reason="legitimate_alternative_unresolved",
            )

        if challenger_review.outcome in {
            "needs_more_evidence",
            "alternative_hypothesis",
        }:
            _absorb_challenger_questions(current, challenger_review)
            alternative_ids: list[str] = []
            if challenger_review.outcome == "alternative_hypothesis":
                alternative_ids = _add_alternative_hypotheses(
                    current,
                    active_target,
                    challenger_review,
                )

            investigation_result = _run_followup_investigation(
                current,
                estate,
                llm_client,
                max_investigation_steps,
            )
            review_round.investigator_result = investigation_result
            current = investigation_result.case_state.model_copy(deep=True)

            stop_reason = _stop_from_investigator(investigation_result)
            if stop_reason is not None:
                return ReviewOrchestrationResult(
                    case_state=current,
                    target_hypothesis_id=active_target,
                    rounds=rounds,
                    review_rounds=len(rounds),
                    ready_for_verification=False,
                    stop_reason=stop_reason,
                )

            next_target = _select_review_target(
                current,
                active_target,
                alternative_ids,
            )
            if next_target is None:
                return ReviewOrchestrationResult(
                    case_state=current,
                    target_hypothesis_id=active_target,
                    rounds=rounds,
                    review_rounds=len(rounds),
                    ready_for_verification=False,
                    stop_reason="no_reviewable_hypothesis",
                )

            active_target = next_target
            current.status = "ready_for_review"
            continue

        # Challenger survived: only now run Method Critic.
        method_result = criticize_method(
            llm_client=llm_client,
            case_state=current,
            target_hypothesis_id=active_target,
            available_actions=available_actions,
            rule_context=rule_context,
        )
        review_round.method_critic_review = method_result.review

        if method_result.review.outcome == "cannot_support":
            _mark_hypothesis_inconclusive(current, active_target)
            current.status = "investigating"
            return ReviewOrchestrationResult(
                case_state=current,
                target_hypothesis_id=active_target,
                rounds=rounds,
                review_rounds=len(rounds),
                ready_for_verification=False,
                stop_reason="method_cannot_support",
            )

        if method_result.review.outcome == "needs_more_work":
            _absorb_method_questions(current, method_result.review)
            investigation_result = _run_followup_investigation(
                current,
                estate,
                llm_client,
                max_investigation_steps,
            )
            review_round.investigator_result = investigation_result
            current = investigation_result.case_state.model_copy(deep=True)

            stop_reason = _stop_from_investigator(investigation_result)
            if stop_reason is not None:
                return ReviewOrchestrationResult(
                    case_state=current,
                    target_hypothesis_id=active_target,
                    rounds=rounds,
                    review_rounds=len(rounds),
                    ready_for_verification=False,
                    stop_reason=stop_reason,
                )

            next_target = _select_review_target(current, active_target, [])
            if next_target is None:
                return ReviewOrchestrationResult(
                    case_state=current,
                    target_hypothesis_id=active_target,
                    rounds=rounds,
                    review_rounds=len(rounds),
                    ready_for_verification=False,
                    stop_reason="no_reviewable_hypothesis",
                )

            active_target = next_target
            current.status = "ready_for_review"
            continue

        # Method Critic clear. Verification still needs a concrete official scheme.
        target = _find_hypothesis(current, active_target)
        if target.scheme_type is None:
            current.status = "investigating"
            return ReviewOrchestrationResult(
                case_state=current,
                target_hypothesis_id=active_target,
                rounds=rounds,
                review_rounds=len(rounds),
                ready_for_verification=False,
                stop_reason="scheme_type_missing",
            )

        current.status = "ready_for_verification"
        return ReviewOrchestrationResult(
            case_state=current,
            target_hypothesis_id=active_target,
            rounds=rounds,
            review_rounds=len(rounds),
            ready_for_verification=True,
            stop_reason="ready_for_verification",
        )

    current.status = "ready_for_review"
    return ReviewOrchestrationResult(
        case_state=current,
        target_hypothesis_id=active_target,
        rounds=rounds,
        review_rounds=len(rounds),
        ready_for_verification=False,
        stop_reason="max_review_rounds_reached",
    )
