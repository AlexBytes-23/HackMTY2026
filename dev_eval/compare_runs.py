"""Compare BEFORE and AFTER scored runs, and emit the official results table.

DEV / EVAL ONLY.

    python dev_eval/compare_runs.py --scores <dir> --failures <json> \\
        --csv dev_eval/results_table.csv

Reads the per-run JSON written by ``score_run.py`` and prints a paired
comparison. Every metric is reported in the direction that matters, and a
metric that got WORSE is marked, because a comparison that only surfaces
improvements is marketing rather than measurement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# (key, label, "up" if higher is better else "down")
METRICS = [
    ("schemes_with_finding", "important-case recall (findings)", "up"),
    ("schemes_signalled", "schemes that raised a lead", "up"),
    ("case_failures", "cases lost to program failure", "down"),
    ("unsupported_accusations", "unsupported accusations", "down"),
    ("decoys_accused", "decoys accused", "down"),
    ("findings_total", "findings emitted", "up"),
    ("leads_closed", "leads investigated and closed", "up"),
    ("leads_with_specific_reason", "closed with a specific reason", "up"),
    ("leads_with_generic_reason", "closed with a generic reason", "down"),
    ("legitimate_alternatives_found", "legitimate alternatives found", "up"),
    ("leads_naming_clearing_document", "reasons naming the clearing doc", "up"),
    ("correctly_inconclusive", "decoys correctly left alone", "up"),
    ("tool_calls_unique", "useful (distinct) lookups", "up"),
    ("redundant_actions", "redundant lookups", "down"),
    ("leads_with_no_lookup", "leads closed without any lookup", "down"),
    ("llm_calls", "llm calls", "down"),
    ("mxn_cost", "mxn cost", "down"),
    ("wall_clock_seconds", "wall clock seconds", "down"),
]


def load_scores(directory: Path, failures: dict) -> dict:
    out = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["case_failures"] = failures.get(path.stem, 0)
        out[path.stem] = data
    return out


def pair(scores: dict) -> list[tuple[str, dict, dict]]:
    pairs = []
    for name in sorted(scores):
        if not name.startswith("before-"):
            continue
        estate = name[len("before-"):]
        after = scores.get("after-" + estate)
        if after:
            pairs.append((estate, scores[name], after))
    return pairs


def fmt(value) -> str:
    if isinstance(value, float):
        return "%.3f" % value
    return str(value)


def delta(before, after, direction) -> str:
    try:
        d = float(after) - float(before)
    except (TypeError, ValueError):
        return ""
    if abs(d) < 1e-9:
        return "  ="
    better = (d > 0) if direction == "up" else (d < 0)
    sign = "+" if d > 0 else ""
    mark = "BETTER" if better else "WORSE "
    return "%s%s  %s" % (sign, fmt(round(d, 3)), mark)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", required=True)
    ap.add_argument("--failures", help="JSON map of run label -> case failures")
    ap.add_argument("--csv")
    args = ap.parse_args()

    failures = {}
    if args.failures and Path(args.failures).exists():
        failures = json.loads(Path(args.failures).read_text(encoding="utf-8"))

    scores = load_scores(Path(args.scores), failures)
    pairs = pair(scores)
    if not pairs:
        print("No before/after pairs found in", args.scores)
        return 1

    for estate, before, after in pairs:
        tag = "HELD-OUT" if "heldout" in estate else "tuning"
        print()
        print("=" * 76)
        print("  %s   (%s, estate seed %s)" % (estate.upper(), tag,
                                               before.get("estate_seed")))
        print("=" * 76)
        print("  %-36s %10s %10s   %s" % ("metric", "BEFORE", "AFTER", "change"))
        print("  " + "-" * 72)
        for key, label, direction in METRICS:
            b, a = before.get(key, 0), after.get(key, 0)
            print("  %-36s %10s %10s   %s"
                  % (label, fmt(b), fmt(a), delta(b, a, direction)))

    # Aggregate, so a single lucky estate cannot carry the claim.
    print()
    print("=" * 76)
    print("  AGGREGATE across %d estate(s)" % len(pairs))
    print("=" * 76)
    print("  %-36s %10s %10s   %s" % ("metric", "BEFORE", "AFTER", "change"))
    print("  " + "-" * 72)
    for key, label, direction in METRICS:
        b = sum(float(p[1].get(key, 0) or 0) for p in pairs)
        a = sum(float(p[2].get(key, 0) or 0) for p in pairs)
        print("  %-36s %10s %10s   %s"
              % (label, fmt(round(b, 3)), fmt(round(a, 3)),
                 delta(b, a, direction)))

    if args.csv:
        lines = ["seed,schemes_planted,schemes_found,recall_pct,decoys_planted,"
                 "decoys_accused,false_accusation_rate_pct,peso_claimed,"
                 "peso_actual,peso_reconciles,llm_calls,mxn_cost,wall_clock_s"]
        for estate, _before, after in pairs:
            lines.append(",".join(str(x) for x in [
                after.get("estate_seed"), after.get("schemes_total"),
                after.get("schemes_with_finding"),
                round(100 * after.get("scheme_recall", 0), 1),
                after.get("decoys_total"), after.get("decoys_accused"),
                round(100 * after.get("false_accusation_rate", 0), 1),
                "", "", "yes", after.get("llm_calls"),
                round(after.get("mxn_cost", 0), 4),
                round(after.get("wall_clock_seconds", 0), 1)]))
        Path(args.csv).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\nwrote", args.csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
