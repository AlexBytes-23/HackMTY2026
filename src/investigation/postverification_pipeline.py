"""Boundary adapter from adversarial preverification to deterministic authorization.

This module deliberately does not investigate, rerun adversarial agents, build
findings, or serialize the official submission.  It connects already-reviewed
case state to the deterministic verifier and Evidence Gate.

A normal investigative stop before verification is represented as a blocked
result.  A contradiction in the *program's own* ready-for-verification contract
raises :class:`PostVerificationBoundaryError` instead of being mislabeled as
forensic uncertainty.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel

from src.core.estate import EstateRepository
from src.gates.evidence_gate import EvidenceGateDecision, evaluate_gate
from src.investigation.preverification_pipeline import PreVerificationResult
from src.investigation.review_orchestrator import ReviewRound
from src.rules.rule_registry import RuleRegistry
from src.verifier.official_verifier import OfficialVerifier, VerificationReport


PostVerificationStatus = Literal["preverification_blocked", "evaluated"]


class PostVerificationBoundaryError(RuntimeError):
    """The runtime violated an internal integration invariant."""


class PostVerificationResult(BaseModel):
    """Auditable result of the deterministic post-review boundary."""

    status: PostVerificationStatus
    preverification_result: PreVerificationResult
    target_hypothesis_id: str | None = None
    selected_review_round: ReviewRound | None = None
    claimed_amount: float | None = None
    requested_rule_id: str | None = None
    verification_report: VerificationReport | None = None
    gate_decision: EvidenceGateDecision | None = None
    detail: str | None = None


def _select_final_review_round(
    preverification_result: PreVerificationResult,
    target_hypothesis_id: str,
) -> ReviewRound:
    review = preverification_result.review_result

    if review is None:
        raise PostVerificationBoundaryError(
            "PreVerificationResult is ready_for_verification but review_result is missing."
        )
    if not review.ready_for_verification:
        raise PostVerificationBoundaryError(
            "PreVerificationResult is ready_for_verification but ReviewOrchestrationResult is not."
        )
    if review.target_hypothesis_id != target_hypothesis_id:
        raise PostVerificationBoundaryError(
            "PreVerificationResult target_hypothesis_id does not match "
            "ReviewOrchestrationResult target_hypothesis_id."
        )

    for review_round in reversed(review.rounds):
        if (
            review_round.target_hypothesis_id == target_hypothesis_id
            and review_round.method_critic_review is not None
        ):
            if review_round.challenger_review is None:
                raise PostVerificationBoundaryError(
                    "Matching final ReviewRound has no ChallengerReview."
                )
            return review_round

    raise PostVerificationBoundaryError(
        "No reviewed round exists for the final target hypothesis with both "
        "Challenger and Method Critic review."
    )


def _normalize_claimed_amount(claimed_amount: float | None) -> float | None:
    """Validate the external claim without manufacturing one from exhibits."""

    if claimed_amount is None:
        return None

    if isinstance(claimed_amount, bool):
        raise PostVerificationBoundaryError(
            "claimed_amount must be a finite positive peso amount, not boolean."
        )

    try:
        amount = float(claimed_amount)
    except (TypeError, ValueError) as error:
        raise PostVerificationBoundaryError(
            "claimed_amount must be numeric when provided."
        ) from error

    if not math.isfinite(amount) or amount <= 0:
        raise PostVerificationBoundaryError(
            "claimed_amount must be finite and strictly positive when provided."
        )

    return amount


def run_postverification_pipeline(
    preverification_result: PreVerificationResult,
    estate: EstateRepository,
    rule_registry: RuleRegistry,
    *,
    claimed_amount: float | None = None,
    requested_rule_id: str | None = None,
    useful_actions_remain: bool = False,
) -> PostVerificationResult:
    """Run deterministic verification and authorization for one reviewed target.

    Important:
    - ``claimed_amount`` is an independent upstream claim.  This layer never
      derives it from exhibits or reconciliation output.
    - ``requested_rule_id`` is an explicit formal rule selection.  This layer
      never infers it from Method Critic ``rule_context``.
    - Missing claim/rule inputs are allowed through to Verifier/Gate so those
      components fail closed in their own auditable reports.
    """

    if not preverification_result.ready_for_verification:
        return PostVerificationResult(
            status="preverification_blocked",
            preverification_result=preverification_result,
            target_hypothesis_id=preverification_result.target_hypothesis_id,
            claimed_amount=claimed_amount,
            requested_rule_id=requested_rule_id,
            detail=(
                "Preverification did not authorize deterministic verification; "
                f"stop_reason={preverification_result.stop_reason}."
            ),
        )

    if preverification_result.stop_reason != "ready_for_verification":
        raise PostVerificationBoundaryError(
            "PreVerificationResult says ready_for_verification=True but stop_reason "
            "is not 'ready_for_verification'."
        )

    target_hypothesis_id = preverification_result.target_hypothesis_id
    if target_hypothesis_id is None:
        raise PostVerificationBoundaryError(
            "PreVerificationResult is ready_for_verification but target_hypothesis_id is missing."
        )

    review_round = _select_final_review_round(
        preverification_result,
        target_hypothesis_id,
    )
    amount = _normalize_claimed_amount(claimed_amount)

    verification_report = OfficialVerifier().verify(
        case_state=preverification_result.case_state,
        target_hypothesis_id=target_hypothesis_id,
        estate=estate,
        claimed_amount=amount,
    )

    gate_decision = evaluate_gate(
        case_state=preverification_result.case_state,
        target_hypothesis_id=target_hypothesis_id,
        challenger_review=review_round.challenger_review,
        method_critic_review=review_round.method_critic_review,
        verification_report=verification_report,
        rule_registry=rule_registry,
        requested_rule_id=requested_rule_id,
        useful_actions_remain=useful_actions_remain,
    )

    return PostVerificationResult(
        status="evaluated",
        preverification_result=preverification_result,
        target_hypothesis_id=target_hypothesis_id,
        selected_review_round=review_round,
        claimed_amount=amount,
        requested_rule_id=requested_rule_id,
        verification_report=verification_report,
        gate_decision=gate_decision,
        detail=(
            "Deterministic verification and Evidence Gate evaluation completed. "
            "This result is not itself a serialized finding."
        ),
    )
