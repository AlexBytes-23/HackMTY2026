"""Rehearse the offline replay, with the network genuinely disabled.

    python dev_eval/rehearse_offline_replay.py

The official checklist says "Replay works with the network off - rehearse it".
This is that rehearsal, and it is not a simulation: name resolution, socket
connect and ``urllib.request.urlopen`` are all made to raise before
``run_audit`` is imported, and ``GEMINI_API_KEY`` is removed from the
environment. If anything in the pipeline reaches for the network, the run dies
instead of quietly succeeding.

It then runs twice and compares the two submissions, because the challenge
says judges may run a seed twice.

What "identical" means here, precisely
--------------------------------------
``findings``, ``leads_not_pursued`` and ``seed`` must match byte for byte.
``run_metadata.wall_clock_seconds`` must NOT: it is a measurement of how long
this machine took, and freezing it would be reporting a number we did not
measure. The check below treats a difference confined to that one field as a
pass, and says so on screen rather than hiding it.

One property worth knowing before the demo: a recording is keyed on the exact
prompt text. Change a detector, a prompt, or anything that alters the case
context, and every recorded key stops matching - the replay then reports
"replay miss" on each case. Re-record after changing the pipeline, not before.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

BLOCKER = """
# Disable the network without breaking imports. ssl subclasses socket.socket,
# so the CLASS has to survive; blocking resolution and connect is enough to
# make any real request impossible.
import socket
import urllib.request


def _blocked(*args, **kwargs):
    raise OSError("NETWORK DISABLED (offline replay rehearsal)")


socket.getaddrinfo = _blocked
socket.create_connection = _blocked
socket.socket.connect = _blocked
urllib.request.urlopen = _blocked
"""


def run_once(blocker: Path, estate: Path, recording: Path, seed: int,
             out: Path, cost: float) -> tuple[int, str, dict | None]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("GEMINI_API_KEY", None)          # no credential either
    out.parent.mkdir(parents=True, exist_ok=True)

    script = (
        "exec(open(r'%s').read());" % blocker
        + "import sys; sys.argv=['run_audit','--estate',r'%s','--out',r'%s',"
          "'--seed','%d','--replay',r'%s','--mxn-cost','%.6f'];"
          % (estate, out, seed, recording, cost)
        + "from src.run_audit import main; sys.exit(main())"
    )
    proc = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                          env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    payload = None
    if out.exists():
        payload = json.loads(out.read_text(encoding="utf-8"))
    return proc.returncode, (proc.stderr or "")[-900:], payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--estate",
                    default="dev_eval/tuning/seed_101/robust_realism_estate.db")
    ap.add_argument("--recording", default="recordings/tuning-101.jsonl")
    ap.add_argument("--seed", type=int, default=101)
    args = ap.parse_args()

    estate = (REPO_ROOT / args.estate).resolve()
    recording = (REPO_ROOT / args.recording).resolve()
    for path, what in ((estate, "estate"), (recording, "recording")):
        if not path.exists():
            print("FAIL  %s not found: %s" % (what, path))
            return 2

    rows = [json.loads(line) for line in
            recording.read_text(encoding="utf-8").splitlines() if line.strip()]
    cost = sum(r.get("configured_cost") or 0 for r in rows)

    print("=" * 68)
    print("  OFFLINE REPLAY REHEARSAL")
    print("=" * 68)
    print("  estate    : %s" % estate.name)
    print("  recording : %s  (%d calls, %.4f MXN when it was live)"
          % (recording.name, len(rows), cost))
    print("  network   : DISABLED (getaddrinfo, connect and urlopen all raise)")
    print("  api key   : removed from the environment")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "nonet.py"
        blocker.write_text(BLOCKER, encoding="utf-8")
        results = []
        for attempt in (1, 2):
            out = REPO_ROOT / "runs" / "offline-rehearsal" / ("submission-%d.json" % attempt)
            code, stderr, payload = run_once(blocker, estate, recording,
                                             args.seed, out, cost)
            if code != 0 or payload is None:
                print("  FAIL  run %d exited %d" % (attempt, code))
                print(stderr)
                return 1
            misses = sum(1 for lead in payload["leads_not_pursued"]
                         if "ReplayMiss" in lead.get("reason", ""))
            print("  run %d  OK   findings %d | leads %d | llm_calls %s | "
                  "MXN %s | replay misses %d"
                  % (attempt, len(payload["findings"]),
                     len(payload["leads_not_pursued"]),
                     payload["run_metadata"]["llm_calls"],
                     payload["run_metadata"]["mxn_cost"], misses))
            results.append((payload, out.read_bytes()))

    first, second = results[0][0], results[1][0]
    same_bytes = results[0][1] == results[1][1]
    same_content = (first["findings"] == second["findings"]
                    and first["leads_not_pursued"] == second["leads_not_pursued"]
                    and first["seed"] == second["seed"])
    differing = sorted(k for k in first["run_metadata"]
                       if first["run_metadata"][k] != second["run_metadata"].get(k))

    print()
    print("  findings / leads / seed identical : %s" % same_content)
    print("  whole file identical              : %s" % same_bytes)
    if differing:
        print("  run_metadata fields that differ   : %s" % ", ".join(differing))
    print()

    if not same_content:
        print("  FAIL  the forensic content is not reproducible.")
        return 1
    if differing and differing != ["wall_clock_seconds"]:
        print("  FAIL  run_metadata differs beyond the measured wall clock.")
        return 1

    misses = sum(1 for lead in first["leads_not_pursued"]
                 if "ReplayMiss" in lead.get("reason", ""))
    print("  PASS  a completed run reproduces with the network off.")
    if misses:
        print("  WARNING  %d case(s) reported a replay miss. The recording no "
              "longer matches the current prompts or detectors; re-record "
              "before the demo." % misses)
    print("        wall_clock_seconds differs by design: it is measured, and "
          "freezing it would report a number we did not measure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
