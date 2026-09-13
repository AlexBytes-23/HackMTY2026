"""Text hygiene for JSON that arrives from a language model.

One shared helper, used by every parser that turns raw model text into a
validated Pydantic contract.  Gemini routinely wraps its JSON in a markdown
code fence even when asked for ``application/json``, so a parser that calls
``json.loads`` on the raw response fails on the very first live call and the
whole case is recorded as an execution error.

This module performs no validation and no parsing.  It only removes the fence.
"""

from __future__ import annotations

import re

FENCE = "```"

# An opening fence may carry a language tag (```json) and is normally followed by
# a newline; a single-line block has none.
_OPENING_FENCE = re.compile(r"\A```[ \t]*[A-Za-z0-9_.+-]*[ \t]*\r?\n?")

# A closing fence sits at the very end, usually on its own line, and the model
# may leave one or more blank lines before it.
_CLOSING_FENCE = re.compile(r"\s*```\s*\Z")


def strip_code_fences(text: str) -> str:
    """Return ``text`` with a surrounding markdown code fence removed.

    Handles the shapes a model actually produces: no fence at all, a bare
    ``` fence, a ```json fence, blank lines before the closing fence, and a
    single-line fenced block.  Text that merely *contains* a backtick is
    returned untouched, because only a fence at the very start is a fence.
    """

    cleaned = (text or "").strip()

    if not cleaned.startswith(FENCE):
        return cleaned

    cleaned = _OPENING_FENCE.sub("", cleaned, count=1)
    cleaned = _CLOSING_FENCE.sub("", cleaned, count=1)

    return cleaned.strip()
