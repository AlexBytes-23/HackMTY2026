"""Score one audit run against the answer key.

DEV / EVAL ONLY. Reads ground truth; nothing in ``src/`` may import this.

Measures the things that actually decide the score, and separates them so a
gain in one cannot hide a loss in another:

  DETECTION   schemes that produced a finding, and schemes that at least
              raised a lead.
  SAFETY      accusations against entities the answer key says are honest;
              decoys that were escalated; and -- the number that keeps us
              honest -- decoys no detector could fire on at all, because a
              decoy nobody trips flatters the false-positive rate through
              blindness rather than judgement.
  REASONING   whether a closed lead names the document that cleared it,
              whether the same lookup was made twice, whether cases were left
              inconclusive rather than forced.
  COST        calls, MXN and wall clock, per role, measured from the recording.

Usage:
    python dev_eval/score_run.py --submission runs/x/submission.json \\
        --ground-truth dev_eval/robust_realism_ground_truth.json \\
        [--answers dev_eval/robust_realism_estate_answers.db] \\
        [--recording recordings/x.jsonl] [--label BEFORE] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path

# A reason that merely asserts a conclusion is scored as generic. These are the
# phrases the challenge calls out plus the ones our own builders can emit.
GENERIC_REASON_MARKERS = (
    "insufficient evidence", "not enough evidence", "no evidence",
    "inconclusive", "unclear", "needs more work", "cannot determine",
)

# A lead reason earns "specific" only if it points at something in the estate.
SPECIFIC_REASON_MARKERS = (
    "invoice", "contract", "purchase order", "purchase_order", "ledger",
    "bank", "clabe", "account", "employee", "vendor", "efos",
    "publication", "evidence gate", "CTR-", "PO-", "BNK-", "RFC:", "EMP:",
)


@dataclass
class Score:
    label: str = ""
    estate_seed: int | None = None

    # detection
    schemes_total: int = 0
    schemes_with_finding: int = 0
    schemes_signalled: int = 0
    scheme_recall: float = 0.0
    scheme_detail: dict = field(default_factory=dict)

    # safety
    findings_total: int = 0
    unsupported_accusations: int = 0
    unsupported_detail: list = field(default_factory=list)
    decoys_total: int = 0
    decoys_triggered: int = 0
    decoys_untriggered: int = 0
    decoys_accused: int = 0
    false_accusation_rate: float = 0.0

    # reasoning
    leads_closed: int = 0
    leads_with_specific_reason: int = 0
    leads_with_generic_reason: int = 0
    leads_naming_clearing_document: int = 0
    legitimate_alternatives_found: int = 0
    correctly_inconclusive: int = 0
    closed_by_breakdown: dict = field(default_factory=dict)

    # actions
    tool_calls_total: int = 0
    tool_calls_unique: int = 0
    redundant_actions: int = 0
    redundancy_rate: float = 0.0
    leads_with_no_lookup: int = 0

    # cost
    llm_calls: int = 0
    mxn_cost: float = 0.0
    wall_clock_seconds: float = 0.0
    calls_by_role: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0


def _norm(entity: str) -> str:
    return (entity or "").strip()


def classify_role(response_text: str) -> str:
    """Which agent produced this response. Robust across prompt versions."""
    try:
        blob = response_text.strip()
        match = re.search(r"\{.*\}", blob, re.S)
        payload = json.loads(match.group(0)) if match else {}
    except Exception:
        payload = {}
    if "decision" in payload or "requested_action" in payload:
        return "investigator"
    if "survives_challenge" in payload or "legitimate_alternative" in str(
            payload.get("outcome", "")):
        return "challenger"
    if "outcome" in payload:
        # Both critics carry `outcome`; only the challenger carries the
        # survival flag, so anything left here is the method critic.
        return "method_critic"
    return "unknown"


def score(submission: dict, truth: dict, decoy_codes: dict,
          recording: list[dict], label: str) -> Score:
    s = Score(label=label, estate_seed=truth.get("seed"))
    company = "RFC:" + truth.get("company_rfc", "")

    scheme_entities: dict[str, list[str]] = {}
    for scheme in truth["schemes"]:
        for entity in scheme["entities"]:
            if entity == company:
                continue
            scheme_entities.setdefault(_norm(entity), []).append(scheme["scheme_id"])
    decoy_entities = {_norm(d["entity"]): d for d in truth["decoys"]}

    findings = submission.get("findings", [])
    leads = submission.get("leads_not_pursued", [])
    s.findings_total = len(findings)
    s.decoys_total = len(decoy_entities)
    s.schemes_total = len(truth["schemes"])

    # ---- detection -------------------------------------------------------
    accused: set[str] = set()
    for finding in findings:
        for entity in finding.get("entities", []):
            accused.add(_norm(entity))

    touched = accused | {_norm(l.get("entity", "")) for l in leads}

    hit_schemes, signalled_schemes = set(), set()
    for scheme in truth["schemes"]:
        ents = [_norm(e) for e in scheme["entities"] if _norm(e) != company]
        found = bool(accused.intersection(ents))
        seen = bool(touched.intersection(ents))
        if found:
            hit_schemes.add(scheme["scheme_id"])
        if seen:
            signalled_schemes.add(scheme["scheme_id"])
        s.scheme_detail[scheme["scheme_id"]] = {
            "type": scheme["type"],
            "difficulty": scheme.get("difficulty"),
            "finding": found,
            "signalled": seen,
        }
    s.schemes_with_finding = len(hit_schemes)
    s.schemes_signalled = len(signalled_schemes)
    s.scheme_recall = round(s.schemes_with_finding / max(s.schemes_total, 1), 3)

    # ---- safety ----------------------------------------------------------
    for entity in sorted(accused):
        if entity == company or entity in scheme_entities:
            continue
        s.unsupported_accusations += 1
        s.unsupported_detail.append({
            "entity": entity,
            "answer_key": ("decoy " + decoy_codes.get(entity, "?")
                           if entity in decoy_entities else "honest, unnamed"),
        })
    s.decoys_accused = len([e for e in accused if e in decoy_entities])
    s.decoys_triggered = len([e for e in decoy_entities if e in touched])
    s.decoys_untriggered = s.decoys_total - s.decoys_triggered
    denom = max(len(accused - {company}), 1)
    s.false_accusation_rate = round(s.unsupported_accusations / denom, 3)

    # ---- reasoning -------------------------------------------------------
    s.leads_closed = len(leads)
    closed_by = Counter()
    for lead in leads:
        entity = _norm(lead.get("entity", ""))
        reason = str(lead.get("reason", ""))
        low = reason.lower()
        closed_by[lead.get("closed_by") or "unspecified"] += 1

        specific = (any(m in low for m in SPECIFIC_REASON_MARKERS)
                    and len(reason.split()) >= 8)
        generic = any(m in low for m in GENERIC_REASON_MARKERS)
        if specific:
            s.leads_with_specific_reason += 1
        if generic and not specific:
            s.leads_with_generic_reason += 1

        # Did we surface the actual documentary explanation for a known decoy?
        if entity in decoy_entities:
            decoy = decoy_entities[entity]
            if specific:
                s.legitimate_alternatives_found += 1
            # naming a contract / PO / publication date is the clearing doc
            if re.search(r"contract|CTR-|purchase order|PO-|publication", low):
                s.leads_naming_clearing_document += 1
            if entity not in accused:
                s.correctly_inconclusive += 1

        calls = lead.get("tool_calls_made") or []
        s.tool_calls_total += len(calls)
        s.tool_calls_unique += len(set(calls))
        if not calls:
            s.leads_with_no_lookup += 1
    s.closed_by_breakdown = dict(closed_by)
    s.redundant_actions = s.tool_calls_total - s.tool_calls_unique
    s.redundancy_rate = round(
        s.redundant_actions / max(s.tool_calls_total, 1), 3)

    # ---- cost ------------------------------------------------------------
    meta = submission.get("run_metadata", {})
    s.llm_calls = int(meta.get("llm_calls", 0) or 0)
    s.mxn_cost = float(meta.get("mxn_cost", 0) or 0)
    s.wall_clock_seconds = float(meta.get("wall_clock_seconds", 0) or 0)

    roles = Counter()
    for entry in recording:
        roles[classify_role(entry.get("response_text", ""))] += 1
        usage = entry.get("usage") or {}
        s.tokens_in += int(usage.get("input_tokens", 0) or 0)
        s.tokens_out += int(usage.get("output_tokens", 0) or 0)
    s.calls_by_role = dict(roles)
    if recording and not s.llm_calls:
        s.llm_calls = len(recording)
    return s


def load(path: str | None) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def decoy_code_map(answers_db: str | None) -> dict:
    if not answers_db or not Path(answers_db).exists():
        return {}
    conn = sqlite3.connect(answers_db)
    try:
        return {entity: code for code, entity in conn.execute(
            "SELECT decoy_id, entity FROM answer_key_decoys")}
    finally:
        conn.close()


def render(s: Score) -> str:
    L = []
    A = L.append
    A("=" * 72)
    A("  %s   (estate seed %s)" % (s.label or "RUN", s.estate_seed))
    A("=" * 72)
    A("  DETECTION")
    A("    schemes with a finding        %d / %d   (recall %.0f%%)"
      % (s.schemes_with_finding, s.schemes_total, 100 * s.scheme_recall))
    A("    schemes that raised a lead    %d / %d"
      % (s.schemes_signalled, s.schemes_total))
    for sid, d in sorted(s.scheme_detail.items()):
        A("      %-34s %-20s finding=%-5s lead=%s"
          % (sid, d["type"], d["finding"], d["signalled"]))
    A("  SAFETY")
    A("    findings emitted              %d" % s.findings_total)
    A("    unsupported accusations       %d" % s.unsupported_accusations)
    for d in s.unsupported_detail:
        A("      !! %-22s (%s)" % (d["entity"], d["answer_key"]))
    A("    decoys accused                %d / %d" % (s.decoys_accused, s.decoys_total))
    A("    decoys triggered / untriggered %d / %d"
      % (s.decoys_triggered, s.decoys_untriggered))
    A("    false-accusation rate         %.0f%% of entities named"
      % (100 * s.false_accusation_rate))
    A("  REASONING")
    A("    leads closed                  %d" % s.leads_closed)
    A("    with a specific reason        %d" % s.leads_with_specific_reason)
    A("    with a generic reason         %d" % s.leads_with_generic_reason)
    A("    naming the clearing document  %d" % s.leads_naming_clearing_document)
    A("    legitimate alternatives found %d" % s.legitimate_alternatives_found)
    A("    decoys correctly not accused  %d" % s.correctly_inconclusive)
    A("    closed_by                     %s" % (s.closed_by_breakdown or "-"))
    A("  ACTIONS")
    A("    lookups total / unique        %d / %d"
      % (s.tool_calls_total, s.tool_calls_unique))
    A("    redundant lookups             %d  (%.0f%%)"
      % (s.redundant_actions, 100 * s.redundancy_rate))
    A("    leads closed with no lookup   %d" % s.leads_with_no_lookup)
    A("  COST")
    A("    llm calls                     %d  %s" % (s.llm_calls, s.calls_by_role or ""))
    A("    tokens in / out               %d / %d" % (s.tokens_in, s.tokens_out))
    A("    mxn cost                      %.4f" % s.mxn_cost)
    A("    wall clock                    %.1f s" % s.wall_clock_seconds)
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission", required=True)
    ap.add_argument("--ground-truth", required=True)
    ap.add_argument("--answers")
    ap.add_argument("--recording")
    ap.add_argument("--label", default="RUN")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    submission = json.loads(Path(args.submission).read_text(encoding="utf-8"))
    truth = json.loads(Path(args.ground_truth).read_text(encoding="utf-8"))
    s = score(submission, truth, decoy_code_map(args.answers),
              load(args.recording), args.label)
    print(render(s))
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps(asdict(s), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
