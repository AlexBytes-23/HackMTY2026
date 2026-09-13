from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

from src.core.models import CaseState
from src.agents.challenger import ChallengerOutcome, ChallengerReview
from src.agents.method_critic import MethodCriticOutcome, MethodCriticReview
from src.verifier.official_verifier import VerificationReport
from src.rules.rule_registry import RuleRegistry

GateOutcome = Literal[
    "authorize_probable",
    "authorize_proven",
    "need_more_work",
    "decline_hypothesis",
    "inconclusive"
]

class EvidenceGateDecision(BaseModel):
    target_hypothesis_id: str
    outcome: GateOutcome
    authorized_confidence: Literal["probable", "proven"] | None = None
    satisfied_requirements: list[str] = Field(default_factory=list)
    failed_requirements: list[str] = Field(default_factory=list)
    unresolved_material_questions: list[str] = Field(default_factory=list)
    reason: str

def evaluate_gate(
    case_state: CaseState,
    target_hypothesis_id: str,
    challenger_review: ChallengerReview | None,
    method_critic_review: MethodCriticReview | None,
    verification_report: VerificationReport | None,
    rule_registry: RuleRegistry,
    requested_rule_id: str | None = None,
    useful_actions_remain: bool = False
) -> EvidenceGateDecision:

    decision = EvidenceGateDecision(
        target_hypothesis_id=target_hypothesis_id,
        outcome="inconclusive",
        reason=""
    )

    # 1. Target Hypothesis state
    hypothesis = next((h for h in case_state.hypotheses if h.hypothesis_id == target_hypothesis_id), None)
    if not hypothesis:
        decision.failed_requirements.append("target hypothesis does not exist")
        decision.reason = "Hypothesis missing."
        return decision

    if hypothesis.status == "rejected":
        decision.outcome = "decline_hypothesis"
        decision.failed_requirements.append("target hypothesis is already rejected")
        decision.reason = "Hypothesis is explicitly rejected."
        return decision

    # 2. Rule Context
    if requested_rule_id:
        rule = rule_registry.get_rule(requested_rule_id)
        if not rule:
            decision.failed_requirements.append("requested rule_id is not valid in the Rule Registry")
        else:
            # Enforce applicability securely
            if not hypothesis.scheme_type:
                decision.failed_requirements.append("target hypothesis has no scheme_type")
            elif not rule.applies_to:
                decision.failed_requirements.append("requested rule has empty applies_to")
            elif hypothesis.scheme_type not in rule.applies_to:
                decision.failed_requirements.append(f"requested rule_id {requested_rule_id} does not apply to scheme_type {hypothesis.scheme_type}")
    else:
        decision.failed_requirements.append("requested rule_id is missing or not valid")

    # 3. Challenger Review
    if not challenger_review:
        decision.failed_requirements.append("ChallengerReview is mandatory but missing")
    else:
        if challenger_review.outcome == "needs_more_evidence":
            decision.failed_requirements.append("Challenger outcome is needs_more_evidence")
        elif challenger_review.outcome == "legitimate_alternative":
            decision.outcome = "decline_hypothesis"
            decision.failed_requirements.append("Challenger identified a resolved legitimate alternative")
            decision.reason = "Hypothesis explicitly declined due to legitimate alternative."
            return decision
        elif challenger_review.outcome == "alternative_hypothesis":
            decision.failed_requirements.append("Challenger identified an alternative hypothesis")

    # 4. Method Critic Review
    if not method_critic_review:
        decision.failed_requirements.append("MethodCriticReview is mandatory but missing")
    else:
        if method_critic_review.outcome == "needs_more_work":
            decision.failed_requirements.append("Method Critic outcome is needs_more_work")
        elif method_critic_review.outcome == "cannot_support":
            decision.failed_requirements.append("Method Critic outcome is cannot_support")

    # 5. Verification Report checks
    if not verification_report:
        decision.failed_requirements.append("verification_report is missing")
    else:
        if verification_report.target_hypothesis_id != target_hypothesis_id:
            decision.failed_requirements.append("VerificationReport target hypothesis does not match Gate target")

        if verification_report.internal_errors:
            decision.failed_requirements.append("VerificationReport contains internal errors")

        if not verification_report.checks:
            decision.failed_requirements.append("VerificationReport contains no checks")

        unique_exhibits = set((ref.source_table, str(ref.record_id)) for ref in verification_report.resolved_exhibits)
        if len(unique_exhibits) < 3:
            decision.failed_requirements.append(f"fewer than 3 valid unique exhibits exist (found {len(unique_exhibits)})")

        reconciles = False
        if (verification_report.reconciliation and verification_report.reconciliation.get("reconciles") is True):
            reconciles = True

        if not reconciles:
            decision.failed_requirements.append("peso_amount is unsupported or fails deterministic reconciliation")

        has_verified_peso = False
        has_verified_phantom = False
        has_verified_kickback = False

        for check in verification_report.checks:
            if check.critical:
                if check.status == "failed":
                    decision.failed_requirements.append(f"Critical check failed: {check.check_id}")
                elif check.status == "unresolved":
                    decision.failed_requirements.append(f"Critical check unresolved: {check.check_id}")
                elif check.status == "verified":
                    if check.check_id == "PESO-RECONCILIATION":
                        has_verified_peso = True
                    elif check.check_id == "PHANTOM-EFOS-INVOICE-LINK":
                        has_verified_phantom = True
                    elif check.check_id == "KICKBACK-VENDOR-EMPLOYEE-LINK":
                        has_verified_kickback = True

        if not has_verified_peso:
            decision.failed_requirements.append("VerificationReport lacks a critical verified PESO-RECONCILIATION check")

        # Substantive scheme verification
        if hypothesis.scheme_type == "phantom_vendor":
            if not has_verified_phantom:
                decision.failed_requirements.append("VerificationReport lacks a critical verified PHANTOM-EFOS-INVOICE-LINK check")
        elif hypothesis.scheme_type == "kickback":
            if not has_verified_kickback:
                decision.failed_requirements.append("VerificationReport lacks a critical verified KICKBACK-VENDOR-EMPLOYEE-LINK check")
        elif hypothesis.scheme_type:
            decision.failed_requirements.append(f"No supported deterministic substantive verifier exists for scheme_type {hypothesis.scheme_type}")

    # Resolution
    if decision.failed_requirements:
        # Determine the most conservative blocking outcome
        if challenger_review and challenger_review.outcome == "legitimate_alternative":
            decision.outcome = "decline_hypothesis"
            decision.reason = "Resolved legitimate alternative exists."
        elif method_critic_review and method_critic_review.outcome == "cannot_support":
            decision.outcome = "inconclusive"
            decision.reason = "Method Critic structurally cannot support."
        elif useful_actions_remain or (challenger_review and challenger_review.outcome == "needs_more_evidence") or (method_critic_review and method_critic_review.outcome == "needs_more_work") or (not challenger_review) or (not method_critic_review):
            decision.outcome = "need_more_work"
            decision.reason = "Repairable missing evidence or objections exist."
        else:
            decision.outcome = "inconclusive"
            decision.reason = "Evidence is insufficient but no reasonable available action can resolve the remaining material uncertainty."
        return decision

    decision.satisfied_requirements = ["All deterministic checks passed."]
    decision.outcome = "authorize_probable"
    decision.authorized_confidence = "probable"
    decision.reason = "All evidence criteria satisfied. Authorizing probable. No explicit scheme-specific proven standard implemented yet."

    return decision
