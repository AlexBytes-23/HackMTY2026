# OFFICIAL CONTRACT AUDIT

## 1. Module Assessment

| module/file | current role | external-contract dependency | status | problem | recommended action | priority |
|---|---|---|---|---|---|---|
| `src/agents/__init__.py` | Various | No | production-compatible | None | Maintain | None |
| `src/agents/challenger.py` | Adversarial challenge | No | production-compatible | None | Maintain | None |
| `src/agents/method_critic.py` | Methodological critique | No | production-compatible | None | Maintain | None |
| `src/core/__init__.py` | Package init | No | production-compatible | None | Maintain | None |
| `src/core/estate.py` | Read SQLite estate | Yes (schema, columns) | production-compatible | None | Maintain | None |
| `src/core/models.py` | Internal orchestration models | Minimal | production-compatible | Output structs belong elsewhere | Keep internal; output models created | None |
| `src/detectors/bayes_tabular.py` | Statistical inference | No | experimental/legacy | Uncalibrated posterior | Do not use as final proof | Medium |
| `src/detectors/deterministic_tabular.py` | Deterministic signals | Yes | production-compatible | None | Maintain | None |
| `src/detectors/tabulares.py` | Tabular detector (experimental) | Yes (columns) | experimental/legacy | Uses legacy columns (vendor_id) | Refactor or retire | Medium |
| `src/gates/__init__.py` | Various | No | production-compatible | None | Maintain | None |
| `src/gates/evidence_gate.py` | Final finding decision | Minimal | needs-migration | Checks exact peso equality; booleans | Deferred architectural review | Medium |
| `src/investigation/__init__.py` | Various | No | production-compatible | None | Maintain | None |
| `src/investigation/action_bank.py` | Investigator tools | Yes (db columns) | production-compatible | None | Maintain | None |
| `src/investigation/case_builder.py` | State orchestration | No | production-compatible | None | Maintain | None |
| `src/investigation/investigator.py` | Reasoning agent | No | production-compatible | None | Maintain | None |
| `src/investigation/lead_builder.py` | Lead builder | No | production-compatible | None | Maintain | None |
| `src/investigation/runner.py` | Run loop | No | production-compatible | None | Maintain | None |
| `src/output/__init__.py` | Package init | No | production-compatible | None | Maintain | None |
| `src/output/formatters.py` | Entity normalization | Yes (RFC:, EMP:) | production-compatible | None | Maintain | None |
| `src/output/models.py` | Official submission models | Yes (exact shapes) | production-compatible | None | Maintain | None |
| `src/output/reconciliation.py` | Peso reconciliation | Yes (2% tolerance, per-table) | production-compatible | None | Maintain | None |
| `src/rules/__init__.py` | Various | No | production-compatible | None | Maintain | None |
| `src/rules/rule_registry.py` | Rule storage | Yes (official rules) | production-compatible | Synthetic rules present | Supply actual official rules | High |
| `src/verifier/tabular_verifier.py` | Deterministic verifier | Yes (peso logic) | needs-migration | Not using official 2% tolerance | Wrap output.reconciliation | High |

## 2. External Contract Issues (Schema / Formatting)
- **Estate Schema:** Existing mock generators and `tabulares.py` use legacy identifiers (`vendor_id`, `invoice_id`).
- **Amount Columns:** Official amounts correctly implemented in `estate.py`.
- **Entity Resolution:** Official entity IDs are `RFC:<rfc>` and `EMP:<emp_id>`. Formatter created.
- **Scheme Enum:** Official: `phantom_vendor`, `kickback`, `round_tripping`, `threshold_splitting`, `revenue_inflation`.
- **Peso Reconciliation:** Must match `PESO_TOLERANCE = 0.02`. Sums are PER TABLE, and matched against the best table.

## 3. Ground Truth Leakage
- Verified that `src/` modules do not read `dev_answer_key.json` or `ground_truth` directly via smoke tests.

## 4. Integration Follow-ups
- Need to integrate `src/output/reconciliation.py` into the Verifier layer to generate actual finding peso amounts.
- Final submission artifact JSON builder needs to combine `run_metadata`, `leads_not_pursued`, and valid findings.

## 5. Evidence Gate Follow-ups
- Needs adaptation to consume the 2% tolerance boundary instead of exact numerical equality.
- Currently consumes caller-supplied booleans; should interact deterministically with `CaseState`.
- Lacks exhibit relevance/provenance checks beyond simple existence.

## 6. Graph Follow-ups
- Not applicable. No graph analysis modules present in `src/`.

## 7. Generator V2 Specification / TODO
- Dev generator must output SQLite or CSVs matching strictly the official 8-table format (`rfc`, `uuid`, etc.).
- Dates must use ISO 8601 strings.
- Must support deterministic seed-based generation for all 5 official schemes.
- Ground-truth evaluation files must be output outside `src/`.

## 8. Run Metadata / Replay Follow-ups
- Instrumentation required to log `llm_calls`, `mxn_cost`, `wall_clock_seconds` natively.

## 9. Unresolved Blockers
- **Rule Registry:** `rule_broken` is a mandatory output field, but `src/rules/rule_registry.py` only has dummy synthetic rules. An actual legal framework (e.g., SAT 69-B) must be provided before production findings can be emitted.
