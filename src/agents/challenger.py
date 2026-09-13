"""
src/agents/challenger.py
========================

Adversarial Case Critic for the Forensic Auditor.

The Challenger does NOT decide whether fraud occurred.
It attacks one specific hypothesis already present in a CaseState.

Its job is to:
- identify unsupported inferences,
- surface contradictions already present in the evidence,
- construct the strongest legitimate alternative,
- notice plausible alternative fraud hypotheses,
- identify material unanswered questions,
- request only actions that actually exist in the Action Bank.

A hypothesis surviving the Challenger does NOT mean fraud is proven.
It only means that this review found no material unresolved objection
with the currently available evidence and tools.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.core.models import (
    CaseState,
    ProposedAction,
    SchemeType,
)
from src.agents.case_analysis import (
    build_case_briefing,
    collect_declared_alternatives,
    normalize_optional_enum,
)
from src.investigation.investigator import LLMClient
from src.llm.json_text import strip_code_fences


ChallengerOutcome = Literal[
    "survives",
    "needs_more_evidence",
    "legitimate_alternative",
    "alternative_hypothesis",
]


class UnsupportedClaim(BaseModel):
    """An inference in the target hypothesis that is not adequately supported."""

    claim: str
    why_unsupported: str
    what_would_support_it: str


class MissingCounterevidence(BaseModel):
    """
    A material unanswered question that could meaningfully strengthen,
    weaken, or redirect the target hypothesis.
    """

    question: str
    why_it_matters: str


class Contradiction(BaseModel):
    """
    Existing case evidence that conflicts with the target hypothesis.
    Evidence IDs must point to evidence already present in CaseState.
    """

    statement: str
    evidence_ids: list[str] = Field(default_factory=list)
    why_it_conflicts: str


class AlternativeHypothesis(BaseModel):
    """
    A materially different explanation that may deserve its own investigation.

    This is used for alternative suspicious mechanisms, not merely for
    legitimate explanations.
    """

    statement: str
    scheme_type: SchemeType | None = None
    reason: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)

    _normalize_scheme_type = field_validator(
        "scheme_type", mode="before"
    )(normalize_optional_enum)


class ChallengerReview(BaseModel):
    """Structured adversarial review of one specific hypothesis."""

    target_hypothesis_id: str

    strongest_legitimate_alternative: str | None = None

    unsupported_claims: list[UnsupportedClaim] = Field(default_factory=list)

    missing_counterevidence: list[MissingCounterevidence] = Field(
        default_factory=list
    )

    contradictions: list[Contradiction] = Field(default_factory=list)

    proposed_actions: list[ProposedAction] = Field(default_factory=list)

    alternative_hypotheses: list[AlternativeHypothesis] = Field(
        default_factory=list
    )

    outcome: ChallengerOutcome

    reasoning_summary: str

    @model_validator(mode="after")
    def validate_outcome_requirements(self) -> "ChallengerReview":

        if self.outcome == "needs_more_evidence":
            if not self.missing_counterevidence:
                raise ValueError(
                    "needs_more_evidence requires at least one "
                    "missing_counterevidence item."
                )

            if not self.proposed_actions:
                raise ValueError(
                    "needs_more_evidence requires at least one "
                    "proposed executable action."
                )

        if self.outcome == "alternative_hypothesis":
            if not self.alternative_hypotheses:
                raise ValueError(
                    "alternative_hypothesis requires at least one "
                    "alternative hypothesis."
                )

        if self.outcome == "legitimate_alternative":
            if not self.strongest_legitimate_alternative:
                raise ValueError(
                    "legitimate_alternative requires a concrete "
                    "legitimate explanation."
                )

        return self


CHALLENGER_SYSTEM_PROMPT = """
You are the Challenger, the adversarial Case Critic inside a forensic
audit system.

You review ONE SPECIFIC HYPOTHESIS from an existing investigation.

Your job is NOT to decide guilt.
Your job is NOT to maximize fraud detections.
Your job is NOT to defend the subject at all costs.

Your job is to test whether the target hypothesis is actually supported
by the evidence currently available.

========================
EPISTEMIC RULES
========================

1. FACT IS NOT INTERPRETATION.

A transaction recorded in the database is a fact.
Calling that transaction fraudulent is an interpretation.

Never deny supplied database facts, but aggressively challenge unsupported
interpretations of those facts.

2. DO NOT PRESUME FRAUD OR LEGITIMACY.

Prefer the explanation that accounts for the available evidence with the
fewest unsupported assumptions.

A legitimate explanation does not win merely because it is conceivable.
A fraud explanation does not win merely because a pattern is anomalous.

3. DISTINGUISH:

KNOWN:
directly supported by supplied evidence.

INFERENCE:
a conclusion drawn from known evidence.

UNKNOWN:
something not established by the available evidence.

Never silently convert an UNKNOWN into a KNOWN fact.

4. SEARCH FOR THE STRONGEST LEGITIMATE ALTERNATIVE.

Ask whether contracts, recurring commercial relationships, normal accounting
processes, timing effects, or other ordinary mechanisms could explain the
observed facts.

But do NOT invent those mechanisms as facts.
If they must be checked, request an action.

5. ALSO SEARCH FOR ALTERNATIVE SUSPICIOUS HYPOTHESES.

Destroying the target hypothesis does NOT imply that the case should close.

For example, evidence inconsistent with threshold splitting could still
support kickback or another mechanism.

When a materially different suspicious explanation emerges, return:

outcome = "alternative_hypothesis"

and describe it explicitly.

6. USE EXISTING CONTRARY EVIDENCE.

If evidence already present in the CaseState conflicts with the hypothesis,
identify it using its real evidence_id.

Do not invent evidence IDs.

7. DO NOT INVENT LAW OR POLICY.

Never invent:
- statutes,
- SAT requirements,
- CNBV rules,
- accounting requirements,
- approval thresholds,
- procurement rules,
- contractual requirements,
- legal consequences.

A legal, regulatory, accounting, or internal-control rule may only be used
if it is explicitly supplied in the input context.

Absence of a supplied rule means the rule is UNKNOWN.

8. EFOS STATUS IS EVIDENCE, NOT AUTOMATIC PROOF.

A vendor appearing in an EFOS-related record does not by itself prove that
every transaction involving that vendor is fraudulent.

Status, dates, transaction-specific evidence, and mechanism still matter.

9. MULTIPLE SIGNALS ARE NOT AUTOMATICALLY INDEPENDENT EVIDENCE.

Two detectors may derive their results from the exact same source records.
Do not count them as independent merely because two algorithms produced them.

10. ONLY REQUEST AVAILABLE ACTIONS.

You may ONLY propose action names included in AVAILABLE ACTIONS.

Never invent tools.

The program will reject invented actions.

11. OUTCOME SEMANTICS

"survives":
No material unresolved objection was identified using the currently
available evidence and tools.

This DOES NOT mean:
- irrefutable,
- guilty,
- fraud proven,
- finding authorized.

"needs_more_evidence":
A material and resolvable uncertainty remains.
You MUST identify the question and propose an available action that could
resolve it.

"legitimate_alternative":
Available evidence materially supports a legitimate explanation that
undermines the TARGET hypothesis.

This only weakens or rejects the target hypothesis.
It does not automatically close the entire case.

"alternative_hypothesis":
The target hypothesis appears materially incomplete or incorrect, but the
evidence supports opening another suspicious hypothesis.

12. BE SPECIFIC.

Do not write:
"Investigate further."

Instead explain:
- what is unknown,
- why it could change the interpretation,
- which available action can resolve it.

13. START FROM THE ALTERNATIVES THE DETECTORS ALREADY DECLARED.

You are given `legitimate_alternatives_declared_by_detectors`. Whoever built each
detector wrote down, in advance, the innocent explanations that produce the same
signal, the limits of what the signal shows, and the checks that would settle it.

That is your starting point, not an afterthought. Work through those first, and only
then look for an alternative nobody anticipated. An alternative the detector itself
warned about and that nobody checked is the strongest objection available to you.

14. DEMAND DISCRIMINATING EVIDENCE, NOT MORE EVIDENCE.

Before proposing any action, ask: "will the two candidate explanations predict
DIFFERENT results for this action?"

If both the fraud explanation and the innocent explanation predict the same result,
the action is worthless no matter how much data it returns. Say what result would
point which way. An action whose outcome cannot change your mind must not be proposed.

15. NAME THE TUNNEL VISION.

Check whether the investigation only ever looked for confirmation:

- Were any actions taken that could have WEAKENED the hypothesis, or only ones that
  could strengthen it?
- Did the hypothesis change at all as evidence arrived, or was the conclusion fixed
  from the first observation?
- Is the subject being investigated because the evidence points there, or because it
  was the first entity a detector surfaced?

If every action taken could only confirm, say so explicitly -- that is a finding
about the investigation, not about the subject.

16. KEEP THE FOUR LEVELS SEPARATE.

- ANOMALY: a pattern that differs from its peers. Not yet a reason to suspect anyone.
- SUSPICION: a reason to look, not a claim about what happened.
- HYPOTHESIS: a specific mechanism proposed, still to be tested.
- VERIFIED FACT: a record in the supplied estate, or arithmetic over those records.

Most bad reasoning in this system is a silent promotion between two of these levels.
When you object, name which level the claim actually sits at and which level it is
being treated as.

17. DO NOT BLOCK A SOUND HYPOTHESIS WITH A REMOTE POSSIBILITY.

You are not here to make accusations impossible. A conceivable-but-unsupported story
is not a legitimate alternative -- a legitimate alternative is one the AVAILABLE
EVIDENCE materially supports.

If the evidence is documented, independent, and the obvious innocent explanations have
been checked and failed, return "survives". Inventing a remote possibility to avoid
committing is as much a failure as waving through a weak hypothesis. Both put the
wrong case in front of a judge.

Use "survives" when no material unresolved objection remains. Use
"needs_more_evidence" only when a SPECIFIC available action could resolve a SPECIFIC
material uncertainty. Use "legitimate_alternative" only when the available evidence
materially supports the innocent explanation -- not merely when you can imagine one.

18. IN reasoning_summary, SHOW WHAT YOU TRIED.

Even when the hypothesis survives, state the strongest legitimate alternative you
considered and why the available evidence does not support it. A hypothesis nobody
tried to break is weaker than one that was attacked and held, and the case file has
to show the attack. Write it so a non-technical reader can follow it.

19. RETURN ONLY VALID JSON, AND FILL EVERY NESTED FIELD.

Measured failures in live runs, both of which killed the whole case:

* `missing_counterevidence[0].why_it_matters` was simply absent. Every nested
  object below is required in full. A question without `why_it_matters` is not a
  smaller answer, it is an unusable one -- the whole point is why it would change
  the interpretation.
* `scheme_type` was the STRING "null". Use a real JSON null, not the word.

A response that fails to parse is neither a finding nor a reasoned decline. It is a
hole in the case file, and it is the worst outcome you can produce -- worse than
being wrong, because nobody can see what you thought.

This is the exact shape. Copy it:

{
  "target_hypothesis_id": "<the id you were asked to review, verbatim>",
  "outcome": "survives" | "needs_more_evidence" | "legitimate_alternative" | "alternative_hypothesis",
  "strongest_legitimate_alternative": "<text, or null>",
  "unsupported_claims": [
    {"claim": "...", "why_unsupported": "...", "what_would_support_it": "..."}
  ],
  "missing_counterevidence": [
    {"question": "...", "why_it_matters": "..."}
  ],
  "contradictions": [
    {"statement": "...", "evidence_ids": ["EV-..."], "why_it_conflicts": "..."}
  ],
  "proposed_actions": [
    {"action_name": "<exact name from available_actions>",
     "arguments": {"<required arg>": "<value>"},
     "reason": "...",
     "question_resolved": "..."}
  ],
  "alternative_hypotheses": [
    {"statement": "...", "scheme_type": null, "reason": "...",
     "supporting_evidence_ids": ["EV-..."]}
  ],
  "reasoning_summary": "..."
}

Omit a LIST entirely if it is empty. Never omit a field inside an object you did
include. `scheme_type` must be JSON null or one of the five official scheme names.

Your output must conform exactly to the supplied ChallengerReview schema.
Do not place prose outside the JSON.
""".strip()


def _serialize_action(action: Any) -> dict[str, Any]:
    """
    Convert an Action Bank definition into prompt-safe JSON.

    Supports Pydantic models, dataclasses/dict-like definitions,
    and ordinary dictionaries.
    """

    if isinstance(action, dict):
        return action

    if hasattr(action, "model_dump"):
        return action.model_dump(mode="json")

    if hasattr(action, "__dict__"):
        return dict(action.__dict__)

    raise TypeError(
        f"Unsupported action definition type: {type(action).__name__}"
    )


def _action_name(action: Any) -> str | None:
    if isinstance(action, dict):
        return action.get("name") or action.get("action_name")

    return (
        getattr(action, "name", None)
        or getattr(action, "action_name", None)
    )


def _required_arguments(action: Any) -> set[str]:
    if isinstance(action, dict):
        values = action.get("required_arguments", [])
    else:
        values = getattr(action, "required_arguments", [])

    return set(values or [])


def _get_target_hypothesis(
    case_state: CaseState,
    target_hypothesis_id: str,
):
    for hypothesis in case_state.hypotheses:
        if hypothesis.hypothesis_id == target_hypothesis_id:
            return hypothesis

    raise ValueError(
        f"Hypothesis {target_hypothesis_id!r} does not exist "
        f"in case {case_state.case_id!r}."
    )


def build_challenger_prompt(
    case_state: CaseState,
    target_hypothesis_id: str,
    available_actions: list[Any],
) -> tuple[str, str]:
    """
    Build separate system and user prompts for the shared LLM client.
    """

    target_hypothesis = _get_target_hypothesis(
        case_state,
        target_hypothesis_id,
    )

    schema = ChallengerReview.model_json_schema()

    action_context = [
        _serialize_action(action)
        for action in available_actions
    ]

    # El orden importa: lo que el Challenger debe leer primero va primero.
    # Las alternativas que los detectores ya declararon son el punto de partida
    # de su trabajo y antes quedaban enterradas dentro del volcado del CaseState.
    # Ver src/agents/case_analysis.py.
    user_payload = {
        "target_hypothesis": target_hypothesis.model_dump(mode="json"),
        "legitimate_alternatives_declared_by_detectors": collect_declared_alternatives(
            case_state
        ),
        "deterministic_pre_analysis": build_case_briefing(case_state),
        "available_actions": action_context,
        "case_state": case_state.model_dump(mode="json"),
        "required_output_schema": schema,
    }

    user_prompt = json.dumps(
        user_payload,
        indent=2,
        ensure_ascii=False,
    )

    return CHALLENGER_SYSTEM_PROMPT, user_prompt


def parse_challenger_review(raw_response: str) -> ChallengerReview:
    """
    Parse and validate the structured Challenger response.
    """

    text = strip_code_fences(raw_response)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Challenger returned invalid JSON."
        ) from exc

    return ChallengerReview.model_validate(payload)


def validate_challenger_review(
    review: ChallengerReview,
    *,
    case_state: CaseState,
    target_hypothesis_id: str,
    available_actions: list[Any],
) -> None:
    """
    Deterministically validate everything the LLM is not trusted to enforce.
    """

    if review.target_hypothesis_id != target_hypothesis_id:
        raise ValueError(
            "Challenger reviewed a different hypothesis than requested: "
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
                "Challenger requested an unavailable action: "
                f"{proposed.action_name!r}."
            )

        required = _required_arguments(
            allowed_actions[proposed.action_name]
        )

        missing = required - set(proposed.arguments)

        if missing:
            raise ValueError(
                f"Action {proposed.action_name!r} is missing "
                f"required arguments: {sorted(missing)}."
            )

    valid_evidence_ids = {
        evidence.evidence_id
        for evidence in case_state.evidence
    }

    for contradiction in review.contradictions:
        unknown_ids = (
            set(contradiction.evidence_ids)
            - valid_evidence_ids
        )

        if unknown_ids:
            raise ValueError(
                "Challenger referenced nonexistent evidence IDs "
                f"in contradiction: {sorted(unknown_ids)}."
            )

    for alternative in review.alternative_hypotheses:
        unknown_ids = (
            set(alternative.supporting_evidence_ids)
            - valid_evidence_ids
        )

        if unknown_ids:
            raise ValueError(
                "Challenger referenced nonexistent evidence IDs "
                f"in alternative hypothesis: {sorted(unknown_ids)}."
            )


def challenge_case(
    case_state: CaseState,
    target_hypothesis_id: str,
    available_actions: list[Any],
    llm_client: LLMClient,
) -> ChallengerReview:
    """
    Adversarially review one hypothesis from a CaseState.

    The LLM proposes the review.
    Python validates the structural and provenance constraints.
    """

    _get_target_hypothesis(
        case_state,
        target_hypothesis_id,
    )

    system_prompt, user_prompt = build_challenger_prompt(
        case_state=case_state,
        target_hypothesis_id=target_hypothesis_id,
        available_actions=available_actions,
    )

    raw_response = llm_client.complete(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    review = parse_challenger_review(raw_response)

    validate_challenger_review(
        review,
        case_state=case_state,
        target_hypothesis_id=target_hypothesis_id,
        available_actions=available_actions,
    )

    return review