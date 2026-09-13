# LedgerLens — desktop interface

```bash
pip install -r requirements.txt -r requirements-ui.txt
python -m ui.leglens_app
```

First run has no accounts. Click **Create account**, register one, then log in.

## What it does

**Analysis Workspace → Select Estate** takes a data estate — either
`estate.db` (SQLite) or `estate_csv.zip` (the eight CSVs) — and runs the audit,
then shows:

- **Findings** — each with its `rule_broken`, peso amount, confidence, and the
  exhibit table listing every `source_table` / `record_id` it rests on.
- **Leads investigated and closed** — every signal that did *not* become an
  accusation, with a reason naming the records that were read and the lookups
  that produced it. This is the panel that answers "why didn't you flag vendor X".
- **Run cost** — model calls, MXN, wall clock, and the run seed.

It writes a submission to `runs/ui-<seed>/submission.json` that passes the
official `validate_format.py` with estate checking enabled.

**Analysis History** lists previous runs. **Reports** opens the current run's
output folder.

## How it is wired

```
ui/leglens_app.py     widgets, login, navigation  (the original design, unchanged)
      |
      v
ui/audit_bridge.py    the only door to the pipeline: run_audit(estate) -> AuditResult
      |
      v
src/                  detectors -> leads -> claim_builder -> OfficialVerifier
                      -> EvidenceGate -> FindingBuilder -> SubmissionBuilder
```

The dependency points one way. `src/` never imports `ui/`, so the pipeline stays
runnable headless and the interface stays a thin shell over it.

Three properties the bridge guarantees, because all three are scored:

- **No network, no model call.** It drives the deterministic path only. A demo on
  a dead venue network behaves identically to one on a good one, and `llm_calls`
  is honestly `0` rather than estimated.
- **Deterministic.** The same estate produces the same submission. When the
  estate carries no seed, the seed is derived from the file's own SHA-256, so
  re-running the same file reproduces the same run id.
- **It never accuses without the gate.** A case appears under Findings only if
  `EvidenceGate` authorised it. Everything else becomes a closed lead.

The audit runs on a worker thread; every widget update is marshalled back onto
the Tk thread with `after()`, so the window never freezes.

## What it does not do yet

Only `phantom_vendor` has a substantive deterministic verifier, so only that
scheme can produce a finding. `kickback`, `round_tripping`,
`threshold_splitting` and `revenue_inflation` raise leads and are reported as
closed with their reason. The interface says so on screen rather than implying
full coverage.

The Challenger and Method Critic are not run, because they need a language model
and this path is deliberately offline. The results panel states that too.

## Authentication

Credentials live in `ui/credentials.json` — **gitignored**, `0600` where the OS
supports it. It stores a per-user random salt and a PBKDF2-HMAC-SHA256
verifier at 600,000 iterations. No password is written anywhere.

> **The previous store was `UsernamePassword.xlsx` on `feature/frontend`, and it
> held usernames and passwords in clear text in a public repository.** Those
> credentials are compromised. They were deliberately *not* migrated: the store
> starts empty and everyone re-registers. Anyone who reused one of those
> passwords elsewhere should change it there too.

## Tests

```bash
python -m pytest tests/test_ui_bridge.py -q
```

21 tests covering the credential store, estate selection, determinism, both
estate formats, official-validator conformance, and the one that matters most:
that the interface never accuses an entity the answer key marks as honest.
