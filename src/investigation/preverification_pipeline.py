from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.core.estate import EstateRepository
from src.core.models import CaseState, InvestigationLoopResult
from src.investigation.investigator import LLMClient
from src.investigation.review_orchestrator import (
    ReviewOrchestrationResult,
    run_review_orchestration,
)
from src.investigation.runner import run_investigation_loop


PreVerificationStopReason = Literal[
    "ready_for_verification",
    "investigator_closed_inconclusive",
    "investigator_repeated_action",
    "investigator_budget_exhausted",
    "no_reviewable_hypothesis",
    "review_blocked",
]


class PreVerificationResult(BaseModel):
    """
    Auditable result of preparing one case for deterministic verification.

    This layer coordinates existing investigation/review components only.
    It does not verify facts, authorize findings, or serialize submissions.
    """

    case_state: CaseState
    initial_investigation: InvestigationLoopResult
    review_result: ReviewOrchestrationResult | None = None
    target_hypothesis_id: str | None = None
    ready_for_verification: bool = False
    stop_reason: PreVerificationStopReason
    detail: str | None = None


def _select_review_target(
    case_state: CaseState,
    preferred_hypothesis_id: str | None,
) -> str | None:
    """Choose one live hypothesis deterministically for adversarial review."""

    live = [
        hypothesis
        for hypothesis in case_state.hypotheses
        if hypothesis.status in {"open", "supported"}
    ]

    if preferred_hypothesis_id is not None:
        for hypothesis in live:
            if hypothesis.hypothesis_id == preferred_hypothesis_id:
                return preferred_hypothesis_id
        raise ValueError(
            f"Preferred hypothesis {preferred_hypothesis_id!r} is not an open/supported "
            f"hypothesis in case {case_state.case_id!r}."
        )

    # Prefer hypotheses already marked supported, but preserve stable CaseState order.
    for hypothesis in live:
        if hypothesis.status == "supported":
            return hypothesis.hypothesis_id

    if live:
        return live[0].hypothesis_id

    return None


def run_preverification_pipeline(
    case_state: CaseState,
    estate: EstateRepository,
    llm_client: LLMClient,
    *,
    preferred_hypothesis_id: str | None = None,
    max_initial_investigation_steps: int = 6,
    max_review_rounds: int = 3,
    max_followup_investigation_steps: int = 3,
    rule_context: list | None = None,
) -> PreVerificationResult:
    """
    Run Investigator and mandatory adversarial review up to the Verifier boundary.

    Important invariant: an Investigator decision of ``ready_for_verification``
    never bypasses Challenger + Method Critic. The case is only returned as ready
    after ``run_review_orchestration`` says so.
    """

    initial = run_investigation_loop(
        case_state=case_state,
        estate=estate,
        llm_client=llm_client,
        max_steps=max_initial_investigation_steps,
    )
    current = initial.case_state.model_copy(deep=True)

    if initial.stop_reason == "close_inconclusive":
        return PreVerificationResult(
            case_state=current,
            initial_investigation=initial,
            ready_for_verification=False,
            stop_reason="investigator_closed_inconclusive",
        )

    if initial.stop_reason == "repeated_action":
        return PreVerificationResult(
            case_state=current,
            initial_investigation=initial,
            ready_for_verification=False,
            stop_reason="investigator_repeated_action",
        )

    if initial.stop_reason == "max_steps_reached":
        return PreVerificationResult(
            case_state=current,
            initial_investigation=initial,
            ready_for_verification=False,
            stop_reason="investigator_budget_exhausted",
        )

    # Both request_review and Investigator's own ready_for_verification decision
    # must pass through the adversarial review stages before deterministic verification.
    target_hypothesis_id = _select_review_target(
        current,
        preferred_hypothesis_id,
    )

    if target_hypothesis_id is None:
        current.status = "investigating"
        return PreVerificationResult(
            case_state=current,
            initial_investigation=initial,
            target_hypothesis_id=None,
            ready_for_verification=False,
            stop_reason="no_reviewable_hypothesis",
            detail="Investigation reached review/verification without an open or supported hypothesis.",
        )

    current.status = "ready_for_review"
    review = run_review_orchestration(
        case_state=current,
        target_hypothesis_id=target_hypothesis_id,
        estate=estate,
        llm_client=llm_client,
        max_review_rounds=max_review_rounds,
        max_investigation_steps=max_followup_investigation_steps,
        rule_context=rule_context,
    )

    if review.ready_for_verification:
        return PreVerificationResult(
            case_state=review.case_state,
            initial_investigation=initial,
            review_result=review,
            target_hypothesis_id=review.target_hypothesis_id,
            ready_for_verification=True,
            stop_reason="ready_for_verification",
        )

    return PreVerificationResult(
        case_state=review.case_state,
        initial_investigation=initial,
        review_result=review,
        target_hypothesis_id=review.target_hypothesis_id,
        ready_for_verification=False,
        stop_reason="review_blocked",
        detail=review.stop_reason,
    )
