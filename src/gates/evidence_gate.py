from __future__ import annotations

from typing import Literal, Any
from pydantic import BaseModel, Field

from src.core.models import VerifiedFact, EvidenceRef
from src.core.estate import EstateRepository
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
    authorized_confidence: Literal["probable", "proven"] | None
    satisfied_requirements: list[str] = Field(default_factory=list)
    failed_requirements: list[str] = Field(default_factory=list)
    unresolved_material_questions: list[str] = Field(default_factory=list)
    reason: str

class GateContext(BaseModel):
    target_hypothesis_id: str
    target_hypothesis_exists: bool = True
    target_hypothesis_rejected: bool = False
    scheme_type: str | None = None
    
    critical_verified_facts: list[VerifiedFact] = Field(default_factory=list)
    auxiliary_verified_facts: list[VerifiedFact] = Field(default_factory=list)
    
    challenger_needs_more_evidence: bool = False
    unresolved_legitimate_alternatives: list[str] = Field(default_factory=list)
    resolved_legitimate_alternative: bool = False
    unresolved_alternative_hypothesis_materially_affects: bool = False
    
    material_method_objections: list[str] = Field(default_factory=list)
    strong_unresolved_contradictions: list[str] = Field(default_factory=list)
    material_unanswered_questions: list[str] = Field(default_factory=list)
    
    proposed_exhibits: list[EvidenceRef] = Field(default_factory=list)
    
    claimed_peso_amount: float | None = None
    reconciled_peso_amount: float | None = None
    
    requested_rule_id: str | None = None
    
    useful_actions_remain: bool = False


class EvidenceGate:
    def __init__(self, estate: EstateRepository, registry: RuleRegistry):
        self.estate = estate
        self.registry = registry
        
    def decide(self, context: GateContext) -> EvidenceGateDecision:
        decision = EvidenceGateDecision(
            target_hypothesis_id=context.target_hypothesis_id,
            outcome="inconclusive",
            authorized_confidence=None,
            reason=""
        )
        
        # Validation checks
        if not context.target_hypothesis_exists:
            decision.failed_requirements.append("target hypothesis does not exist")
        if context.target_hypothesis_rejected:
            decision.failed_requirements.append("target hypothesis is already rejected")
        if not context.scheme_type:
            decision.failed_requirements.append("scheme_type is missing")
        if context.challenger_needs_more_evidence:
            decision.failed_requirements.append("Challenger outcome is needs_more_evidence")
        if context.unresolved_legitimate_alternatives:
            decision.failed_requirements.append("unresolved legitimate alternative remains")
        if context.unresolved_alternative_hypothesis_materially_affects:
            decision.failed_requirements.append("unresolved alternative hypothesis materially affects the target conclusion")
        if context.material_method_objections:
            decision.failed_requirements.append("material Method Critic objection remains")
            
        for fact in context.critical_verified_facts:
            if not fact.verified:
                decision.failed_requirements.append(f"explicitly critical VerifiedFact failed verification: {fact.fact_id}")
                
        if context.strong_unresolved_contradictions:
            decision.failed_requirements.append("strong contradiction remains unresolved")
        if context.material_unanswered_questions:
            decision.failed_requirements.append("material unanswered question remains")
            for q in context.material_unanswered_questions:
                decision.unresolved_material_questions.append(q)
                
        if len(context.proposed_exhibits) < 3:
            decision.failed_requirements.append("fewer than 3 valid exhibits exist")
            
        for exhibit in context.proposed_exhibits:
            try:
                exists = self.estate.record_exists(exhibit.source_table, exhibit.record_id)
                if not exists:
                    decision.failed_requirements.append(f"an exhibit points to a record that does not exist: {exhibit.source_table}.{exhibit.record_id}")
            except ValueError:
                decision.failed_requirements.append(f"invalid source table in exhibit: {exhibit.source_table}")

        if context.claimed_peso_amount is None or context.reconciled_peso_amount is None or context.claimed_peso_amount != context.reconciled_peso_amount:
            decision.failed_requirements.append("peso_amount is unsupported or fails deterministic reconciliation")
            
        if context.requested_rule_id:
            rule = self.registry.get_rule(context.requested_rule_id)
            if not rule:
                decision.failed_requirements.append("requested rule_id is not valid in the Rule Registry")
        else:
            decision.failed_requirements.append("requested rule_id is missing or not valid")

        if decision.failed_requirements or context.resolved_legitimate_alternative:
            if context.resolved_legitimate_alternative or context.target_hypothesis_rejected:
                decision.outcome = "decline_hypothesis"
                decision.reason = "Hypothesis explicitly declined or resolved legitimate alternative exists."
            elif context.useful_actions_remain:
                decision.outcome = "need_more_work"
                decision.reason = "There are missing requirements, but useful actions remain."
            else:
                decision.outcome = "inconclusive"
                decision.reason = "Evidence is insufficient but no reasonable available action can resolve the remaining material uncertainty."
            return decision
            
        # If we got here, all requirements are satisfied
        decision.satisfied_requirements = ["All deterministic checks passed."]
        
        # PROVEN POLICY Check
        # For now, without a specific standard mechanism implemented, we authorize probable.
        decision.outcome = "authorize_probable"
        decision.authorized_confidence = "probable"
        decision.reason = "All evidence criteria satisfied. Authorizing probable. No explicit scheme-specific proven standard implemented yet."
        
        return decision
