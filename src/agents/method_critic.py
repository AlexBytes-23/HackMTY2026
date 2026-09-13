from __future__ import annotations

import json
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator

from src.core.models import (
    CaseState,
    ProposedAction,
)
from src.agents.case_analysis import build_case_briefing
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

=== THE ONE FAILURE MODE THAT KEEPS RECURRING ===

A check that establishes something about ONE record, used to make a claim about ALL
of them. This has re-entered this system three separate times through different doors.

For every claim in the hypothesis, ask explicitly: is this UNIVERSAL over the cited
records, or EXISTENTIAL over them? "An invoice was issued after the listing" and
"every invoice was issued after the listing" are different claims resting on
different evidence. If the method establishes the existential and the hypothesis
asserts the universal, that is a material methodological defect -- say so, and say
which records would have to be checked to close the gap.

=== USE THE DETERMINISTIC PRE-ANALYSIS ===

You are given a `deterministic_pre_analysis` block computed in Python from the case
state. Its numbers are established facts; do not recompute or dispute them. Use them
instead of eyeballing the raw case state:

* `evidence_independence.distinct_underlying_records` is how many separate facts
  actually exist. `evidence_item_count` is NOT corroboration. If five evidence items
  rest on two records, the case has two facts, not five.
* `evidence_independence.records_supporting_more_than_one_signal` names each record
  that more than one detector or evidence item is built on. Those signals are NOT
  independent of one another. Multiple detectors agreeing is not agreement; it is the
  same record counted twice.
* `absence_claims.successful_queries_returning_no_record` lists queries that ran
  correctly and found nothing. If the hypothesis leans on any of these as if it
  established non-existence, that is a material defect.
* `cycle_continuity` gives the amount spread and date span of each detected cycle.
  A cycle with legs differing many-fold, or separated by months, is weak support for
  a same-money interpretation. No threshold is supplied and you must not invent one:
  reason from the numbers shown.
* `shared_identifier_claims` states whether a transfer between the parties is also
  observed. A shared identifier WITHOUT an observed flow supports a much narrower
  claim than one with it.
* `scores` lists every score and whether its semantics were declared. A score with
  `semantics_declared: false` cannot be interpreted at all and must not support a
  conclusion.

=== HOW TO DECIDE ===

Ask, in this order:

1. Does any claim rest on a record that another claim already rests on, while being
   presented as independent support?
2. Does any claim treat a query that returned nothing as proof that nothing exists?
3. Does any claim treat a matching identifier as ownership, control or collusion?
4. Does any claim treat a cycle as the same money, or a correlation as a cause?
5. Does any claim read a score as a likelihood?
6. Is any claim universal where the method only established an existential?
7. Is a rule, threshold or legal requirement being used that was NOT supplied in
   `rule_context`?

Then choose:

- "clear": you worked through 1-7 and none applies with material force. This does NOT
  mean the hypothesis is correct or proven -- only that you found no methodological
  defect that would change the conclusion. You must NOT include material objections.
  Put genuinely minor observations in `non_material_notes`.
- "needs_more_work": a material defect exists AND at least one action in the available
  list could materially resolve it. MUST propose at least one such action. Prefer the
  action whose result could FALSIFY the hypothesis, not merely add to it.
- "cannot_support": a material defect exists and NO available action can repair it --
  for example the estate cannot answer the question at all. MUST give a material
  blocker saying precisely why it is unrepairable. Do not use this outcome for a defect
  that a listed action could fix; that is "needs_more_work".

Be skeptical and useful, not obstructive. A clean method deserves "clear". Do not
manufacture a defect to appear rigorous, and do not block a sound method over a
remote possibility. Equally, do not wave through a conclusion that is stronger than
its evidence.

=== reasoning_summary ===

Write it so a non-technical reader can follow it. State, in plain language: what the
method actually established, what it did not, and -- if you objected -- exactly how
your objection could change the conclusion. Do not restate the hypothesis back.

Do NOT duplicate the Challenger (e.g. do not invent legitimate alternative business stories).
Do NOT duplicate the Verifier (do not recalculate final monetary facts).

If you refer to specific evidence items or prior actions, you MUST include their exact IDs/step numbers in `referenced_evidence_ids` and `referenced_action_steps`.

=== RETURN VALID JSON, AND FILL EVERY NESTED FIELD ===

A response that fails to parse is neither a finding nor a reasoned decline. It is a
hole in the case file, and it is the worst outcome you can produce -- worse than
being wrong, because nobody can see what you thought. Live runs have died on a
nested required field being omitted and on the STRING "null" being sent where a
real JSON null was meant.

The exact output shape is supplied as `required_output_example` in the context
block below. Copy its structure.

Omit a LIST entirely if it is empty. Never omit a field inside an object you did
include. Remember the validator will REJECT "clear" if any material list is
non-empty, "needs_more_work" without a proposed action, and "cannot_support"
without a material blocker -- so choose the outcome that matches what you actually
found rather than trying to fit findings to a chosen outcome.
"""


# El ejemplo de salida vive aqui y NO dentro de METHOD_CRITIC_PROMPT: ese texto se
# antepone al user prompt, y hay stubs y arneses externos que extraen el contexto
# buscando la primera "{". Un ejemplo con llaves dentro del prompt les rompe el
# parseo. Invariante: METHOD_CRITIC_PROMPT no contiene "{".
_REQUIRED_OUTPUT_EXAMPLE = {
    "target_hypothesis_id": "<the id you were asked to review, verbatim>",
    "outcome": "one of: clear | needs_more_work | cannot_support",
    "problematic_assumptions": ["..."],
    "evidence_dependency_risks": ["..."],
    "confirmation_bias_risks": ["..."],
    "selection_or_window_bias": ["..."],
    "rule_misuse": ["..."],
    "source_completeness_risks": ["..."],
    "entity_resolution_risks": ["..."],
    "missing_alternative_methods": ["..."],
    "material_blockers": ["..."],
    "non_material_notes": ["..."],
    "proposed_actions": [
        {
            "action_name": "<exact name from available_actions>",
            "arguments": {"<required arg>": "<value>"},
            "reason": "...",
            "question_resolved": "...",
        }
    ],
    "reasoning_summary": "...",
    "referenced_evidence_ids": ["EV-..."],
    "referenced_action_steps": [1],
    "_rules": [
        "Omit a LIST entirely if it is empty.",
        "Never omit a field inside an object you did include.",
        "Use a real JSON null, never the string 'null'.",
    ],
}


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
    
    # El pre-analisis va ANTES del volcado del case_state a proposito: es lo que
    # el critico debe leer primero, y lo que evita que tenga que inferir a ojo
    # cosas que Python ya calculo. Ver src/agents/case_analysis.py.
    context = {
        "target_hypothesis_id": target_hypothesis_id,
        "deterministic_pre_analysis": build_case_briefing(case_state),
        "deterministic_dependency_analysis": dependencies,
        "rule_context": rule_context_str,
        "available_actions": safe_actions,
        "case_state": case_state.model_dump(),
        "required_output_example": _REQUIRED_OUTPUT_EXAMPLE,
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
