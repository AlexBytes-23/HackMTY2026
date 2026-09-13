# Plan — Maximize Results and Accuracy (Forensic Auditor, HackMTY 2026)

## Spec

The binding authority is the official challenge pack, already in this repo. Read
only the parts your task names:

- `official_materials/student-materials/forensic-auditor/estate_schema.sql` — the
  estate's eight tables, exact column names, exact enum values.
- `official_materials/student-materials/forensic-auditor/submission_schema.json` —
  the output contract.
- `official_materials/student-materials/forensic-auditor/ground_truth_schema.json` —
  the answer-key shape our own harness must use.
- `official_materials/student-materials/forensic-auditor/case_file_structure.md` —
  the required case-file sections.
- `official_materials/student-materials/forensic-auditor/results_table_template.csv` —
  the Results table shape.
- `official_materials/student-materials/forensic-auditor/validate_format.py` — the
  format check. This is the arbiter of output shape. Never edit it.
- `CLAUDE.md` at the repo root — the project's twelve non-negotiable rules.

## Context

The system already runs `estate.db -> submission.json` and passes the official
validator (commit 77f85e9). Architecture is sound and deliberately fails closed:
the Evidence Gate refuses to authorize anything it cannot deterministically
verify. What is missing is *reach* and *legibility*.

Judges score four equally weighted criteria at 1-5 each. Today:

- **Results** is unmeasurable — we have no estate generator, no answer key, and
  no scoring harness. And only `phantom_vendor` has a substantive verifier, so
  four of the five scheme types can never become a finding.
- **Clarity** is capped at 3 — there is no case file, and the official spec says
  a prose-only money trail caps Clarity at 3. `money_trail` is currently `[]`.
- **Judgment** is strong but has one real hole: a `presunto` EFOS listing and an
  invoice predating publication both authorize a `probable` accusation today.
- **Feasibility** is largely done: determinism, offline replay and truthful
  `run_metadata` all exist and are tested.

This plan closes those gaps in dependency order.

## Global Constraints

These bind every task. A violation is a spec failure, not a style note.

1. **Ground truth is unreachable from `src/`.** Judges run
   `grep -r 'ground_truth' src/ --include='*.py'` and cap Results at 2 if it
   hits. Answer keys live in `dev/`. Only `eval/` may read them. Nothing in
   `src/` may import from `dev/` or `eval/`, or contain the strings
   `ground_truth`, `answer_key`, `is_fraud`, or `scheme_type` as *data read from
   the estate*. `tests/` may import `dev/` (it is not `src/`).
2. **Arithmetic is Python's, never the LLM's.** Every amount, sum, and match is
   recomputed deterministically.
3. **No finding without `authorize_probable` from the Evidence Gate.** No task
   may add a path that bypasses `src/gates/evidence_gate.py`.
4. **Absence in the estate is not absence in reality.** Write "no record was
   found in the supplied estate", never "does not exist".
5. **Every claim cites `source_table` + `record_id` that exist.**
6. **A score always travels with its `score_semantics`.** Never present a score
   as a probability of fraud.
7. **Entities in official output carry `RFC:` or `EMP:` only.** `CLABE:` is
   internal and must never appear in a submission or a case file.
8. **`peso_amount` reconciles PER TABLE**, tolerance `0.02 * max(best_total, 1)`,
   where `best_total` is the cited per-table sum closest to the claim. The
   amount-bearing columns are exactly: `invoices.total`, `bank_txns.amount`,
   `purchase_orders.amount`, `contracts.value`.
9. **Determinism.** Same seed, same bytes. Sort every iterable with an explicit
   key. Never iterate a `set` into output. No timestamps in generated data.
10. **No hardcoded paths.** Every path is a parameter or CLI argument.
11. **The estate schema is immutable.** Never add a column. The exact
    `efos_list.status` values are lowercase `definitivo` and `presunto`.
12. **Tests live beside the module**, as `tests/test_<module>.py`. Nothing merges
    without a test. Run the suite with:
    `C:/Users/danie/AppData/Local/Temp/claude/C--Users-danie-OneDrive-Documentos-GitHub-HackMTY2026/f24bb000-7ec0-4722-a189-3f59600bb289/scratchpad/av/Scripts/python.exe -m pytest -q`
    Baseline is **274 passed, 3 skipped**. The 3 skips are the GNN modules
    (torch is not installed in this environment) — that is expected, not a
    regression.
13. **Language.** New modules that are mostly English stay English; modules
    already in Spanish keep Spanish docstrings and comments. Never translate an
    existing module.
14. **Every detector declares `limitations`, `legitimate_alternatives`, and
    `recommended_checks`.**

---

## Task 1: Estate generator and answer key

**Files:** create `dev/__init__.py`, `dev/estate_generator.py`,
`dev/generate_estate.py`, `tests/test_estate_generator.py`

Nothing can be measured until we can generate estates with a known answer key.

Write a deterministic generator that, given a seed, produces an `estate.db`
conforming exactly to `estate_schema.sql` plus a `ground_truth.json` conforming
exactly to `ground_truth_schema.json`.

**Public API in `dev/estate_generator.py`:**

```python
@dataclass(frozen=True)
class EstateSpec:
    seed: int
    n_honest_vendors: int = 18
    n_employees: int = 8
    schemes: tuple[str, ...] = (
        "phantom_vendor", "kickback", "round_tripping",
        "threshold_splitting", "revenue_inflation",
    )
    n_decoys: int = 10

def generate_estate(spec: EstateSpec, db_path: str | Path) -> dict:
    """Write estate.db at db_path. Return the ground-truth dict."""
```

**Requirements:**

- Use `random.Random(spec.seed)` only. Never the global `random` module, never
  `datetime.now()`, never `uuid4()`.
- The company is the receiver: `company_rfc = "EMP920101AB1"`. All invoices to
  the company use it as `receiver_rfc`. Give the company a CLABE of
  `"000000000000000099"` and use it as `from_clabe` on company payments.
- Populate all eight tables with the exact columns from `estate_schema.sql`.
  `invoices` must satisfy `subtotal + iva == total` with `iva == round(subtotal
  * 0.16, 2)`. Use `uso_cfdi` `"G03"`, `forma_pago` `"03"`, `metodo_pago`
  `"PUE"`, `status` `"vigente"` unless a scheme needs otherwise.
- RFCs: 12-13 chars, uppercase, generated deterministically and unique.
  CLABEs: 18 digits, unique, zero-padded. `emp_id`: bare `"0001"` style —
  do **not** embed the `EMP:` prefix in the primary key.
- Id formats: `INV-00001`, `BNK-00001`, `PO-00001`, `CTR-00001`, `ledger.entry_id`
  as a 1-based integer.
- **Every honest vendor gets a plausible paper trail**: a contract or a PO, an
  invoice, a matching bank transfer, and balanced ledger entries (one debit row
  and one credit row of equal value referencing the invoice).

**The five schemes.** Each must be detectable in principle from the estate alone,
and each must record its entities, supporting invoices, supporting txns, and
`peso_amount` in the ground truth:

- `phantom_vendor` — a vendor whose RFC is also in `efos_list` with status
  `definitivo` and a `publication_date` *before* its invoices' `issue_date`.
  Invoices to the company, paid by bank transfer. No contract, no PO, vague
  `concepto_text`.
- `kickback` — a vendor whose `bank_clabe` equals an employee's `bank_clabe`, or
  which transfers to an employee's CLABE shortly after being paid. The employee
  is the `approver` on the related POs. Record both `RFC:` and `EMP:` entities.
- `round_tripping` — a directed cycle of bank transfers across 3-4 CLABEs where
  the amounts are near-equal and the dates strictly increase, returning to the
  company's CLABE.
- `threshold_splitting` — several invoices/POs from one vendor in a short window,
  each just under a round approval threshold, summing well above it. Put the
  threshold in code as a named constant in the generator, and record it in the
  ground truth entry's `scheme_id` description. Do **not** invent a legal
  threshold; this is an internal approval limit the generator itself defines.
- `revenue_inflation` — invoices *issued by the company* (`issuer_rfc ==
  company_rfc`) near a period end, with `status == "cancelado"` shortly after, or
  with no corresponding bank receipt. Ledger credits revenue with no cash.

**The ten decoys.** Each must trip a detector and then *check out* on inspection.
Each ground-truth decoy entry needs a one-sentence `why_innocent` naming the
document that clears it. Include at least these shapes:

- A vendor in `efos_list` with status `presunto` whose invoices all predate the
  `publication_date` — the timing clears it.
- A vendor and an employee at the same bank (CLABE sharing the first 3 digits)
  but with different account numbers.
- A legitimate reciprocal payment pair that forms a 2-cycle (refund).
- A vendor with many small invoices under a fixed-fee framework contract that
  explains the pattern.
- A cancelled-and-reissued invoice pair near a period end that nets to zero.

**`dev/generate_estate.py`** is the CLI:

```
python -m dev.generate_estate --seed 101 --out-dir dev/estates/101
```
writes `dev/estates/<seed>/estate.db` and `dev/estates/<seed>/ground_truth.json`.
Add `dev/estates/` to `.gitignore`.

**Tests** (`tests/test_estate_generator.py`):

- The generated db passes `EstateRepository`'s schema validation.
- Same seed, two runs → identical `ground_truth.json` and identical row dumps of
  every table (compare sorted `get_all()` output). Different seeds → different.
- `subtotal + iva == total` for every invoice, to the cent.
- Every ground-truth `supporting_invoices` / `supporting_txns` id exists in the
  estate.
- All five scheme types appear when requested; `n_decoys` decoys are recorded.
- Every decoy has a non-empty `why_innocent`.
- The ground truth validates against the required keys in
  `ground_truth_schema.json` (`seed`, `company_rfc`, `schemes`, `decoys`, and the
  per-item required keys).

---

## Task 2: Evaluation harness and results table

**Files:** create `eval/__init__.py`, `eval/score_submission.py`,
`eval/run_benchmark.py`, `tests/test_score_submission.py`

**Files:** this is the only place in the repo besides `dev/` that may read an
answer key.

`eval/score_submission.py` public API:

```python
@dataclass(frozen=True)
class SeedScore:
    seed: int
    schemes_planted: int
    schemes_found: int
    recall_pct: float
    decoys_planted: int
    decoys_accused: int
    false_accusation_rate_pct: float
    peso_claimed: float
    peso_actual: float
    peso_reconciles: bool
    llm_calls: int
    mxn_cost: float
    wall_clock_s: float

def score_submission(submission: dict, ground_truth: dict, estate_db: str | Path) -> SeedScore
def format_results_table(scores: list[SeedScore]) -> str   # CSV, template column order
```

**Matching rules — be strict, and document each in a docstring:**

- A planted scheme counts as **found** when some finding has the same
  `scheme_type` AND that finding's `entities` intersect the scheme's `entities`.
  Entity comparison is exact on the prefixed string.
- A decoy counts as **accused** when any finding's `entities` contain the decoy's
  `entity`. `false_accusation_rate_pct = decoys_accused / decoys_planted * 100`.
- A finding naming an entity that is neither in a planted scheme nor in the decoy
  list is **also** a false accusation. Count it in `decoys_accused` and say so in
  the docstring — an accusation against an uninvolved honest vendor is at least as
  bad as accusing a decoy.
- `peso_claimed` is the sum of `peso_amount` over findings; `peso_actual` is the
  sum of `peso_amount` over *matched* planted schemes only.
- `peso_reconciles` is `validate_format.validate_against_estate(...)` returning no
  errors. Import that module by path from the official materials directory;
  never copy its logic.

`eval/run_benchmark.py` CLI:

```
python -m eval.run_benchmark --seeds 101,102,103,104,105 --estates-dir dev/estates --out eval/results/results_table.csv
```

It runs `src.run_audit.main` per seed (in-process, with a replay or scripted
client path — accept `--replay-dir`), scores each, and writes the CSV in the
exact column order of `results_table_template.csv` with a `TOTAL` row. Print a
human-readable summary too.

**Tests:** cover a perfect submission, an empty submission, a submission that
accuses a decoy, a submission that accuses an entity in neither list, and the
`TOTAL` row arithmetic. Build the ground truth and submission dicts inline —
do not depend on Task 1's generator, so this task can be reviewed alone.

---

## Task 3: Three small correctness fixes

**Files:** modify `src/investigation/investigator.py`, `src/llm/runtime.py`,
`src/run_audit.py`; create `tests/test_investigator_parsing.py`; extend
`tests/test_run_audit_end_to_end.py`

Three independent small defects, batched because each is a few lines.

**3a — the Investigator's parser rejects fenced JSON.** `parse_investigator_decision`
in `src/investigation/investigator.py` calls `json.loads` directly, while
`parse_challenger_review` (`src/agents/challenger.py`) strips markdown fences.
Gemini routinely returns ```` ```json ```` fences, so the first live call raises
`ValueError` and every case is recorded as an execution error.

Extract the fence-stripping that `parse_challenger_review` already implements
into a shared helper — put it in `src/llm/json_text.py` as
`strip_code_fences(text: str) -> str` — and use it in all three parsers
(`investigator.py`, `challenger.py`, `method_critic.py`). It must handle:
no fence; ```` ``` ```` alone; ```` ```json ````; trailing newline before the
closing fence; and text that merely *contains* a backtick.

**3b — ask Gemini for JSON.** In `GeminiLLMClient.complete`
(`src/llm/runtime.py`), add to the request payload:
`"generationConfig": {"responseMimeType": "application/json"}`. Do not change
anything else about the request. Keep the existing api-key scrubbing exactly as
it is.

**3c — `emp_id` may already carry its prefix.** CONFIRMED on the held-out estate:
`detect_vendor_employee_shared_clabe` and `detect_vendor_to_employee_transfers` emit
`EMP:EMP:0003` for 3 employees. A doubled prefix makes the entity UNMATCHABLE against
ground truth, so a correct finding would score as a missed scheme AND as an
unattributed false accusation at the same time. Add ONE idempotent prefix helper beside
`normalize_entity` in `src/output/formatters.py` and use it in both detectors and in
`run_audit._official_entity`.

**3d — a lead whose subject is a CLABE loses its official entity.** `build_leads`
(`src/investigation/lead_builder.py`) groups on `observation.entities[0]`, and a bank
cycle's first entity is `CLABE:<clabe>`, so 3 of 19 leads on the held-out estate reach
the Investigator with no `RFC:`/`EMP:` subject. `run_audit` then DROPS them rather than
leak a CLABE, so they vanish from `leads_not_pursued` — a scored Clarity item and a
guaranteed judge question. Resolve a CLABE to its owning vendor/employee at lead-build
time via `EstateRepository.find_vendor_by_clabe` / `find_employee_by_clabe`, and keep the
raw CLABE out of `subject_entities` entirely. If a CLABE has no owner in the estate, keep
the lead and use the observation's next official entity; never invent one.

ORIGINAL 3c NOTE (superseded above, kept for the record): `estate_schema.sql`'s
illustrative `employees` row shows `emp_id` as `EMP:0001`, so a judge's estate
may store the prefix in the primary key. `_official_entity` in
`src/run_audit.py` does `f"EMP:{emp_id}"`, which would emit `EMP:EMP:0001`.
Make it idempotent for both `EMP:` and `RFC:`: if the looked-up value already
starts with the prefix, do not add it again. Same for the vendor `rfc` branch.

**Tests:** a parametrized test over the fence shapes for `strip_code_fences`;
one test that `parse_investigator_decision` accepts a fenced decision; one that
the Gemini payload contains the `responseMimeType` (build the payload without a
network call — if that needs a tiny refactor, extract a `_build_payload` method
and test it); and two cases added to the existing `_official_entity` tests for
already-prefixed `emp_id` and `rfc`.

---

## Task 4: A repeated action must not kill the case

**Files:** modify `src/investigation/runner.py`; create
`tests/test_runner_repeated_action.py`

`run_investigation_loop` returns `stop_reason="repeated_action"` the moment the
Investigator proposes an action whose `(name, arguments)` signature was already
executed. It returns *without executing anything* and discards the remaining
step budget. `review_orchestrator._stop_from_investigator` then maps that to a
terminal `investigator_repeated_action`, so the whole case dies: the Method
Critic never runs and no finding is possible.

This is measured behaviour, not theory — with `max_review_rounds=3` and two
follow-up steps still available, one repeat ends the case after round 1. LLMs
repeat actions constantly, so this directly costs recall.

**Required change:** on a repeated signature, record an unsuccessful
`ActionRecord` (`success=False`, an `errors` entry naming the repetition and the
step that already ran it) so the repeat is auditable, then **continue** to the
next loop iteration. Only return `stop_reason="repeated_action"` if the step
budget is exhausted and every iteration in this call was a repeat — meaning the
Investigator is genuinely stuck in a cycle rather than merely duplicating once.

Keep the existing `_action_signature` helper and the pre-seeding of
`seen_actions` from `current.actions_taken` exactly as they are.

**Tests:** one repeat followed by a new useful action reaches
`request_review`, executes the new action, and records both attempts; every
iteration repeating returns `repeated_action`; the recorded repeat has
`success=False` and produces no `CaseEvidence`; and a case that previously died
now survives — assert `run_review_orchestration` reaches the Method Critic when
the follow-up repeats once then proposes something new.

---

## Task 5: EFOS status and timing must gate a phantom_vendor accusation

**Files:** modify `src/verifier/official_verifier.py`,
`src/output/finding_builder.py`, `src/rules/default_rules.py`; extend
`tests/test_official_verifier.py`; create `tests/test_phantom_vendor_timing.py`

`PHANTOM-EFOS-INVOICE-LINK` in `OfficialVerifier.verify` currently compares only
`efos_list.rfc == invoices.issuer_rfc`. It never reads `efos_list.status` or
`publication_date`, and never compares them against `invoices.issue_date` — even
though `detect_efos_vendor_matches` lists exactly those in its own `limitations`
as things that must still be evaluated.

So a vendor listed as `presunto`, or one whose invoices all predate the
publication date, still yields an authorized `probable` accusation. That is the
highest false-accusation risk in the output path, and the challenge weights false
accusations at least as heavily as recall.

**Required deterministic sub-conditions**, evaluated after the RFC intersection
and before `status = "verified"`. Read the exact lowercase enum values from
`estate_schema.sql`: `definitivo` and `presunto`.

- If no matched `efos_list` record has `status == "definitivo"`
  (case-insensitive, whitespace-stripped), the check is **`unresolved`**, not
  `failed` — a `presunto` listing is a real signal that has not yet matured, so
  the honest outcome is "not established", not "disproved".
- If no cited invoice from a matched issuer has
  `issue_date >= publication_date` (ISO-8601 string comparison is correct for
  this format; state that in a comment), the check is **`unresolved`**.
- A missing or unparseable `status` / `publication_date` is also `unresolved`,
  never `verified`.
- Put the matched status, the publication date, and the qualifying invoice dates
  into `check.calculation` so the case file can quote them.

Add a module-level named constant for the qualifying status — do not inline the
string at the comparison site. A judge will ask to see where the constant is
defined.

**Narrative:** extend `build_finding`'s narrative to state the timing
relationship in plain language — that the cited invoices were issued on or after
the EFOS publication date, and that the listing status is definitive. Stay under
150 words; the validator counts them.

**Rule registry:** `RULE_CFF_69B.exceptions` currently mentions "desvirtuado",
which is not in the estate schema's enum. Replace that entry with one that names
the two real statuses and says a `presunto` listing does not support a definitive
imputation.

**Acceptance test, from the held-out estate — this is a MEASURED false accusation, not
a hypothetical.** `RFC:GAL160126E3F` (`efos_list.status='presunto'`,
`publication_date='2025-12-12'`, five invoices dated 2024-02-14..2024-10-15, every one
covered by an approved PO under a 2023 contract) is currently authorised for
2,506,760.00 MXN and MUST now be blocked — it fails on status AND on timing
independently. `RFC:BER2407128JB` (`status='definitivo'`,
`publication_date='2025-03-14'`, seven invoices 2025-04-09..2025-09-18 all after
publication, no contract) MUST still authorise. Build both as fixtures in
`tests/test_phantom_vendor_timing.py`; do not read the held-out estate file from a test.

**Tests:** `definitivo` + invoice on/after publication → `verified`; `presunto`
only → `unresolved`; `definitivo` but every invoice predates publication →
`unresolved`; missing `publication_date` → `unresolved`; missing `status` →
`unresolved`; and an end-to-end test that the gate declines and the CLI emits a
`leads_not_pursued` entry (not a finding) for a `presunto` vendor.

---

## Task 6: Money trail construction

**Files:** create `src/output/money_trail.py`,
`tests/test_money_trail.py`; modify `src/output/finding_builder.py`

`build_finding` hard-codes `money_trail=[]`. The official validator accepts an
empty array, but `case_file_structure.md` requires a rendered money trail and the
submission schema calls it required for full Clarity credit. An empty trail is a
silent Clarity loss.

The danger is the opposite error: fabricating a trail. A directed cycle is not
proof the same pesos moved. So build a trail **only** from cited `bank_txns`
records, and emit nothing when the records do not support one.

**Public API:**

```python
def build_money_trail(
    resolved_exhibits: Sequence[EvidenceRef],
    exhibit_ids: Mapping[tuple[str, str], str],
    estate: EstateRepository,
) -> list[MoneyTrailStep]
```

- Consider only exhibits with `source_table == "bank_txns"`.
- For each, resolve `from_clabe` and `to_clabe` to an official entity using the
  estate: `find_vendor_by_clabe` → `RFC:<rfc>`, `find_employee_by_clabe` →
  `EMP:<emp_id>` (idempotent on an already-prefixed id, as in Task 3c). **If a
  CLABE cannot be resolved to an owner, return `[]`** — never emit a raw CLABE
  and never invent a party. Global Constraint 7 is absolute here.
- Order steps by `(date, txn_id)` — explicit, deterministic.
- Enforce continuity: `step[i].to == step[i+1].from`. If the ordered steps do not
  chain, return only the longest leading run that does chain; if that run is
  empty, return `[]`. `SubmissionFinding` validates continuity and will raise
  otherwise.
- Every step's `exhibit_id` must be the id of that `bank_txns` exhibit within
  this finding. `amount` and `date` come from the record, never computed.

In `build_finding`, build the exhibit list first, then pass the
`(source_table, record_id) -> exhibit_id` mapping to `build_money_trail`, and use
the result instead of `[]`. Do not make a non-empty trail a precondition for a
finding — a phantom_vendor case whose payments are not cited is still a valid
finding.

**Tests:** a two-hop chain resolves and orders correctly; an unresolvable CLABE
yields `[]`; non-chaining transfers yield the leading run only; a single transfer
yields one step; no `bank_txns` exhibit yields `[]`; and a `build_finding` test
asserting the emitted `money_trail` steps all reference exhibit ids present in
the same finding.

---

## Task 7: Kickback and threshold_splitting verifiers

**Files:** modify `src/verifier/official_verifier.py`,
`src/gates/evidence_gate.py`, `src/investigation/claim_builder.py`,
`src/output/finding_builder.py`, `src/rules/default_rules.py`,
`src/run_audit.py`; create `tests/test_kickback_verifier.py`,
`tests/test_threshold_splitting_verifier.py`

Four of five scheme types can never become a finding, which caps recall at
roughly a fifth of what the estate contains. This task adds the two most
deterministically tractable.

Follow the `phantom_vendor` pattern exactly — it is the reference implementation.
For each scheme, add:

1. **A substantive critical check** in `OfficialVerifier.verify`, keyed by
   `hypothesis.scheme_type`, with its own `check_id`.
2. **The gate requirement** in `evaluate_gate`: extend the scheme dispatch so the
   new scheme requires its own verified critical check, and keep the
   `else: "No supported deterministic substantive verifier exists..."` branch for
   the still-unsupported ones.
3. **A claim scope rule** in `claim_builder.py`, following
   `build_phantom_vendor_claim`'s contract: a fixed scope computed in advance,
   a recorded `formula`, and a refusal (`claimed_amount=None` plus a specific
   error) when the cited exhibit set is ambiguous. Add a
   `build_claim(case_state, hypothesis_id, estate)` dispatcher that routes on
   `scheme_type` so `run_audit` has one entry point.
4. **Finding support** in `build_finding`: replace the hard
   `scheme_type != "phantom_vendor"` rejection with a per-scheme required-check
   table, a per-scheme entity extraction, and a per-scheme narrative. Keep the
   refusal for any scheme not in the table.
5. **A real rule** in `default_rules.py` with honest `does_not_prove` entries.

**`kickback` — `KICKBACK-VENDOR-EMPLOYEE-LINK`:**
`verified` only when both hold from cited records: a cited `vendors` record and a
cited `employees` record share an identical `bank_clabe`, **or** a cited
`bank_txns` record runs from the vendor's CLABE to the employee's CLABE. Entities
are `RFC:<vendor>` and `EMP:<emp_id>`. Claim scope: the sum of
`bank_txns.amount` over cited transfers between those two CLABEs; if none are
cited, the sum of `invoices.total` for cited invoices issued by that vendor.
Whichever branch applies must be named in the `formula`.
**Same bank is not the same account** — an identical institution prefix with
different account numbers must not verify. Test that explicitly; it is one of
the questions judges are listed as asking.
Rule: `LFPIORPI`/internal conflict-of-interest is *not* safely citable here.
Use the company's own procurement segregation-of-duties requirement as an
internal control rule, and say in `does_not_prove` that a shared account does not
by itself establish intent or that the employee benefited.

**`threshold_splitting` — `SPLITTING-BELOW-APPROVAL-THRESHOLD`:**
`verified` only when three or more cited `purchase_orders` (or `invoices`) from
the same vendor each fall below a named threshold constant, fall inside a named
window in days, and together exceed that threshold. **Both the threshold and the
window must be module-level named constants in `src/verifier/official_verifier.py`
with a comment stating they are internal control parameters this system defines,
not law.** A judge will open that file. Claim scope: the sum of the qualifying
records' amounts. Rule: the company's own approval-limit policy, with
`does_not_prove` stating that a cluster of purchases below a limit can be
ordinary recurring procurement.

**Deterministic noise filter, before any LLM call.** On the held-out estate
`detect_short_window_similar_invoice_clusters` fires on 12 windows, of which 10 are
ordinary honest vendors (~83% noise). Add deterministic `contract_coverage` and
`po_coverage` checks to the threshold-splitting path: a cluster whose invoices are
covered by a framework contract or by approved POs is CLOSED AS A RECORDED LEAD with a
specific documentary reason naming the covering `CTR-` / `PO-` ids. Never discard it
silently — the reason must reach `leads_not_pursued`. One deterministic change that
cuts false positives, fills a section judges ask about, and lowers the LLM call count.

**Adversarial negative cases — write these tests BEFORE the verifiers**, so the
verifier is shaped by them. From the held-out estate:

- `RFC:TRVC84021936Z` shares an ENTIRE 18-digit CLABE with `EMP:0031` because the vendor
  IS the employee — persona fisica con actividad empresarial, with a contract and
  approved POs. A kickback verifier keyed naively on CLABE equality accuses a legitimate
  self-employed contractor. It MUST NOT verify.
- `RFC:VIA171002P3W` transfers 8,432.50 to `EMP:0022` with reference
  "Reembolso gastos comprobados EXP-2025-0412" — a documented expense reimbursement.
  It MUST NOT verify.

Build both as fixtures; do not read the held-out estate file from a test.

`run_audit._authorize_case` must stop hardcoding `phantom_vendor`: route through
the claim dispatcher and let the gate and builder refuse unsupported schemes.
Its `leads_not_pursued` reason for an unsupported scheme must still name the
scheme and say no deterministic verifier exists for it.

**Tests:** for each scheme, a positive case reaching `authorize_probable` and a
finding; the same-bank-different-account negative for kickback; a two-PO cluster
(below the count floor) not verifying for splitting; a cluster spanning longer
than the window not verifying; an ambiguous exhibit set producing a claim refusal;
and confirmation that the three still-unsupported schemes remain refused.

---

## Task 8: Case file with a rendered money trail diagram

**Files:** create `src/output/case_file.py`, `tests/test_case_file.py`; modify
`src/run_audit.py`

`case_file_structure.md` is a scored artifact we do not produce at all, and it
says plainly: **a prose-only money trail caps Clarity at 3.**

Emit Markdown with a Mermaid `flowchart LR` block per finding. Markdown with
Mermaid renders as a diagram on GitHub and in most viewers, needs no network and
no dependency, and stays diffable for the determinism check.

**Public API:**

```python
def render_case_file(
    submission: Submission,
    case_reports: Sequence[CaseFileEntry],
    estate_path: str,
    *,
    company_name: str,
    audit_period: str,
) -> str
```

`CaseFileEntry` is a small model carrying, per finding, what only the pipeline
knows: the verification report's check calculations, the claim `formula`, and the
adversarial review outcomes (the Challenger's and Method Critic's verdict and
reasoning summary). `run_audit` assembles these as it authorizes each case.

**Required sections, in this exact order** (the spec is explicit):

1. **Header** — company name, audit period, estate seed, LLM call count, MXN
   cost, wall-clock seconds, and whether the run is deterministic.
2. **Executive summary** — a few plain sentences plus the summary table with
   exactly the rows `Findings` (count with confidence levels), `Total exposure`
   (pesos), `Leads investigated and closed` (count).
3. **One section per finding**, each with, in order: heading (entity name and id,
   scheme type), rule broken, amount and confidence, what happened (the
   narrative, under 150 words), **the money trail as a Mermaid diagram**, an
   exhibits table (exhibit id, source table, record id, what it proves), and the
   reconciliation arithmetic. Then a short "Adversarial review" subsection: what
   was argued and why the finding survived.
4. **Leads not pursued** — in the body, not an appendix. One entry per lead:
   entity, which detector pointed there, the specific reason referencing the
   evidence examined, which tools were called, and what closed it.
5. **Method and limits** — the architecture in a few sentences; what was out of
   scope; what the system **cannot** detect (name the schemes with no verifier,
   the fact that a score is not a fraud probability, and that absence from the
   estate is not absence in reality); and how to regenerate the file.

When a finding's `money_trail` is empty, the diagram must still render — show the
cited invoice and vendor nodes and label the payment link explicitly as *"no bank
transfer for this amount was cited in the supplied estate"*. Never draw an edge
that implies a transfer we did not verify. Never put a CLABE in the diagram.

Add `--case-file <path>` to `run_audit`. Write it with the same atomic-write
helper style used by `write_submission_json`. Escape any Mermaid-breaking
characters in labels (quotes, newlines, `[`, `]`).

**Tests:** all five sections present in order; a Mermaid fence per finding; the
no-trail fallback renders and says no transfer was cited; no `CLABE:` and no
18-digit run appears anywhere in the output; the leads section names tools and
the closer; byte-identical output for the same submission rendered twice; and the
exhibits table lists every exhibit of every finding.

---

## Task 9: revenue_inflation detector and verifier

**Files:** create `src/detectors/deterministic_revenue.py`; modify
`src/investigation/estate_pipeline.py`, `src/verifier/official_verifier.py`,
`src/gates/evidence_gate.py`, `src/investigation/claim_builder.py`,
`src/output/finding_builder.py`, `src/rules/default_rules.py`; create
`tests/test_revenue_inflation.py`

This task REVERSES an earlier decision in this plan. `revenue_inflation` was cut for
having no detector. Measurement on the held-out adversarial estate showed it is the
LARGEST scheme present — 13,953,060 MXN — and produces ZERO observations, making it our
single biggest recall hole. Unlike round_tripping, its evidence is fully determinate from
the estate, so the cut was wrong.

**Detector** — `detect_period_end_revenue_without_cash`. The company is the receiver on
purchases, so an invoice whose `issuer_rfc` is the company's own RFC is a sale. Flag such
invoices issued within a named window of a period close that are either
`status == 'cancelado'` or have no `bank_txns` row whose `amount` matches the invoice
`total` crediting the company's CLABE. Pair that with the ledger side: a credit to a
revenue account with no corresponding cash debit. The window and the period-close dates
are module-level named constants with a comment stating they are internal parameters this
system defines, not law. Declare `limitations`, `legitimate_alternatives` (genuine
period-end sales; a cancelled-and-reissued pair that nets to zero; payment terms that
legitimately postpone receipt) and `recommended_checks`.

The company's own RFC must be derived from the estate, never hardcoded: it is the
`receiver_rfc` appearing on the overwhelming majority of purchase invoices. Compute it
deterministically and state the rule in a docstring.

**Verifier** — `REVENUE-INFLATION-NO-CASH-RECEIPT`: `verified` only when, from cited
records, a company-issued invoice is either cancelled after the period close or has no
matching receipt in the estate, AND a cited `ledger` entry credits revenue for that
invoice. Write the absence honestly in `check.calculation`: "no bank_txns record matching
this amount was found in the supplied estate", never "no payment exists" (Global
Constraint 4). Claim scope: the sum of `invoices.total` over the qualifying cited
company-issued invoices. Rule: the company's own revenue-recognition policy — do NOT
cite a statute. `does_not_prove` must state that an unpaid or cancelled invoice is not by
itself evidence of deliberate inflation.

**Tests:** the cancelled-after-close positive; the no-receipt positive; a
cancelled-and-reissued pair netting to zero MUST NOT verify; a genuine period-end sale
that WAS paid MUST NOT verify; an ambiguous cited set produces a claim refusal; and the
company-RFC derivation works on an estate where the company is not the alphabetically
first RFC.

---

## Out of scope for this plan

Stated so a reader knows it was a decision, not an oversight:

- **`round_tripping` verifier.** The detector exists and fires correctly on the held-out
  estate, but a defensible verifier must prove the SAME pesos returned, and the estate
  carries dates without intraday timestamps. The held-out estate makes the danger
  concrete: decoy `RFC:ORB160721LW3` is a benign 3-CLABE cycle running through the SAME
  company operating account as a real round trip, with identical topology. The two are
  separable only by amount continuity (legs within 1.5% of each other versus differing
  16-fold) and date proximity (3-4 days versus 5-10 months). Until that continuity test
  lives in the detector, a verifier would accuse the decoy. Round-tripping stays a lead.
- **`authorize_proven`.** No scheme has a proof standard, so the ceiling stays
  `probable`.
- **GNN training on judged estates.** Discovery only, as today.
- **Any change to `validate_format.py`.**
