"""
src/agents/challenger.py
========================
Case Critic Agent — "The Challenger"

Role: Red-team adversarial agent that takes a forensic hypothesis produced by
another agent and tries to destroy it by surfacing the most plausible *legitimate*
alternative explanation. Only when no credible alternative can be found does the
hypothesis "survive the challenge" (survives_challenge=True).

Architecture note
-----------------
This module is intentionally decoupled from the LLM call. The function
`challenge_case` assembles a structured prompt, then invokes the model.  For now,
the LLM call is STUBBED: the function returns a deterministic mock so the rest of
the pipeline can be integrated and tested end-to-end without API credentials.

Replace the "--- MOCK ZONE ---" block with a real `genai` call when ready.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# TASK 1: Pydantic Data Models
# ---------------------------------------------------------------------------


class UnsupportedClaim(BaseModel):
    """A specific claim inside the hypothesis that lacks sufficient evidentiary
    support from the available database facts."""

    claim: str = Field(
        ...,
        description="Verbatim or paraphrased claim from the original hypothesis.",
    )
    why_unsupported: str = Field(
        ...,
        description=(
            "Concrete reason why this claim cannot be confirmed with the "
            "evidence currently on record (missing documents, alternative "
            "causal path, etc.)."
        ),
    )
    what_would_support_it: str = Field(
        ...,
        description=(
            "The specific evidence or document that, if found, would make "
            "this claim defensible."
        ),
    )


class MissingCounterevidence(BaseModel):
    """An open investigative question whose answer could either refute or
    corroborate the hypothesis."""

    question: str = Field(
        ...,
        description="The unanswered question that could change the verdict.",
    )
    why_it_matters: str = Field(
        ...,
        description=(
            "How the answer to this question would affect the probability "
            "of fraud vs. a legitimate explanation."
        ),
    )
    suggested_action: str = Field(
        ...,
        description=(
            "The tool or investigative step that should be executed next "
            "(e.g., check_vendor_contracts, pull_gl_entries, cross_check_efos)."
        ),
    )


class ProposedAction(BaseModel):
    """A concrete, executable next step for the orchestrator to queue."""

    action_name: str = Field(
        ...,
        description="Machine-readable action identifier (snake_case).",
    )
    arguments: dict = Field(
        default_factory=dict,
        description="Keyword arguments to pass to the action handler.",
    )
    reason: str = Field(
        ...,
        description="Why this action is the highest-value move right now.",
    )
    priority: int = Field(
        ...,
        ge=1,
        le=10,
        description="Urgency score 1 (low) to 10 (critical).",
    )


class ChallengerReview(BaseModel):
    """
    The complete adversarial review produced by the Case Critic.

    Interpretation guide
    --------------------
    - survives_challenge=True  -> hypothesis is irrefutable given current evidence;
                                   the orchestrator may escalate to human review or
                                   generate a formal finding.
    - survives_challenge=False -> at least one credible legitimate alternative
                                   exists; proposed_actions MUST be pursued before
                                   any fraud conclusion is drawn.
    """

    strongest_legitimate_alternative: str = Field(
        ...,
        description=(
            "The single most plausible non-fraudulent explanation for the "
            "observed pattern.  Must reference specific, named legal constructs "
            "(contract type, tariff, regulatory threshold, etc.)."
        ),
    )
    unsupported_claims: list[UnsupportedClaim] = Field(
        default_factory=list,
        description="Claims in the original hypothesis that lack hard evidence.",
    )
    missing_counterevidence: list[MissingCounterevidence] = Field(
        default_factory=list,
        description="Open questions whose answers could flip the verdict.",
    )
    proposed_actions: list[ProposedAction] = Field(
        default_factory=list,
        description="Ordered list of next investigative actions for the orchestrator.",
    )
    survives_challenge: bool = Field(
        ...,
        description=(
            "True only when the hypothesis is irrefutable under Occam's razor "
            "AND all counterevidence gaps have been closed."
        ),
    )
    reasoning_summary: str = Field(
        ...,
        description=(
            "Free-form 3-5 sentence narrative explaining the Challenger's "
            "overall assessment and the decisive factor in the verdict."
        ),
    )


# ---------------------------------------------------------------------------
# TASK 2: Adversarial System Prompt
# ---------------------------------------------------------------------------

CHALLENGER_SYSTEM_PROMPT: str = """
You are the Case Critic, an adversarial forensic auditor acting as the Red Team
inside a multi-agent audit system.

YOUR SOLE PURPOSE is to challenge hypotheses produced by other agents. You do NOT
investigate fraud yourself — you protect the integrity of the investigation by
ensuring that no conclusion is drawn without exhausting every legitimate alternative.

=== GOLDEN RULES ===

1. NEVER deny database facts. You may only challenge their *interpretation*.
   A payment of $89,500 exists — that is a fact.
   That the payment is fraudulent — that is an interpretation you must challenge.

2. ALWAYS seek the strongest legitimate alternative first.
   Before concluding misconduct, ask: could this be a fixed-fee retainer? a
   regulatory threshold (CNBV, SHCP, SAT)? a framework contract with monthly
   deliverables? a batch payroll run? a statutory fee schedule?

3. APPLY Occam's Razor in favor of legality.
   If a contract, tariff table, or business norm explains the pattern without
   requiring collusion, that explanation wins until disproven.

4. survives_challenge=True is a HIGH BAR.
   Only set it when ALL of the following are true:
     a) No plausible legitimate structure (contract, tariff, schedule) fits the
        pattern.
     b) The documentary gaps themselves are anomalous (e.g., missing POs that
        should legally exist under SAT rules for amounts > $2,000 MXN).
     c) The statistical signals converge across at least two independent
        detectors (e.g., entropy collapse AND temporal burst).
   When in doubt, set survives_challenge=False and propose an action.

5. PROPOSED ACTIONS must be specific and executable.
   Bad:  "Investigate further."
   Good: "Execute check_vendor_contracts(vendor_id='VEN-007', min_amount=89500)
          to verify the existence of a framework contract covering these payments."

6. OUTPUT FORMAT: You MUST respond with a single, valid JSON object that conforms
   exactly to the ChallengerReview schema. No prose outside the JSON block.

=== SCHEMA REFERENCE ===
{schema}
""".strip()


def _build_challenger_prompt(case_state: dict, available_actions: list) -> str:
    """
    Assembles the full prompt (system + user turn) that will be sent to the LLM.

    Parameters
    ----------
    case_state:
        Serializable dict describing the current case: hypothesis, evidence,
        detector outputs, Bayesian posteriors, etc.
    available_actions:
        List of action descriptors the orchestrator exposes to the Challenger.

    Returns
    -------
    str
        Complete prompt text ready for the LLM call.
    """
    schema_json = json.dumps(ChallengerReview.model_json_schema(), indent=2)
    system_block = CHALLENGER_SYSTEM_PROMPT.replace("{schema}", schema_json)

    user_block = (
        "=== CASE STATE ===\n"
        + json.dumps(case_state, indent=2, ensure_ascii=False)
        + "\n\n=== AVAILABLE ACTIONS ===\n"
        + json.dumps(available_actions, indent=2, ensure_ascii=False)
        + "\n\nNow produce your ChallengerReview JSON."
    )

    return f"{system_block}\n\n{user_block}"


# ---------------------------------------------------------------------------
# TASK 3: Main Function
# ---------------------------------------------------------------------------


def challenge_case(
    case_state: dict,
    available_actions: list,
    *,
    model: str = "gemini-2.5-pro",
    _mock: bool = True,
) -> ChallengerReview:
    """
    Challenge a forensic hypothesis and return a structured adversarial review.

    Parameters
    ----------
    case_state:
        Dict with at minimum:
            - "hypothesis": str — the claim being challenged
            - "evidence": list[dict] — detector observations / DB facts
            - "bayesian_posterior": float — P(Anomaly | Evidence)
    available_actions:
        List of action dicts the orchestrator can execute, e.g.:
            [{"name": "check_vendor_contracts", "description": "..."}]
    model:
        Gemini model identifier (used when _mock=False).
    _mock:
        If True (default during development), returns a deterministic mock
        without calling the LLM. Set to False in production.

    Returns
    -------
    ChallengerReview
        Validated Pydantic object ready for the orchestrator.
    """
    # Build the prompt regardless of mock mode.
    # This validates the assembly logic and lets us inspect the exact text
    # that WOULD be sent to the LLM.
    prompt_text = _build_challenger_prompt(case_state, available_actions)

    # -------------------------------------------------------------------------
    # --- MOCK ZONE ---
    # Replace this entire block with the real Gemini call:
    #
    #   import google.genai as genai
    #   client = genai.Client()
    #   response = client.models.generate_content(
    #       model=model,
    #       contents=prompt_text,
    #       config=genai.types.GenerateContentConfig(
    #           response_mime_type="application/json",
    #           response_schema=ChallengerReview,
    #       ),
    #   )
    #   return response.parsed
    #
    # -------------------------------------------------------------------------
    if _mock:
        # Scenario: 12 identical payments flagged as THRESHOLD_SPLITTING.
        # The Challenger suspects a legitimate fixed-fee service contract and
        # demands verification before any fraud conclusion is drawn.
        mock_review = ChallengerReview(
            strongest_legitimate_alternative=(
                "The 12 uniform payments of $89,500 MXN each are consistent with "
                "a fixed-fee professional services retainer (contrato de honorarios "
                "a tarifa fija) — a common SAT-compliant structure for monthly "
                "consulting, IT maintenance, or outsourced payroll services. Under "
                "Article 27 of the CFF, such contracts are valid and do NOT require "
                "individual purchase orders when a framework agreement exists."
            ),
            unsupported_claims=[
                UnsupportedClaim(
                    claim=(
                        "12 payments of identical amount to the same vendor within "
                        "a single fiscal year constitute threshold-splitting to avoid "
                        "approval controls."
                    ),
                    why_unsupported=(
                        "Identical monthly amounts are the defining characteristic of "
                        "a fixed-fee retainer, not exclusively of threshold-splitting. "
                        "No framework contract has been queried yet; absence of evidence "
                        "in the purchase_orders table does NOT rule out a contract-based "
                        "arrangement that legally waives PO requirements."
                    ),
                    what_would_support_it=(
                        "A confirmed absence of any active contract in the contracts "
                        "table covering this vendor and fiscal year, AND a second signal "
                        "such as the vendor appearing in the EFOS/69-B list or having "
                        "a registered address mismatch."
                    ),
                ),
            ],
            missing_counterevidence=[
                MissingCounterevidence(
                    question=(
                        "Does a valid framework contract exist between the audited "
                        "entity and this vendor covering the period of the 12 payments?"
                    ),
                    why_it_matters=(
                        "If a contract with a monthly fee of $89,500 MXN exists and "
                        "was registered before the first payment, the entire pattern "
                        "is legally expected behavior — not an anomaly. This single "
                        "lookup could exonerate the vendor entirely."
                    ),
                    suggested_action="check_vendor_contracts",
                ),
                MissingCounterevidence(
                    question=(
                        "Is this vendor currently listed in the SAT EFOS (69-B) "
                        "definitive or presumed list?"
                    ),
                    why_it_matters=(
                        "EFOS membership is a hard disqualifying fact that overrides "
                        "any contract defense. If the vendor is on the list, "
                        "survives_challenge would flip to True regardless of contracts."
                    ),
                    suggested_action="cross_check_efos",
                ),
            ],
            proposed_actions=[
                ProposedAction(
                    action_name="check_vendor_contracts",
                    arguments={
                        "vendor_id": case_state.get("vendor_id", "UNKNOWN"),
                        "fiscal_year": case_state.get("fiscal_year", 2024),
                        "min_monthly_amount": 89_500.00,
                    },
                    reason=(
                        "Highest-value action: a positive result immediately provides "
                        "a complete legitimate explanation and closes the case; a "
                        "negative result materially strengthens the fraud hypothesis."
                    ),
                    priority=9,
                ),
                ProposedAction(
                    action_name="cross_check_efos",
                    arguments={
                        "vendor_id": case_state.get("vendor_id", "UNKNOWN"),
                        "rfc": case_state.get("vendor_rfc", "UNKNOWN"),
                    },
                    reason=(
                        "EFOS status is a binary, authoritative disqualifier. "
                        "Checking it in parallel with contracts costs nothing and "
                        "could instantly elevate severity."
                    ),
                    priority=8,
                ),
            ],
            survives_challenge=False,
            reasoning_summary=(
                "The entropy-collapse signal (H_norm=0.00) and temporal clustering "
                "of 12 identical payments are statistically significant, but they "
                "are also the expected fingerprint of a legitimate monthly fixed-fee "
                "retainer. The hypothesis does NOT survive the challenge at this stage "
                "because the contracts table has not been queried. Until a contract "
                "lookup returns negative AND EFOS status is confirmed, declaring "
                "fraud would violate the presumption of legality. The orchestrator "
                "MUST execute check_vendor_contracts before any escalation."
            ),
        )
        return mock_review

    # Production path (reached only when _mock=False)
    raise NotImplementedError(
        "Live LLM call not yet wired. Set _mock=True or replace this block "
        "with a google.genai call as described in the MOCK ZONE comment above."
    )


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample_case_state = {
        "hypothesis": (
            "Vendor VEN-007 received 12 identical payments of $89,500 MXN in FY-2024, "
            "exhibiting entropy collapse (H_norm=0.00) and no matching purchase orders. "
            "Bayesian posterior P(Anomaly|Evidence)=0.87. Pattern is consistent with "
            "threshold-splitting to avoid $100,000 approval controls."
        ),
        "vendor_id": "VEN-007",
        "vendor_rfc": "ABC123456789",
        "fiscal_year": 2024,
        "bayesian_posterior": 0.87,
        "detector_signals": [
            {"type": "ENTROPY_COLLAPSE_STRUCTURED_PATTERN", "score": 1.0},
            {"type": "MULTIDIMENSIONAL_ISOLATION_ANOMALY", "score": 0.91},
        ],
        "evidence_summary": {
            "total_paid": 1_074_000.00,
            "payment_count": 12,
            "unique_amounts": 1,
            "purchase_orders_found": 0,
            "contracts_checked": False,
            "efos_checked": False,
        },
    }

    sample_available_actions = [
        {
            "name": "check_vendor_contracts",
            "description": (
                "Query the contracts table for active agreements with a given vendor."
            ),
        },
        {
            "name": "cross_check_efos",
            "description": (
                "Verify if a vendor RFC appears in the SAT 69-B EFOS definitive list."
            ),
        },
        {
            "name": "pull_gl_entries",
            "description": (
                "Pull general ledger entries linked to a vendor or invoice set."
            ),
        },
        {
            "name": "request_human_review",
            "description": (
                "Escalate the case to a human auditor for final determination."
            ),
        },
    ]

    review: ChallengerReview = challenge_case(
        case_state=sample_case_state,
        available_actions=sample_available_actions,
        _mock=True,
    )

    print("=" * 72)
    print("CHALLENGER REVIEW — Case Critic Output")
    print("=" * 72)
    print(review.model_dump_json(indent=2))
    print("=" * 72)
    print(f"  survives_challenge : {review.survives_challenge}")
    print(f"  top action         : {review.proposed_actions[0].action_name}")
    print(f"  top priority       : {review.proposed_actions[0].priority}/10")
    print("=" * 72)
