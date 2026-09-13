from __future__ import annotations

import json
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator

from src.core.models import (
    CaseState,
    ProposedAction,
)
from src.investigation.investigator import LLMClient
from src.llm.json_text import strip_code_fences

MethodCriticOutcome = Literal["clear", "needs_more_work", "cannot_support"]

class MethodCriticReview(BaseModel):
    target_hypothesis_id: str
    outcome: MethodCriticOutcome
    
    problematic_assumptions: list[str] = Field(default_factory=list)
    evidence_dependency_risks: list[str] = Field(default_factory=list)
    confirmation_bias_risks: list[str] = Field(default_factory=list)
    selection_or_window_bias: list[str] = Field(default_factory=list)
    rule_misuse: list[str] = Field(default_factory=list)
    source_completeness_risks: list[str] = Field(default_factory=list)
    entity_resolution_risks: list[str] = Field(default_factory=list)
    missing_alternative_methods: list[str] = Field(default_factory=list)
    
    material_blockers: list[str] = Field(
        default_factory=list,
        description="Explicit blockers explaining why current methodology CANNOT be repaired with available tools (cannot_support)."
    )
    non_material_notes: list[str] = Field(
        default_factory=list,
        description="Minor observations that do not block a 'clear' outcome."
    )
    
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    reasoning_summary: str
    
    referenced_evidence_ids: list[str] = Field(
        default_factory=list,
        description="All evidence IDs explicitly referenced in your critique."
    )
    
    referenced_action_steps: list[int] = Field(
        default_factory=list,
        description="All action steps explicitly referenced in your critique."
    )
    
    @model_validator(mode="after")
    def validate_outcome_requirements(self) -> "MethodCriticReview":
        material_lists = [
            self.problematic_assumptions, self.evidence_dependency_risks,
            self.confirmation_bias_risks, self.selection_or_window_bias,
            self.rule_misuse, self.source_completeness_risks,
            self.entity_resolution_risks, self.missing_alternative_methods,
            self.material_blockers
        ]
        has_material_objections = any(len(lst) > 0 for lst in material_lists)
        
        if self.outcome == "clear":
            if has_material_objections:
                raise ValueError("outcome 'clear' cannot contain material objections or blockers. Use non_material_notes for minor issues.")
                
        elif self.outcome == "needs_more_work":
            if not self.proposed_actions:
                raise ValueError("needs_more_work requires at least one proposed_actions item.")
                
        elif self.outcome == "cannot_support":
            if not self.material_blockers:
                raise ValueError("cannot_support requires at least one explicit material_blockers item explaining why it cannot be repaired.")
                
        return self


class MethodCriticResult(BaseModel):
    review: MethodCriticReview
    deterministic_dependencies: list[dict[str, Any]]


def find_evidence_dependencies(case_state: CaseState) -> list[dict[str, Any]]:
    """
    Deterministically find overlapping source references among evidence items.
    """
    ref_to_evidence: dict[tuple[str, str], set[str]] = {}
    
    for evidence in case_state.evidence:
        for ref in evidence.source_refs:
            key = (ref.source_table, ref.record_id)
            if key not in ref_to_evidence:
                ref_to_evidence[key] = set()
            ref_to_evidence[key].add(evidence.evidence_id)
            
    dependencies = []
    
    for key, ev_ids_set in ref_to_evidence.items():
        if len(ev_ids_set) > 1:
            dependencies.append({
                "shared_source_table": key[0],
                "shared_record_id": key[1],
                "evidence_ids_involved": sorted(list(ev_ids_set)),
                "warning": "These evidence items share the exact same underlying record and cannot automatically be treated as independent corroboration."
            })
            
    grouped = {}
    for dep in dependencies:
        gkey = tuple(dep["evidence_ids_involved"])
        if gkey not in grouped:
            grouped[gkey] = {
                "evidence_ids_involved": list(gkey),
                "shared_source_refs": [],
                "warning": dep["warning"]
            }
        grouped[gkey]["shared_source_refs"].append({
            "source_table": dep["shared_source_table"],
            "record_id": dep["shared_record_id"]
        })
        
    return list(grouped.values())


METHOD_CRITIC_PROMPT = """
You are the Method Critic, the methodological red-team layer of a Forensic Auditor multi-agent system.
Your job is NOT to ask "what other explanation could account for these facts?" (That is the Challenger's job).
Instead, you must ask: "Even if this interpretation sounds plausible, was the PROCESS used to reach it methodologically defensible?"

You must NEVER decide guilt, fraud, confidence level, probable/proven, or Finding authorization.

=== GOLDEN RULES ===
1. anomaly != fraud
2. correlation != causation
3. matching identifiers != proof of ownership/collusion
4. absence in estate != absence in reality (Never allow reasoning like "No contract record was found, therefore no contract exists". Instead, state "No contract record was found in the supplied estate.")
5. multiple detectors != independent evidence (If detectors share the same underlying transactions, they double-count evidence).
6. graph cycle != automatically same-money round trip (Temporal order and exact amounts must be proven).
7. statistical score != calibrated fraud probability (e.g. 0.87 anomaly score != 87% probability of fraud).
8. NO INVENTED LAW: Only evaluate rule usage when rule context is explicitly supplied from the Rule Registry. Do not manufacture legal citations, procurement thresholds, or internal policies.
9. NO GUILT DETERMINATION: Do not declare a subject innocent or guilty.
10. If you object, identify EXACTLY how the objection could change the conclusion.
11. Propose a real action ONLY when it can materially resolve the weakness. Do not propose actions that do not exist in the available actions list.

=== EXPECTED OUTCOME ===
- "clear": No material methodological objection remains using the available case state, tools, and supplied rule context. (Does NOT mean hypothesis is correct/proven. You must NOT include material objections).
- "needs_more_work": A material methodological weakness exists and at least one concrete available action can reasonably address it. (MUST propose at least one action).
- "cannot_support": The current hypothesis cannot be methodologically supported from the available estate/tools because a material defect cannot reasonably be repaired with another available action. (MUST provide a material blocker).

Do NOT duplicate the Challenger (e.g. do not invent legitimate alternative business stories).
Do NOT duplicate the Verifier (do not recalculate final monetary facts).

If you refer to specific evidence items or prior actions, you MUST include their exact IDs/step numbers in `referenced_evidence_ids` and `referenced_action_steps`.

You must return a valid JSON object matching the requested schema.
"""


def _safe_serialize(items: list[Any]) -> list[Any]:
    safe_items = []
    for item in items:
        if hasattr(item, "model_dump"):
            safe_items.append(item.model_dump())
        elif hasattr(item, "__dict__"):
            safe_items.append(item.__dict__)
        else:
            safe_items.append(item)
    return safe_items


def build_method_critic_prompt(
    case_state: CaseState,
    target_hypothesis_id: str,
    available_actions: list[Any],
    rule_context: list[Any] | None = None
) -> str:
    dependencies = find_evidence_dependencies(case_state)
    
    safe_actions = _safe_serialize(available_actions)
    
    if rule_context is None:
        rule_context_str = "No explicit rule context supplied. Normative, legal, or threshold-based claims CANNOT be validated."
    else:
        rule_context_str = _safe_serialize(rule_context)
    
    context = {
        "target_hypothesis_id": target_hypothesis_id,
        "case_state": case_state.model_dump(),
        "deterministic_dependency_analysis": dependencies,
        "available_actions": safe_actions,
        "rule_context": rule_context_str,
        "schema": MethodCriticReview.model_json_schema()
    }
    
    return f"{METHOD_CRITIC_PROMPT}\n\n=== CONTEXT ===\n{json.dumps(context, indent=2)}"


def parse_method_critic_review(text: str) -> MethodCriticReview:
    try:
        payload = json.loads(strip_code_fences(text))
        return MethodCriticReview.model_validate(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Method Critic returned invalid JSON.") from exc


def _action_name(action: Any) -> str | None:
    if isinstance(action, dict):
        return action.get("name")
    if hasattr(action, "name"):
        return action.name
    return None


def _required_arguments(action: Any) -> set[str]:
    if isinstance(action, dict):
        return set(action.get("required_arguments", []))
    if hasattr(action, "required_arguments"):
        return set(action.required_arguments)
    return set()


def validate_method_critic_review(
    review: MethodCriticReview,
    *,
    case_state: CaseState,
    target_hypothesis_id: str,
    available_actions: list[Any],
) -> None:
    known_hypotheses = {h.hypothesis_id for h in case_state.hypotheses}
    if target_hypothesis_id not in known_hypotheses:
        raise ValueError(
            f"Target hypothesis ID {target_hypothesis_id!r} not found in case state."
        )

    if review.target_hypothesis_id != target_hypothesis_id:
        raise ValueError(
            "Method Critic reviewed a different hypothesis than requested: "
            f"expected {target_hypothesis_id!r}, "
            f"received {review.target_hypothesis_id!r}."
        )

    allowed_actions: dict[str, Any] = {}
    for action in available_actions:
        name = _action_name(action)
        if name:
            allowed_actions[name] = action

    for proposed in review.proposed_actions:
        if proposed.action_name not in allowed_actions:
            raise ValueError(
                "Method Critic requested an unavailable action: "
                f"{proposed.action_name!r}."
            )

        required = _required_arguments(allowed_actions[proposed.action_name])
        missing = required - set(proposed.arguments)
        if missing:
            raise ValueError(
                f"Action {proposed.action_name!r} missing required arguments: {missing}"
            )

    known_evidence_ids = {ev.evidence_id for ev in case_state.evidence}
    for eid in review.referenced_evidence_ids:
        if eid not in known_evidence_ids:
            raise ValueError(f"Hallucinated evidence ID: {eid}")
            
    known_steps = {act.step for act in case_state.actions_taken}
    for step in review.referenced_action_steps:
        if step not in known_steps:
            raise ValueError(f"Hallucinated action step: {step}")


def criticize_method(
    llm_client: LLMClient,
    case_state: CaseState,
    target_hypothesis_id: str,
    available_actions: list[Any],
    rule_context: list[Any] | None = None
) -> MethodCriticResult:
    prompt = build_method_critic_prompt(
        case_state, 
        target_hypothesis_id, 
        available_actions,
        rule_context
    )
    
    response_text = llm_client.complete(
        system_prompt=METHOD_CRITIC_PROMPT,
        user_prompt=prompt
    )
    
    review = parse_method_critic_review(response_text)
    
    validate_method_critic_review(
        review,
        case_state=case_state,
        target_hypothesis_id=target_hypothesis_id,
        available_actions=available_actions
    )
    
    dependencies = find_evidence_dependencies(case_state)
    return MethodCriticResult(
        review=review,
        deterministic_dependencies=dependencies
    )
