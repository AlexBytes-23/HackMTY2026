# CODEBASE MAP — Forensic Auditor (HackMTY 2026 / Infosys Track 2)

> **Para qué sirve este archivo.**
> Es el brief maestro del repo. Está escrito para que **un agente de IA (Claude Code, Antigravity, Cursor, Gemini)
> pueda leerlo solo y entender todo el sistema sin explorar 16,000 líneas**: el contrato oficial del reto,
> la arquitectura real, qué hace cada módulo, qué invariantes no se pueden romper, qué falta, y en qué
> orden atacarlo.
>
> Última auditoría de código: rama `feature/vertical-slice-smoke`, commit `98194a5`.
> Estado verificado: **222 tests pasan** (excluyendo 3 módulos de test que requieren `torch`). ~16,300 LOC.

---

## 0. Cómo usar este documento

| Si eres… | Lee |
|---|---|
| Un agente que va a escribir código aquí | §1 (contrato), §6 (invariantes), §8 (gaps), luego el módulo que vas a tocar en §5 |
| Un humano que se acaba de unir | §2, §4, §7 |
| Alguien preparando el pitch/demo | §1, §7, §12 |

**Regla de oro del repo:** ninguna capa puede afirmar más de lo que su evidencia soporta.
Un detector produce *observaciones*, no acusaciones. Un LLM propone, Python verifica. Un finding solo
existe si un verificador determinista lo reconstruyó desde filas reales del estate.

---

## 1. El contrato oficial (NO negociable)

Fuente: `official_materials/student-materials/forensic-auditor/`. Todo lo de esta sección es
literal del reto y decide la calificación.

### 1.1 El estate (entrada)

8 tablas, nombres y columnas **exactos** (`estate_schema.sql`):

| Tabla | PK | Columna de monto | Campos clave |
|---|---|---|---|
| `vendors` | `rfc` | — | `legal_name, registered_date, address, bank_clabe, category, contact_email` |
| `invoices` | `uuid` | `total` | `issuer_rfc, receiver_rfc, issue_date, subtotal, iva, concepto_text, uso_cfdi, forma_pago, metodo_pago, status` |
| `ledger` | `entry_id` | — | `date, account_code, account_name, debit, credit, description, invoice_uuid, cost_center, approver` |
| `bank_txns` | `txn_id` | `amount` | `date, from_clabe, to_clabe, reference, channel` |
| `purchase_orders` | `po_id` | `amount` | `vendor_rfc, date, requester, approver, description` |
| `contracts` | `contract_id` | `value` | `vendor_rfc, start_date, scope_text` |
| `employees` | `emp_id` | — | `name, role, bank_clabe, hire_date` |
| `efos_list` | `rfc` | — | `legal_name, status (definitivo\|presunto), publication_date` |

- El estate llega en **SQLite (`estate.db`) o CSV (`estate_csv.zip`, 8 archivos UTF-8)**. Declaramos cuál leemos y los jueces usan ese.
- **La ruta del estate se recibe en runtime. Rutas hardcodeadas = descalificación del criterio.**
- Los jueces corren el sistema sobre **estates nunca vistos**, con hasta **5 esquemas y 10 decoys**, y los esquemas pueden estar **entrelazados** (dos esquemas compartiendo una entidad).

### 1.2 La salida (`submission.json`)

```
{ seed:int, findings:[...], leads_not_pursued:[...], run_metadata:{...} }
```

**Finding** requiere: `scheme_type, entities, narrative, rule_broken, peso_amount, exhibits, confidence`
(+ `money_trail`, que el validador oficial marca como error si está **ausente**).

- `scheme_type` ∈ `phantom_vendor | kickback | round_tripping | threshold_splitting | revenue_inflation`
- `entities`: strings con prefijo de tipo — `RFC:AAAA010101AA1`, `EMP:0001`. **Cada entidad necesita al menos un exhibit que la soporte.**
- `narrative`: ≤ **150 palabras**, lenguaje llano, entendible por un juez no técnico.
- `rule_broken`: la regla/artículo concreto (`SAT Artículo 69-B`). **No** "patrón estadístico anómalo".
- `peso_amount`: > 0 y debe **reconciliar dentro del 2 %** con la suma de los exhibits citados.
- `exhibits`: **mínimo 3**, cada uno `{exhibit_id, source_table, record_id, note}`; `record_id` debe existir en el estate (los ids alucinados se detectan).
- `money_trail`: pasos ordenados; el `to` de un paso es el `from` del siguiente; cada paso cita un `exhibit_id` del mismo finding.
- `confidence` ∈ `proven | probable`.

**Regla de reconciliación de pesos (crítica y mal entendida):**
los montos se suman **POR TABLA** y se comparan contra la tabla que mejor empata. Citar una factura
*y* la transferencia que la liquidó **no** se cuenta como dinero doble. Tolerancia `2 %` sobre `max(mejor_total, 1)`.

**`leads_not_pursued`** (`entity, signal, reason` obligatorios; `tool_calls_made`, `closed_by` opcionales pero valen):
va **en el cuerpo del case file, no en un apéndice**. Un juez va a escoger una entidad de ahí y preguntar
"¿por qué no la acusaste?" — la respuesta debe leerse **de la página en menos de 10 segundos**.
`reason` genérico ("insufficient evidence") puntúa como genérico.

**`run_metadata`**: `llm_calls` (int), `mxn_cost` (float), `wall_clock_seconds` (float),
opcionales `cost_by_role`, `deterministic`. "No sabemos" puntúa bajo en Feasibility.

### 1.3 El case file (artefacto que el juez lee)

Secciones obligatorias, **en este orden** (`case_file_structure.md`):
1. **Header** — empresa, periodo, seed, llm_calls, costo MXN, wall-clock, si es determinista.
2. **Executive summary** — con tabla `Findings / Total exposure / Leads investigated and closed`.
3. **Una sección por finding** — heading (entidad + id + esquema), rule broken, monto + confianza,
   qué pasó (<150 palabras), **money trail como DIAGRAMA RENDERIZADO (prosa sola ⇒ Clarity tope 3)**,
   tabla de exhibits, y la **aritmética de reconciliación**. Si hubo revisión adversarial: qué argumentó y por qué el finding sobrevivió.
4. **Leads not pursued** — en el cuerpo.
5. **Method and limits** — arquitectura, qué quedó fuera de alcance, **qué NO puede detectar el sistema**, reproducibilidad.

Formato: HTML / PDF / Markdown. **Se debe poder generar sin red.**

### 1.4 Reglas que deciden el score

- **Recall es la mitad. Las acusaciones falsas contra decoys pesan al menos igual.** Acusar a todos = recall perfecto y score malo.
- Reportar recall **y** tasa de falsa acusación, como tabla, sobre **≥ 5 seeds held-out**.
- Los seeds de tuning y los de reporte deben ser **disjuntos y nombrados** en el pitch.
- **Ground truth inalcanzable desde el agente.** Solo el harness de evaluación puede tocarlo. Los jueces pueden correr
  `grep -r 'ground_truth' your_project/src/ --include='*.py'`. Si parece haberse filtrado, **Results tope 2**.
- **Determinismo:** el mismo seed debe producir el mismo case file. Pueden correrlo dos veces.
- **Replay sin red.**
- Preguntas que harán: "¿qué pasa si cambio este input?", "¿por qué confío en este número?",
  "¿qué hace cuando se equivoca o cuando no hay nada?", "¿por qué no marcaste al vendor X?",
  "¿y si el empleado simplemente tiene cuenta en el mismo banco?".
  **Responder desde un log en <10 s también se califica. Re-correr el sistema es la respuesta equivocada.**

---

## 2. Tesis arquitectónica

No estamos construyendo "una IA que diga fraude". Estamos construyendo un **investigador que separa
hechos, anomalías, hipótesis y acusaciones**, y que somete sus propios criterios a crítica antes de emitir un finding.

Niveles con responsabilidades distintas:

| Nivel | Quién | Responsabilidad |
|---|---|---|
| Determinista | Python/pandas/SQL | Sumas, joins, conciliación, existencia de registros. **Nunca el LLM.** |
| Algorítmico | grafos, estadística, GNN opcional | Encontrar estructura (ciclos, clusters, rareza). Produce *observaciones*. |
| Investigativo | Investigator (LLM) | Decidir qué mirar después. No concluye culpabilidad. |
| Meta-razonamiento | Challenger + Method Critic (LLM) | ¿Puede ser falsa esta historia? ¿Está mal construido el método? |
| Autoridad | Verifier + Evidence Gate | Recalcula y decide si se permite acusar. **Único que autoriza.** |

Separación explícita de los dos críticos:
- **Challenger (Case Critic):** "¿Puede esta historia ser falsa? ¿Cuál es la explicación legítima más fuerte?"
- **Method Critic:** "¿Nuestro procedimiento para construir esta historia está sesgado o mal fundamentado?"
  (p. ej.: dos detectores que salen de las mismas filas **no** son evidencia independiente).

---

## 3. Mapa del repositorio

```
HackMTY2026/
├── .env                      # GEMINI_API_KEY  (gitignored, NO commitear)
├── requirements.txt          # pandas, pydantic, pytest, networkx
├── requirements-gnn.txt      # + torch cpu, torch-geometric   (opcional)
├── data/                     # VACÍO  ← no hay estate de desarrollo
├── docs/
│   ├── OFFICIAL_CONTRACT_AUDIT.md   # auditoría previa módulo-por-módulo
│   └── CODEBASE_MAP.md              # (este archivo)
├── official_materials/student-materials/forensic-auditor/
│   ├── estate_schema.sql  submission_schema.json  ground_truth_schema.json
│   ├── case_file_structure.md  submission_example.json
│   ├── validate_format.py            # validador oficial (stdlib, structure-only)
│   ├── results_table_template.csv
│   └── estate_csv_example/*.csv      # forma de columnas, NO dataset
├── src/
│   ├── core/        estate.py  models.py
│   ├── detectors/   deterministic_tabular.py  deterministic_relational.py
│   │                tabulares.py  bayes_tabular.py        ← LEGACY, fuera del path
│   ├── graph/       bank_graph.py
│   ├── gnn/         contracts, features, graph_builder, model, training,
│   │                scoring, discovery, observation_adapter, runtime   ← opcional
│   ├── investigation/ lead_builder, case_builder, action_bank, investigator,
│   │                runner, review_orchestrator, preverification_pipeline,
│   │                postverification_pipeline, estate_pipeline
│   ├── agents/      challenger.py  method_critic.py
│   ├── verifier/    official_verifier.py  tabular_verifier.py   ← el 2º es LEGACY
│   ├── gates/       evidence_gate.py
│   ├── rules/       rule_registry.py        ← vacío en runtime
│   └── output/      models.py reconciliation.py formatters.py
│                    finding_builder.py submission_builder.py
└── tests/           30 módulos, 222 tests verdes (sin torch)
```

Nota: `src/`, `src/detectors/` y `src/verifier/` **no tienen `__init__.py`** (namespace packages implícitos).
Funciona, pero es inconsistente con el resto.

---

## 4. Flujo end-to-end (el que existe hoy)

```
                      estate.db (ruta en runtime)
                               │
                    ┌──────────▼──────────┐
                    │  EstateRepository   │  valida las 8 tablas y columnas
                    └──────────┬──────────┘
                               │  DataFrames / dicts
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
 deterministic_tabular  deterministic_relational   gnn.runtime (opcional, off por defecto)
   · CLABE compartida        · vendor→employee txn      · HeteroGNN multi-seed
     vendor↔employee         · clusters de facturas       auto-supervisada
   · CLABE compartida        · ciclos bancarios         · scores peer-relativos
     vendor↔vendor             dirigidos                  (NO probabilidades)
   · vendor ∈ efos_list
        │                      │                      │
        └──────────────────────┴──────────┬───────────┘
                                          │  list[Observation]
                                   build_leads()          agrupa por entities[0]
                                          │  list[Lead]
                                   build_case_state()     Lead → CaseState + CaseEvidence
                                          │
        ╔═════════════════════════════════▼══════════════════════════════════╗
        ║              run_preverification_pipeline (por caso)               ║
        ║                                                                    ║
        ║  run_investigation_loop  ──►  Investigator (LLM) decide            ║
        ║     THINK → ACT (Action Bank) → OBSERVE → UPDATE                   ║
        ║     stop: request_review | ready_for_verification |                ║
        ║           close_inconclusive | repeated_action | max_steps         ║
        ║                              │                                     ║
        ║  run_review_orchestration ───┤                                     ║
        ║     Challenger(LLM) ─ outcome:                                     ║
        ║        legitimate_alternative → hipótesis inconclusive, STOP       ║
        ║        needs_more_evidence / alternative_hypothesis                ║
        ║               → absorbe preguntas → re-investiga → otra ronda      ║
        ║        survives → Method Critic(LLM)                               ║
        ║                      cannot_support → STOP                         ║
        ║                      needs_more_work → re-investiga                ║
        ║                      clear + scheme_type ≠ None → READY            ║
        ╚═════════════════════════════════╤══════════════════════════════════╝
                                          │  PreVerificationResult
                     run_postverification_pipeline
                                          │
                    ┌─────────────────────▼─────────────────────┐
                    │ OfficialVerifier.verify()                 │
                    │  · EXISTS-<tabla>-<id> por exhibit        │
                    │  · PHANTOM-EFOS-INVOICE-LINK (solo PV)    │
                    │  · PESO-RECONCILIATION (2 %, per-table)   │
                    └─────────────────────┬─────────────────────┘
                                          │  VerificationReport
                                   evaluate_gate()
                     · rule_id válido y aplicable al scheme_type
                     · Challenger y MethodCritic presentes y limpios
                     · ≥3 exhibits únicos, reconcilia, checks críticos verified
                                          │  EvidenceGateDecision
                        authorize_probable │ need_more_work │
                        decline_hypothesis │ inconclusive
                                          │
                                   build_finding()      ← SOLO phantom_vendor
                                          │  SubmissionFinding
                                 build_submission()
                              write_submission_json()   ← escritura atómica
```

**Invariante clave ya implementado:** un `ready_for_verification` del Investigator **nunca** salta
al Verifier — siempre pasa por Challenger + Method Critic (`preverification_pipeline.py`).

---

## 5. Inventario módulo por módulo

### 5.1 `src/core/`

**`models.py`** — el idioma interno (Canonical Evidence Model).
`EvidenceRef(source_table, record_id, note)` · `Observation` (statement + evidence + facts + `limitations`
+ `legitimate_alternatives` + `recommended_checks`; `score` siempre acompañado de `score_semantics`)
· `Lead` · `Hypothesis(status: open|supported|rejected|inconclusive)` · `CaseEvidence(direction: for|against|neutral)`
· `ActionRecord` (auditoría completa de cada acción, incluidas las fallidas) · `CaseState` (memoria del caso)
· `ProposedAction` · `InvestigatorDecision` · `InvestigationLoopResult` · `VerifiedFact`.
*Nota:* `ChallengerReview` y `MethodReview` aquí son **stubs muertos** — los reales viven en `src/agents/`.

**`estate.py`** — `EstateRepository`, único acceso al estate.
Valida al construir que existan las 8 tablas y todas las columnas oficiales (falla ruidosamente si no).
Diccionarios `OFFICIAL_COLUMNS`, `ID_COLUMN`, `AMOUNT_COLUMN` (espejo exacto del validador oficial).
API: `table_df`, `get_all`, `get_record`, `record_exists`, `get_record_amount`, `get_vendor/_employee/_invoice/_bank_txn/_efos_record`,
`get_vendor_invoices/_purchase_orders/_contracts`, `get_bank_transactions_for_clabe`, `get_ledger_for_invoice`,
`find_vendor_by_clabe`, `find_employee_by_clabe`. Whitelist de tablas ⇒ sin SQL injection por nombre de tabla.
**Solo SQLite.** No lee `estate_csv.zip`.

### 5.2 `src/detectors/` — producción

**`deterministic_tabular.py`** (3 detectores, todos sin score, con `limitations` y `legitimate_alternatives` explícitas):
- `detect_vendor_employee_shared_clabe` — misma CLABE en `vendors` y `employees`. *Hecho de vínculo de cuenta, no kickback.*
- `detect_shared_vendor_clabe` — una CLABE con ≥2 RFCs distintos.
- `detect_efos_vendor_matches` — RFC de vendor presente en `efos_list`; conserva `status` y `publication_date`.

IDs de observación deterministas vía SHA-1 truncado (`OBS-TAB-<PREFIX>-<12 hex>`).

**`deterministic_relational.py`**:
- `detect_vendor_to_employee_transfers` — transferencia desde CLABE de vendor a CLABE de empleado.
- `detect_short_window_similar_invoice_clusters` — ≥3 facturas del mismo emisor→receptor en ventana de 7 días
  con spread relativo ≤3 %. Conserva solo ventanas maximales. **Declara explícitamente que el estate no trae
  umbral de aprobación, así que NO afirma threshold splitting.**
- `detect_directed_bank_transfer_cycles` — delega en `graph/bank_graph.py`.

**`tabulares.py` y `bayes_tabular.py` — LEGACY, NO usados por el pipeline.**
Usan un esquema que no existe (`invoice_id`, tabla `payments`, `vendor_id`), importan `sklearn`
(que **no está en `requirements.txt`**) y ejecutan DataFrames de demo a nivel de módulo al importarse.
Contienen entropía de Shannon, Isolation Forest y una red bayesiana con prior 2 % no calibrado.

### 5.3 `src/graph/bank_graph.py`

Multigrafo dirigido exacto de `bank_txns` (nodos = CLABEs, una arista por `txn_id`, sin colapsar paralelas).
Falla si hay `txn_id` duplicados (la provenance sería ambigua). `find_directed_bank_cycles` corre sobre una
proyección simple (`nx.simple_cycles(length_bound=...)`), canonicaliza rotaciones, excluye self-loops,
elige una arista representante determinista por arco y calcula `date_span_days`.
**Documenta que un ciclo estructural ≠ mismo dinero regresando.**

### 5.4 `src/gnn/` — descubrimiento neuronal opcional

Solo se activa con `enable_gnn=True`. `runtime.py` importa tarde el stack neuronal y captura cualquier
excepción como `status="error"` sin tumbar la auditoría determinista.

- `contracts.py` — `GNNGraphBundle`: `HeteroData` + mapeos reversibles a IDs reales + `edge_provenance`.
  **Los IDs nunca entran como features numéricas.**
- `features.py` — features de bajo nivel (conteos, totales, spans, grados). `assert_low_level_feature_contract`
  **prohíbe** tokens forenses (`fraud`, `kickback`, `round_trip`, …) en nombres de feature, para que la GNN
  no reaprenda las etiquetas que ya decidieron los detectores. Escalado robusto log1p + mediana/IQR por tipo de nodo.
- `graph_builder.py` — nodos `vendor|employee|account`; aristas `owns`, `owned_by_*`, `transfers_to`
  (con `edge_attr`: txn_count, total, mean, max, date_span).
- `model.py` — `HeteroDiscoveryGNN`: message passing relación-a-relación, `mask_embeddings` aprendidas
  (para que un 0 enmascarado no se confunda con un 0 real), decoders de reconstrucción + bilinear de aristas.
- `training.py` — auto-supervisado: reconstrucción de features enmascaradas + predicción de aristas **held-out**
  (no tautológica). Seed determinista, early stopping sobre una vista de monitoreo fija, grad clipping.
- `scoring.py` — error de reconstrucción por feature + "edge surprise" en 5 folds; percentiles **entre pares
  del mismo tipo**. `SCORE_SEMANTICS` deja claro: rank relativo al estate, **no** probabilidad calibrada.
- `discovery.py` — ensamble multi-seed (mediana, IQR, stddev). La estabilidad mide optimización, no generalización.
- `observation_adapter.py` — `GNNObservationPolicy` (score mediano ≥0.85, IQR ≤0.25, ≥5 pares, ≤3 por tipo)
  → `Observation` con EvidenceRefs a registros reales.

### 5.5 `src/investigation/`

**`lead_builder.py`** — agrupa observaciones por `entities[0]`, une entidades, acumula
`recommended_checks` como `open_questions`, y añade 3 preguntas fijas (explicación legítima / qué distingue /
qué contradice). **No asigna `scheme_type`.**

**`case_builder.py`** — `Lead + Observations → CaseState`. Falla si el Lead referencia observaciones ausentes
(no construye casos incompletos en silencio). Cada observación se vuelve `CaseEvidence` con `direction="neutral"`.

**`action_bank.py`** — 7 acciones, cada una con `purpose`, `required_arguments`, `returns`,
**`does_not_prove`** y `legitimate_exceptions` (ese metadato se le muestra al LLM):
`get_vendor`, `get_employee`, `get_vendor_contracts`, `get_vendor_purchase_orders`,
`get_vendor_invoices`, `get_bank_transactions_for_clabe`, `check_efos`.
`execute_action` es el único punto de ejecución; el LLM nunca llama funciones arbitrarias.

**`investigator.py`** — `LLMClient` es un `Protocol` (`complete(system_prompt, user_prompt) -> str`).
`INVESTIGATOR_SYSTEM_PROMPT`: 18 reglas (anomalía≠fraude; KNOWN/INFERENCE/UNKNOWN; no inventar;
solo acciones del Action Bank; buscar evidencia contraria; nunca declarar culpabilidad).
`parse_investigator_decision` valida JSON → Pydantic; `validate_requested_action` impide herramientas inventadas.

**`runner.py`** — aplica una decisión sobre el CaseState y corre el loop.
Merge de hipótesis por ID (no borra hipótesis anteriores). Firma estable de acción
(`nombre:json(args, sort_keys)`) para cortar repeticiones exactas.
**Detalle importante y correcto:** una query exitosa con 0 resultados **no** crea `CaseEvidence`
(solo `ActionRecord`) — ejecutar no es lo mismo que producir evidencia.

**`review_orchestrator.py`** — el bucle adversarial completo descrito en §4.
Crea hipótesis alternativas con IDs `<target>-ALT-01`; absorbe preguntas del Challenger/MethodCritic
como `unknowns`; re-investiga; re-selecciona target. Corta con `max_review_rounds`.

**`preverification_pipeline.py`** — Investigator + revisión obligatoria hasta la frontera del Verifier.
Selección determinista del target (prefiere `supported`, si no el primer `open`).

**`postverification_pipeline.py`** — la frontera determinista.
Distingue **parada investigativa normal** (`preverification_blocked`) de **contradicción del propio programa**
(`PostVerificationBoundaryError`). `claimed_amount` y `requested_rule_id` son **entradas externas**:
esta capa nunca las deriva de los exhibits ni del Method Critic.

**`estate_pipeline.py`** — orquestador de descubrimiento→preverificación para todos los leads.
Aísla fallos por caso (un JSON malformado no borra el resto). Metadata operativa (no oficial):
conteos de observaciones/leads/casos ready/blocked/error + `wall_clock_seconds` + estado del GNN.

### 5.6 `src/agents/`

**`challenger.py`** — `ChallengerReview` con `outcome ∈ survives | needs_more_evidence | legitimate_alternative | alternative_hypothesis`.
Validadores Pydantic que **fuerzan coherencia**: `needs_more_evidence` exige al menos una pregunta
*y* una acción propuesta; `alternative_hypothesis` exige la hipótesis; `legitimate_alternative` exige la explicación.
System prompt de 13 reglas epistémicas (hecho≠interpretación; no presumir fraude ni legitimidad;
**prohibido inventar ley/umbrales/políticas**; EFOS es evidencia no prueba automática;
señales múltiples no son automáticamente independientes; solo acciones disponibles).
`validate_challenger_review` verifica en Python: hipótesis correcta, acciones existentes con argumentos
requeridos, y que **ningún `evidence_id` citado sea inventado**.

**`method_critic.py`** — `outcome ∈ clear | needs_more_work | cannot_support`.
`find_evidence_dependencies()` es **determinista**: detecta evidencias que comparten exactamente el mismo
registro fuente y se lo pasa al prompt como `deterministic_dependency_analysis` — así el crítico no tiene
que adivinar si dos señales son independientes. 11 reglas de oro (incluye "ausencia en el estate ≠ ausencia
en la realidad" y "score estadístico ≠ probabilidad calibrada"). Valida que no aluciné `evidence_ids` ni `action steps`.

### 5.7 `src/verifier/`, `src/gates/`, `src/rules/`

**`official_verifier.py`** — `OfficialVerifier.verify()`:
1. reúne exhibits desde `supporting_evidence_ids` de la hipótesis;
2. `EXISTS-<tabla>-<id>` por cada uno (críticos), marca duplicados y faltantes;
3. verificación sustantiva: **solo `phantom_vendor`** (`PHANTOM-EFOS-INVOICE-LINK`: intersección exacta
   entre `efos_list.rfc` y `invoices.issuer_rfc`). Cualquier otro esquema ⇒ check `unresolved`;
4. `PESO-RECONCILIATION`. **Si no le dan `claimed_amount`, NO lo infiere** — lo deja `unresolved`.

**`tabular_verifier.py` — LEGACY.** Esquema inexistente (`payments`), tolerancia exacta `0.01` en vez del
2 % per-table oficial, entropía de Shannon. Fuera del path de producción.

**`gates/evidence_gate.py`** — el único que autoriza. Falla cerrado. Requiere simultáneamente:
rule_id válido y aplicable al `scheme_type`; ChallengerReview presente y no bloqueante;
MethodCriticReview presente y no bloqueante; VerificationReport del mismo target, sin errores internos,
con ≥3 exhibits únicos, reconciliación exitosa, `PESO-RECONCILIATION` verified y el verificador sustantivo
del esquema verified. Salidas: `authorize_probable | authorize_proven | need_more_work | decline_hypothesis | inconclusive`.
Hoy **solo emite `authorize_probable`** (no hay estándar "proven" implementado).

**`rules/rule_registry.py`** — `RuleDefinition(rule_id, title, source, source_reference, applies_to,
supports, does_not_prove, requirements, exceptions)` + registry en memoria.
**Vacío en runtime: nadie registra reglas en `src/`.**

### 5.8 `src/output/`

**`models.py`** — espejo Pydantic del `submission_schema.json`, con validadores que atrapan lo que el
validador oficial castiga: prefijo `RFC:`/`EMP:`, ≤150 palabras, ≥3 exhibits, `exhibit_id` únicos,
`money_trail.exhibit_id` ∈ exhibits del finding, y **continuidad del money trail** (`paso[i].to == paso[i+1].from`).

**`reconciliation.py`** — `reconcile_pesos()`: suma **por tabla**, elige la tabla más cercana al monto
reclamado, tolerancia `2 % · max(mejor, 1)`. Réplica exacta del validador oficial. `PESO_TOLERANCE = 0.02`.

**`formatters.py`** — `normalize_entity()`; rechaza prefijos contradictorios y payloads vacíos.

**`finding_builder.py`** — traduce estado ya autorizado a un `SubmissionFinding`.
**No investiga, no repara evidencia, no infiere el monto, no sube la confianza.**
8 chequeos defensivos: target coincide, gate = `authorize_probable`, esquema = `phantom_vendor`,
report coherente y cerrado, `PHANTOM-EFOS-INVOICE-LINK` y `PESO-RECONCILIATION` verified,
regla existe y aplica, `claimed_amount` finito y > 0, ≥3 exhibits únicos.
Narrativa conservadora que dice explícitamente *"these checks do not independently establish criminal intent"*.
`money_trail=[]` siempre. Deuda técnica marcada en el código: reconstruye la entidad por intersección
RFC desde el estate en vez de tomarla de `VerificationCheck.evidence`.

**`submission_builder.py`** — `build_submission` (no muta nada, preserva orden) +
`write_submission_json` (rechaza NaN/Inf, `allow_nan=False`, escritura **atómica** con `mkstemp` + `fsync` + `os.replace`).

### 5.9 `tests/` — 30 módulos, 222 tests

Cobertura fuerte de: contrato del estate, detectores, bank graph, GNN (4 módulos), investigator,
runner, review orchestrator, pre/post-verification, challenger, method critic, evidence gate,
verifier oficial, reconciliación, modelos de salida, finding builder (+ hardening), submission builder,
y `test_vertical_slice_smoke.py` — el **único test end-to-end determinista** (estate sintético →
verifier → gate → finding → submission.json), con aserciones anti-fuga de estructuras internas
y anti-lenguaje ("proves fraud", "criminal intent proven") en la narrativa.

---

## 6. Invariantes que NO se pueden romper

1. **El agente jamás toca ground truth.** Nada bajo `src/` puede leer `dev_answer_key.json` / `ground_truth*`.
   Verificado hoy: `grep -rn "ground_truth\|answer_key" src/` ⇒ limpio. **Mantenerlo así.**
2. **Aritmética en Python, nunca en el LLM.** Montos, sumas y matches los recalcula el verificador.
3. **Ningún finding sin `authorize_*` del Evidence Gate.**
4. **Nada llega al Verifier sin pasar por Challenger + Method Critic.**
5. **Solo acciones del Action Bank.** El LLM propone nombres; Python resuelve y ejecuta.
6. **No inventar ley.** Una regla legal/normativa solo se usa si viene del `RuleRegistry` (input explícito).
7. **Ausencia en el estate ≠ ausencia en la realidad.** Nunca "no hay contrato ⇒ no existe contrato";
   siempre "no se encontró registro de contrato en el estate suministrado".
8. **Toda afirmación apunta a `source_table` + `record_id` reales.**
9. **`Observation.score` siempre acompañado de `score_semantics`.** Nunca presentarlo como probabilidad de fraude.
10. **Sin rutas hardcodeadas.** El estate entra por parámetro.
11. **El esquema del estate es inmutable.** Nada de columnas `is_fraud` / `scheme_type` en la DB.
12. **Determinismo:** cualquier iteración, orden o selección debe ser estable (sort explícito, seeds fijos).

---

## 7. Estado real: qué funciona y qué no

### Funciona hoy
- Lectura y validación estricta del estate SQLite oficial.
- 6 detectores deterministas con provenance y limitaciones explícitas.
- Descubrimiento de ciclos bancarios exacto y acotado.
- Subsistema GNN completo, opcional, con semántica honesta y fallo aislado.
- Loop de investigación agentic con Action Bank blindado.
- Doble revisión adversarial (Challenger + Method Critic) con validación determinista de sus salidas.
- Verificador oficial + Evidence Gate que fallan cerrado.
- Modelos de salida que replican el validador oficial, incluida la reconciliación per-table al 2 %.
- Escritura atómica del `submission.json`.
- Slice vertical determinista end-to-end para `phantom_vendor` (en test).

### No existe todavía
- **Generador de estate de desarrollo** (`data/` está vacío) ⇒ sin datos, sin ground truth, sin métricas.
- **Implementación de `LLMClient`** (Gemini/Ollama). Es solo un `Protocol`.
- **Entrypoint CLI**. No hay `python -m src.main --estate <ruta> --out submission.json`.
- **Soporte de `estate_csv.zip`.**
- **Case file renderer** con **diagrama** de money trail.
- **`leads_not_pursued`**: los casos bloqueados/cerrados nunca se convierten en entradas.
- **`money_trail`** poblado (siempre `[]`).
- **4 de los 5 esquemas**: sin detector dedicado, sin verificador sustantivo, sin finding builder.
  `revenue_inflation` no tiene ni siquiera un detector.
- **Reglas legales** en el registry ⇒ el gate **siempre** falla por `rule_id missing or not valid`.
- **Cálculo de `claimed_amount`**: nadie lo produce.
- **`run_metadata` instrumentado** (`llm_calls`, `mxn_cost`, `wall_clock_seconds`).
- **Cache/replay de LLM** ⇒ sin determinismo real ni replay offline.
- **Harness de evaluación** (recall / false-accusation sobre ≥5 seeds) y tabla de resultados.

### Deuda y riesgos
- `detectors/tabulares.py`, `detectors/bayes_tabular.py`, `verifier/tabular_verifier.py`:
  código muerto con esquema inventado, dependencia `sklearn` ausente de `requirements.txt`,
  y DataFrames de demo ejecutados al importar. Un juez que hace `grep` en `src/` los va a ver.
- `tests/test_gnn_*.py` (3 módulos) hacen `import torch` a nivel de módulo ⇒ **`pytest` falla en collection**
  en una instalación base (solo `requirements.txt`). Deben usar `pytest.importorskip`.
- `lead_builder` agrupa por `entities[0]`: para un ciclo bancario eso es un `CLABE:`, no una entidad oficial.
  Esquemas entrelazados terminan en un único lead gigante o en leads duplicados.
- `finding_builder` reconstruye la entidad desde el estate (deuda marcada en el código).
- `MoneyTrailStep` usa alias `from`/`to` sin `populate_by_name` ⇒ hay que construirlo con las llaves alias.
- Falta `__init__.py` en `src/`, `src/detectors/`, `src/verifier/`.

---

## 8. Gaps ordenados por impacto en el score

| # | Gap | Criterio afectado | Severidad |
|---|---|---|---|
| 1 | Sin estate generator ni answer key | Results (no se puede medir nada) | Bloqueante |
| 2 | Sin LLMClient real | Todo el pipeline agentic | Bloqueante |
| 3 | Sin CLI / runner end-to-end | Feasibility, Results | Bloqueante |
| 4 | RuleRegistry vacío ⇒ gate nunca autoriza | Results = 0 findings | Bloqueante |
| 5 | Nadie calcula `claimed_amount` | Results | Bloqueante |
| 6 | Solo `phantom_vendor` verificable | Recall ≤ 1/5 de los tipos | Alta |
| 7 | Sin case file con diagrama | **Clarity tope 3** | Alta |
| 8 | `leads_not_pursued` vacío | Judgment (pregunta garantizada del juez) | Alta |
| 9 | `money_trail` vacío | Clarity | Media-alta |
| 10 | Sin `run_metadata` real | Feasibility ("no sabemos" puntúa bajo) | Media-alta |
| 11 | Sin cache/replay ⇒ sin determinismo ni modo offline | Feasibility, regla explícita | Media-alta |
| 12 | Sin harness de evaluación ni tabla de 5 seeds | Results (tope por pocos seeds) | Media-alta |
| 13 | Sin soporte CSV zip | Robustez ante el formato que elijan | Media |
| 14 | Código legacy con esquema falso en `src/` | Percepción del juez + reproducibilidad | Media |
| 15 | `pytest` no colecta sin torch | Feasibility / CI | Media |
| 16 | Lead grouping por `entities[0]` | Recall en esquemas entrelazados | Media |

---

## 9. Recomendaciones algorítmicas

### 9.1 Detectores faltantes (todos deterministas, todos con `limitations`)

**Cobertura documental (base de `phantom_vendor`)**
- Facturas **pagadas** sin `purchase_orders` ni `contracts` para ese `vendor_rfc`.
- Vendor sin `registered_date`, sin dirección, o con `contact_email` de dominio genérico.
- `registered_date` del vendor **posterior o muy cercano** a su primera factura grande.
- RFC genérico `XAXX010101000`.
- Cruce EFOS **con tiempo**: `efos_list.publication_date` vs `invoices.issue_date`, y `status`
  (`presunto` vs `definitivo`). El reto lo menciona explícitamente. Esto convierte "está en la lista"
  en "facturó **después** de ser publicado como definitivo", que es una acusación mucho más defendible.

**Conciliación 3-way / 4-way (invoice ↔ PO ↔ ledger ↔ bank)**
- Factura sin asiento en `ledger` (por `invoice_uuid`).
- Asiento de ledger sin factura.
- `debit ≠ credit` por `invoice_uuid` (partida doble rota).
- Pago bancario sin factura que lo respalde (por `reference` / monto / CLABE del vendor).
- **Pagos duplicados**: mismo `to_clabe` + mismo `amount` en ventana corta.
- Factura con `status = cancelado` pero pagada.
- Pago con fecha **anterior** a `issue_date` de la factura.
- `invoices.total ≠ subtotal + iva` (redondeos o manipulación).

**`threshold_splitting` (lo que falta hoy)**
- El umbral **no viene en el estate**, pero sí viene la pista: `purchase_orders.approver` y `ledger.approver`.
  Inferir el umbral **empíricamente y de forma auditable**: buscar el monto donde **cambia el aprobador**
  (montos < X aprobados por un rol, ≥ X por otro). Ese umbral inferido es una *observación con limitación
  declarada*, no una ley.
- Luego: clusters de facturas/POs **justo por debajo** del umbral inferido, mismo vendor, ventana corta,
  suma que lo supera.

**`kickback` (endurecer)**
- Encadenar: `empresa → vendor` (pago de factura) **seguido en ≤N días** de `vendor → empleado`
  por un **porcentaje estable** del pago original. Hoy solo se detecta la segunda pata.
- Cruzar con `purchase_orders.approver` / `ledger.approver` == ese empleado ⇒ conflicto de interés documental.

**`round_tripping` (endurecer)**
- Sobre los ciclos ya detectados: exigir **continuidad temporal** (fechas monótonas alrededor del ciclo)
  y **continuidad de monto** (cada tramo dentro de ±X % del anterior). Sin eso, sigue siendo estructura, no dinero.
- Resolver dueño de cada CLABE (`vendors`/`employees`) y descartar ciclos donde todos los tramos tienen
  factura + PO + contrato que los respalden.

**`revenue_inflation` (no existe nada)**
- La empresa es `receiver_rfc` en compras; para ventas infladas mirar facturas donde la empresa es **emisor**.
- Señales: facturas emitidas cerca del cierre de periodo sin cobro bancario correspondiente;
  facturas canceladas justo después del cierre; asientos de ingreso en `ledger` sin `bank_txns` de entrada;
  ventas a partes con CLABE compartida con empleados/vendors.

### 9.2 Defensa anti-decoy (pesa tanto como el recall)

Correr **antes** de gastar un token de LLM, como filtro determinista que cierra leads baratos:
- `contract_coverage(vendor)` — ¿existe contrato cuyo `value` y `start_date` expliquen la cadencia observada?
- `po_coverage(invoices)` — ¿cada factura del cluster tiene PO con monto y fecha compatibles?
- `base_rate(vendor)` — ¿estos 6 pagos "raros" son 6 de 6 o 6 de 300 idénticos? (el reto lo menciona literal).
- `bank_institution_only(clabe_a, clabe_b)` — mismos 3 primeros dígitos ≠ misma cuenta.
  El juez **va a preguntar exactamente esto**: *"What if the employee just happens to bank at the same institution?"*
- `timing_sanity` — ¿la ventana elegida es la conveniente o la natural?

Cada uno de estos que cierre un lead debe escribir automáticamente una entrada de `leads_not_pursued`
con la razón específica y los `tool_calls_made` reales tomados de `case_state.actions_taken`.

### 9.3 Priorización y control de costo

`llm_calls` y `mxn_cost` se califican. Hoy el pipeline corre el loop completo sobre **todos** los leads.

- Calcular un **`lead_priority` determinista y explicable** (no un score de IA): número de señales
  independientes (usando `find_evidence_dependencies` para no contar dos veces la misma fila),
  exposición en pesos involucrada, y si sobrevivió los filtros anti-decoy.
- Presupuesto explícito: `--max-llm-calls N`, top-K leads por prioridad, y registrar en el case file
  **qué leads no se investigaron por presupuesto** — eso es honesto y puntúa mejor que fingir cobertura total.

### 9.4 Determinismo, cache y replay (dos reglas explícitas del reto)

Un decorador sobre `LLMClient`:

```
CachedMeteredLLMClient(inner, cache_path, offline=False)
  key = sha256(model + system_prompt + user_prompt)
  hit  -> devuelve del cache, no cuenta llamada
  miss -> si offline: RuntimeError explícito; si no: llama, guarda, cuenta tokens/costo
```

Guardar `runs/<seed>/llm_cache.json` + `runs/<seed>/trace.jsonl`. Con eso se obtiene, gratis:
determinismo por seed, replay sin red, `llm_calls`, `mxn_cost`, `cost_by_role`, y el **log desde el cual
responder preguntas del juez en <10 s**.

Además: `temperature=0`, y un **repair loop** de 1 reintento cuando el JSON del LLM no valida
(hoy un JSON malo mata el caso entero).

### 9.5 El log que gana las preguntas del juez

Un `runs/<seed>/decision_log.jsonl` con una línea por decisión:
`{case_id, entity, signal, action, why, outcome, closed_by, evidence_refs}`.
El case file se genera **desde ese log**, no al revés. Así "¿por qué no marcaste al vendor X?"
se responde abriendo una página, no re-corriendo nada.

---

## 10. Layout objetivo

```
src/
  core/ detectors/ graph/ gnn/ investigation/ agents/ verifier/ gates/ rules/ output/   (ya existen)
  adapters/      # NUEVO  estate_csv.py (zip → SQLite en memoria), sqlite.py
  llm/           # NUEVO  client.py (Protocol), gemini.py, ollama.py, cache.py, metering.py
  report/        # NUEVO  case_file.py (HTML/MD), money_trail_diagram.py (Mermaid/SVG)
  main.py        # NUEVO  CLI: --estate <path> --seed N --out <dir> [--offline] [--max-llm-calls N]
dev/             # NUEVO  generate_estate.py  (genera estate.db + estate_csv.zip + answer key)
eval/            # NUEVO  harness: corre N seeds, compara vs ground truth, emite results_table.csv
                 #        ← ÚNICO lugar donde puede aparecer la palabra ground_truth
runs/<seed>/     # NUEVO  submission.json, case_file.html, llm_cache.json, trace.jsonl, decision_log.jsonl
```

`dev/` y `eval/` **fuera de `src/`** por la regla de aislamiento del ground truth.

---

## 11. Plan por fases

**Fase A — Desbloquear (sin esto no hay entregable)**
1. `dev/generate_estate.py` determinista por seed → `estate.db` + `estate_csv.zip` + `dev/answer_key_<seed>.json`.
   Esquema oficial exacto, sin columnas extra. ~40 vendors, 15 employees, 350 invoices, 700 ledger,
   350+ bank_txns, 200 POs, 20 contracts, EFOS. Los 5 esquemas + ≥4 decoys, **mezclados en memoria antes de insertar**.
2. `src/llm/` con `GeminiClient` + `OllamaClient` + `CachedMeteredLLMClient`.
3. Seedear `RuleRegistry` con reglas reales (SAT Art. 69-B, CFF Art. 29/29-A, y la política de aprobación
   interna **declarada como inferida**) y cablearlo al pipeline.
4. `src/output/amount_builder.py` — deriva `claimed_amount` por esquema desde el set exacto de exhibits (per-table).
5. `src/main.py` — CLI end-to-end que ya produce `submission.json` válido y pasa `validate_format.py`.

**Fase B — Score**
6. Detectores faltantes de §9.1 + filtros anti-decoy de §9.2.
7. Verificadores sustantivos para los 5 esquemas + generalizar `finding_builder` por esquema.
8. `money_trail` real desde `bank_txns` con validación de continuidad.
9. Generación automática de `leads_not_pursued` desde casos bloqueados/cerrados.
10. `run_metadata` instrumentado.

**Fase C — Presentación**
11. `src/report/case_file.py` con las 5 secciones y **diagrama** de money trail.
12. `eval/` + tabla de resultados sobre ≥5 seeds held-out (disjuntos de los de tuning).
13. Modo `--offline` probado con la red apagada.

**Fase D — Limpieza**
14. Borrar o mover a `legacy/` `tabulares.py`, `bayes_tabular.py`, `tabular_verifier.py`.
15. `pytest.importorskip("torch")` en los tests de GNN; añadir `__init__.py` faltantes.
16. `grep -r 'ground_truth' src/ --include='*.py'` debe salir vacío. Correrlo en CI.

---

## 12. Checklist de demo

- [ ] `python -m src.main --estate <ruta_del_juez> --seed N --out runs/N/` corre de principio a fin.
- [ ] `python official_materials/.../validate_format.py --submission runs/N/submission.json --estate <ruta>` ⇒ PASS.
- [ ] Correr el mismo seed dos veces produce bytes idénticos.
- [ ] Correr con la red apagada (`--offline`) reproduce el run.
- [ ] El case file abre en un navegador, tiene el diagrama, y las 5 secciones en orden.
- [ ] Tabla de resultados con ≥5 seeds held-out, recall **y** tasa de falsa acusación, seeds nombrados.
- [ ] Los 3 números (`llm_calls`, `mxn_cost`, `wall_clock_seconds`) visibles en el header.
- [ ] Preguntas ensayadas, respondidas **desde el log**:
      "¿por qué no marcaste a X?" · "¿qué tan confiado estás en el finding 2?" ·
      "¿y si el empleado solo tiene cuenta en el mismo banco?" · "¿qué NO puede detectar esto?" ·
      "¿qué recortaron y por qué?"
- [ ] Un finding que el sistema **decidió no emitir** — mostrarlo es más fuerte que esconderlo.

---

## 13. Frase que resume el proyecto

> "No solo encontramos cosas raras. Mostramos qué observamos, qué inferimos, qué intentamos refutar,
> y exactamente qué evidencia sobrevivió — y cuánto dinero puede reconstruir Python desde las filas citadas."
