"""The parsers must survive the markdown fence Gemini puts around its JSON.

A fenced response used to make ``parse_investigator_decision`` raise, so the very
first live call turned a perfectly good decision into an execution error and the
case was closed as a program failure.
"""

from __future__ import annotations

import json

import pytest

from src.agents.challenger import parse_challenger_review
from src.agents.method_critic import parse_method_critic_review
from src.investigation.investigator import parse_investigator_decision
from src.llm.json_text import strip_code_fences


PAYLOAD = '{"a": 1}'


@pytest.mark.parametrize(
    "raw,expected",
    [
        # No fence at all.
        (PAYLOAD, PAYLOAD),
        # Leading and trailing whitespace only.
        ("\n  " + PAYLOAD + "  \n", PAYLOAD),
        # A bare fence.
        ("```\n" + PAYLOAD + "\n```", PAYLOAD),
        # A fence with a language tag.
        ("```json\n" + PAYLOAD + "\n```", PAYLOAD),
        # Blank lines before the closing fence.
        ("```json\n" + PAYLOAD + "\n\n\n```", PAYLOAD),
        # No newline before the closing fence.
        ("```json\n" + PAYLOAD + "```", PAYLOAD),
        # A single-line fenced block.
        ("```json " + PAYLOAD + "```", PAYLOAD),
        # A fence with trailing prose whitespace after it.
        ("```json\n" + PAYLOAD + "\n```   \n", PAYLOAD),
    ],
)
def test_strip_code_fences_handles_every_shape_a_model_produces(raw, expected):
    assert strip_code_fences(raw) == expected


def test_text_that_merely_contains_a_backtick_is_untouched():
    """Only a fence at the very start is a fence."""

    raw = '{"note": "the reference field contains a ` character"}'
    assert strip_code_fences(raw) == raw

    # Backticks inside the JSON, and not at position 0, are content.
    embedded = '{"snippet": "```json```"}'
    assert strip_code_fences(embedded) == embedded


def test_strip_code_fences_tolerates_empty_input():
    assert strip_code_fences("") == ""
    assert strip_code_fences("   ") == ""


# ==========================================================================
# THE THREE PARSERS
# ==========================================================================

def _decision() -> dict:
    return {
        "decision": "close_inconclusive",
        "current_assessment": "The estate does not settle the question.",
        "hypotheses": [],
        "next_action": None,
        "new_unknowns": [],
        "resolved_unknowns": [],
        "reason": "No remaining action has informative value.",
    }


def test_parse_investigator_decision_accepts_a_fenced_decision():
    fenced = "```json\n" + json.dumps(_decision()) + "\n```"

    decision = parse_investigator_decision(fenced)

    assert decision.decision == "close_inconclusive"
    assert decision.next_action is None


def test_parse_investigator_decision_still_rejects_text_that_is_not_json():
    with pytest.raises(ValueError, match="JSON"):
        parse_investigator_decision("```json\nnot json at all\n```")


def test_parse_challenger_review_accepts_a_fenced_review():
    review = parse_challenger_review(
        "```json\n"
        + json.dumps(
            {
                "target_hypothesis_id": "H-001",
                "outcome": "survives",
                "reasoning_summary": "No material unresolved objection was found.",
            }
        )
        + "\n```"
    )

    assert review.target_hypothesis_id == "H-001"
    assert review.outcome == "survives"


def test_parse_method_critic_review_accepts_a_bare_fence():
    """The Method Critic's old parser only stripped ```json, never a bare fence."""

    review = parse_method_critic_review(
        "```\n"
        + json.dumps(
            {
                "target_hypothesis_id": "H-001",
                "outcome": "clear",
                "reasoning_summary": "The method is record matching, not inference.",
            }
        )
        + "\n```"
    )

    assert review.outcome == "clear"
