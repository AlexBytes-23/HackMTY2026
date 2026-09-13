# OFFICIAL CONTRACT AUDIT

## 1. Module Assessment

| module/file | current role | external-contract dependency | status | problem | recommended action | priority |
|---|---|---|---|---|---|---|
| src/core/estate.py | Read SQLite estate | Yes (schema, columns) | production-compatible | None | Maintain | None |
| src/core/models.py | Internal orchestration models | Minimal | production-compatible | Output structs belong elsewhere | Keep internal; output models created | None |
| src/output/models.py | Official submission models | Yes (exact shapes) | production-compatible | None | Maintain | None |
| src/output/formatters.py | Entity normalization | Yes (RFC:, EMP:) | production-compatible | None | Maintain | None |
| src/output/reconciliation.py| Peso reconciliation | Yes (2% tolerance, per-table) | production-compatible | None | Maintain | None |
| src/detectors/tabulares.py | Detector logic | Yes (columns, anomaly models) | needs-migration | Uses legacy column names (e.g. endor_id) | Refactor detectors to use official schema | High |
| src/detectors/bayes_tabular.py | Statistical inference | No | experimental/legacy | Posterior is not calibrated | Mark as experimental, do not use as final proof | Medium |
| src/investigation/action_bank.py | Investigator tools | Yes (db columns) | needs-migration | Tools expect legacy columns | Update tools to query official schema | High |
| src/investigation/investigator.py | Reasoning agent | No | production-compatible | None | Maintain | None |
| src/investigation/case_builder.py | State orchestration | No | production-compatible | None | Maintain | None |
| src/investigation/runner.py | Run loop | No | production-compatible | None | Maintain | None |
| src/agents/challenger.py | Adversarial challenge | No | production-compatible | None | Maintain | None |
| src/agents/method_critic.py | Methodological critique | No | production-compatible | None | Maintain | None |
| src/verifier/tabular_verifier.py | Deterministic verifier | Yes (peso logic) | needs-migration | Doesn't use official 2% tolerance per-table logic | Rebuild to wrap econciliation.py | High |
| src/rules/rule_registry.py | Rule storage | Yes (official rules) | production-compatible | Synthetic rules present | Supply actual official rules | Low |
| src/gates/evidence_gate.py | Final finding decision | Minimal | needs-migration | Checks exact peso equality instead of 2% tolerance per table; uses internal booleans | Needs later architectural review (deferred) | Medium |

## 2. External Contract Issues (Schema / Formatting)
- **Estate Schema:** Existing mock estate generators and some tools (	abulares.py, ction_bank.py) likely use endor_id, invoice_id, 	otal_amount, origin_account, destination_account. Official schema uses fc, uuid, 	otal, rom_clabe, 	o_clabe, 	xn_id.
- **Amount Columns:** Official amounts are invoices.total, ank_txns.amount, purchase_orders.amount, contracts.value. Implemented correctly in estate.py.
- **Entity Resolution:** Official entity IDs are RFC:<rfc> and EMP:<emp_id>. Formatter created.
- **Scheme Enum:** Official: phantom_vendor, kickback, ound_tripping, 	hreshold_splitting, evenue_inflation.
- **Peso Reconciliation:** Must match PESO_TOLERANCE = 0.02. Sums are PER TABLE, and matched against the best table, avoiding cross-table double counting. Reusable utility created in econciliation.py.

## 3. Internal Design Issues
- **Evidence Gate:** Currently assumes exact reconciliation and trusts caller-supplied booleans instead of CaseState.
- **Tabular Verifier:** Re-calculates facts arbitrarily. Needs to be replaced with or wrap the exact official validator logic.

## 4. Ground Truth Leakage
- Verified that src/ modules do not read dev_answer_key.json or ground_truth directly via smoke tests.
