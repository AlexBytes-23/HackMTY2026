# ANTIGRAVITY BRIEF — prompts listos para copiar y pegar

Paquete de instrucciones para construir lo que falta del Forensic Auditor con un agente programador
(Antigravity, Cursor, Claude Code, Gemini CLI). Cada tarea es **autocontenida**: incluye el contexto,
las reglas, el archivo a crear, el criterio de aceptación y el test que debe pasar.

## Cómo usarlo

1. Pega el **BLOQUE 0** al abrir la sesión. Es el contexto maestro.
2. Luego pega **una tarea a la vez**, en orden. Espera a que termine y a que corran los tests antes de la siguiente.
3. Modelo recomendado: el más capaz disponible con razonamiento extendido. `temperature` baja.
4. Si el agente te da el código en el chat en vez de escribir el archivo, dile:
   *"Aplica el código directamente en el archivo `<ruta>`. Escribe el archivo real en el proyecto, no me des un resumen."*
5. Después de cada tarea, corre siempre:
   ```bash
   pytest -q && grep -r 'ground_truth' src/ --include='*.py'
   ```
   El segundo comando **debe salir vacío**.

---

## BLOQUE 0 — Contexto maestro (pegar al inicio de cada sesión nueva)

```
Vas a trabajar en el repo HackMTY2026: un auditor forense agentic para el reto
"The Forensic Auditor" de Infosys en HackMTY 2026.

ANTES DE ESCRIBIR NADA, lee estos archivos del repo y trátalos como la fuente de verdad:
  - docs/CODEBASE_MAP.md              (arquitectura completa, inventario, gaps, plan)
  - CLAUDE.md                          (las 12 reglas invariantes)
  - official_materials/student-materials/forensic-auditor/estate_schema.sql
  - official_materials/student-materials/forensic-auditor/submission_schema.json
  - official_materials/student-materials/forensic-auditor/case_file_structure.md
  - official_materials/student-materials/forensic-auditor/validate_format.py

QUÉ ES EL SISTEMA
Recibe el estate contable de una empresa (8 tablas SQLite o CSV, esquema oficial inmutable:
vendors, invoices, ledger, bank_txns, purchase_orders, contracts, employees, efos_list),
investiga, sigue el dinero y entrega un expediente defendible sin acusar a quien no puede respaldar.
Los jueces lo corren sobre estates nunca vistos con hasta 5 esquemas de fraude y 10 decoys,
posiblemente entrelazados (dos esquemas compartiendo una entidad).

Los 5 esquemas: phantom_vendor, kickback, round_tripping, threshold_splitting, revenue_inflation.

ARQUITECTURA (ya construida, respétala)
  estate -> detectores deterministas -> Observations -> Leads -> CaseState
         -> Investigator (LLM, Action Bank) -> Challenger (LLM) -> Method Critic (LLM)
         -> OfficialVerifier (Python) -> Evidence Gate -> FindingBuilder -> submission.json

REGLAS QUE NO SE ROMPEN
 1. Nada en src/ puede leer ground truth ni answer keys. Los jueces corren
    `grep -r 'ground_truth' src/ --include='*.py'`. Si aparece, la calificación de Results
    queda topada en 2. El answer key vive en dev/ y solo lo lee eval/.
 2. La aritmética es de Python, nunca del LLM.
 3. Ningún finding sin autorización del Evidence Gate.
 4. Nada llega al Verifier sin pasar por Challenger + Method Critic.
 5. El LLM solo puede pedir acciones existentes en el Action Bank.
 6. Prohibido inventar leyes, umbrales o políticas. Solo se usan las del RuleRegistry.
 7. "No se encontró registro en el estate suministrado" != "no existe".
 8. Toda afirmación cita source_table + record_id que existen de verdad.
 9. Un score siempre viaja con su score_semantics. Nunca es una probabilidad de fraude.
10. Sin rutas hardcodeadas: el estate entra por parámetro en runtime.
11. El esquema del estate es inmutable. Nada de columnas is_fraud o scheme_type en la DB.
12. Determinismo: mismo seed => mismo output byte a byte. Orden explícito en todo iterable.

REGLAS DE SALIDA QUE EL VALIDADOR OFICIAL CASTIGA
  - peso_amount reconcilia POR TABLA con tolerancia 2% sobre max(mejor_total, 1).
    Citar la factura Y la transferencia que la liquidó NO es dinero doble.
  - minimo 3 exhibits por finding, exhibit_id unicos, record_id existente en el estate.
  - narrative <= 150 palabras, lenguaje llano para un juez no tecnico.
  - rule_broken = articulo o regla concreta (ej. "SAT Articulo 69-B"), no "patron estadistico".
  - entities con prefijo RFC: o EMP:. El prefijo CLABE: es interno y nunca sale en un finding.
  - money_trail presente y continuo: paso[i].to == paso[i+1].from, cada paso cita un exhibit del finding.
  - run_metadata con llm_calls, mxn_cost, wall_clock_seconds reales.
  - leads_not_pursued con razon ESPECIFICA que nombre la evidencia revisada, mas tool_calls_made.

ESTILO
  - Pydantic para todo contrato entre capas.
  - Todo detector nuevo declara limitations, legitimate_alternatives y recommended_checks.
  - Un test por modulo en tests/test_<modulo>.py. Nada se mergea sin test.
  - No refactorices modulos que no te pedi tocar.
  - Escribe los archivos reales en el proyecto. No me des solo un resumen en el chat.

Confirma que leiste los archivos y dime en 5 lineas que entendiste antes de codear.
```

---

# FASE A — Desbloquear el entregable

## TAREA A1 — Generador de estate de desarrollo

```
TAREA: crear dev/generate_estate.py

Contexto: no tenemos datos. data/ esta vacio. Sin un estate sintetico no podemos medir
recall ni falsas acusaciones, que son la mitad de la calificacion.

REQUISITOS

CLI:
  python dev/generate_estate.py --seed 42 --out-db data/estate_42.db \
      --out-zip data/estate_42_csv.zip --out-key dev/answer_key_42.json

Determinismo estricto: random.seed(seed) y, si usas Faker, Faker.seed(seed).
Mismo seed => mismos bytes en la DB, en el zip y en el answer key.

Esquema: EXACTAMENTE el de official_materials/.../estate_schema.sql. Ocho tablas, mismos
nombres de tabla y columna, nada extra. PROHIBIDO cualquier columna tipo is_fraud,
scheme_type, ground_truth o similar dentro del estate.

Formatos:
  - fechas ISO 8601 (YYYY-MM-DD)
  - CLABE: texto de exactamente 18 digitos
  - RFC: 12 caracteres (moral) o 13 (fisica), sinteticos pero con forma mexicana valida
  - invoices: round(subtotal*0.16, 2) == iva  y  subtotal + iva == total al centavo
  - uso_cfdi / forma_pago / metodo_pago: codigos del catalogo SAT (G03, 03, PUE/PPD)
  - status de invoices: vigente | cancelado
  - efos_list.status: definitivo | presunto

Volumen base (una empresa coherente, mayoritariamente NORMAL):
  40 vendors, 15 employees, ~350 invoices, ~700 ledger (partida doble real por factura,
  vinculada por invoice_uuid, debit total == credit total), ~350 bank_txns,
  ~200 purchase_orders, ~20 contracts, varios registros efos_list.

Integridad referencial:
  - invoices.issuer_rfc existe en vendors
  - bank_txns.to_clabe == vendors.bank_clabe del emisor de la factura pagada
  - bank_txns.amount == invoices.total de la factura que liquida
  - purchase_orders.vendor_rfc y contracts.vendor_rfc existen en vendors
  - employees tienen CLABE propia y distinta de las de vendors

ESQUEMAS A PLANTAR (mezclados en memoria ANTES de insertar en SQLite):
  phantom_vendor      vendor en efos_list, con facturas pagadas, SIN purchase_orders ni contracts.
                      La publication_date del EFOS debe ser ANTERIOR a las facturas.
  kickback            pago legitimo empresa -> vendor y, 0-2 dias despues, transferencia
                      vendor -> CLABE de un empleado por un porcentaje estable del pago.
                      Ese mismo empleado aparece como approver en los purchase_orders del vendor.
  round_tripping      ciclo de 3-4 CLABEs con montos decrecientes coherentes y fechas monotonas,
                      donde el dinero vuelve a la cuenta de origen.
  threshold_splitting 6 facturas del mismo vendor en una semana, todas justo por debajo de un
                      umbral de aprobacion implicito. El umbral se infiere porque los POs por
                      encima de ese monto los aprueba un rol senior y los de abajo un rol junior.
                      Genera esa evidencia de aprobacion en purchase_orders.approver.
  revenue_inflation   facturas donde la empresa es el EMISOR, emitidas cerca del cierre de periodo,
                      con asiento de ingreso en ledger pero SIN cobro en bank_txns; varias
                      canceladas poco despues del cierre.

DECOYS (minimo 6, son igual de importantes que los fraudes):
  - 12 pagos mensuales identicos respaldados por un contract de cuota fija que cuadra en monto y fechas
  - proveedor nuevo y honesto, alto volumen, con PO y contrato completos
  - empleado y vendor cuyas CLABEs comparten los 3 primeros digitos (mismo banco) pero son
    cuentas distintas y no hay ninguna transferencia entre ellos
  - ciclo de transferencias con soporte comercial completo en cada tramo
  - dos facturas casi identicas por entregas escalonadas reales con PO separados
  - un pago duplicado que es un error administrativo con su nota de correccion en ledger.description

ANSWER KEY (fuera de la DB, en dev/):
  Formato exacto de official_materials/.../ground_truth_schema.json:
  { seed, company_rfc, schemes: [{scheme_id, type, entities, supporting_invoices,
    supporting_txns, peso_amount, difficulty}], decoys: [{entity, signal, why_innocent, invoices}] }
  entities con prefijo RFC:/EMP:. difficulty en easy|medium|hard.
  El peso_amount del answer key debe ser el que reconcilia POR TABLA con los exhibits
  que un auditor honesto citaria.

CONFIGURACION:
  generate_estate(seed, scheme_types=[...], decoy_count=N) -> dict en memoria,
  y funciones separadas export_to_sqlite(data, path) y export_to_csv_zip(data, path).
  El zip lleva 8 CSV UTF-8 con header identico al esquema.

AL FINAL: imprime SELECT COUNT(*) de las 8 tablas.

ACEPTACION:
  - correr dos veces con --seed 42 produce archivos identicos (comparar sha256)
  - sqlite3 data/estate_42.db ".schema" no muestra ninguna columna fuera del esquema oficial
  - los CSV del zip tienen exactamente los headers de estate_schema.sql
  - dev/answer_key_42.json valida contra ground_truth_schema.json

TESTS: crea tests/test_generate_estate.py con:
  - determinismo (mismo seed, mismo sha256 de la DB)
  - esquema exacto (compara contra src.core.estate.OFFICIAL_COLUMNS)
  - aritmetica del IVA al centavo en todas las facturas
  - partida doble balanceada por invoice_uuid
  - EstateRepository(path) construye sin error
  - el answer key NO esta dentro de la DB
```

## TAREA A2 — Cliente LLM con cache, metering y modo offline

```
TAREA: crear el paquete src/llm/

Contexto: src/investigation/investigator.py define LLMClient como un Protocol con
complete(system_prompt, user_prompt) -> str. Nadie lo implementa todavia.
El reto exige tres cosas que este paquete resuelve de golpe:
determinismo por seed, replay sin red, y los numeros llm_calls / mxn_cost / wall_clock_seconds.

ARCHIVOS

src/llm/client.py
  - Protocol LLMClient (re-export del contrato, para no importar desde investigation)
  - dataclass LLMCallRecord: role, model, prompt_sha256, prompt_tokens, completion_tokens,
    latency_ms, cached: bool, mxn_cost
  - excepcion LLMOfflineCacheMiss

src/llm/gemini.py
  - GeminiClient(api_key, model="gemini-2.5-flash", temperature=0.0)
  - lee la key de la variable de entorno GEMINI_API_KEY; NUNCA la imprime ni la loguea
  - response_mime_type="application/json"
  - reintentos con backoff solo para 429/5xx, maximo 3
  - si la libreria google-genai no esta instalada, lanza un error claro al construir, no al importar

src/llm/ollama.py
  - OllamaClient(model="llama3.1", host="http://localhost:11434", temperature=0.0)
  - format="json", sin dependencias extra (usa urllib de stdlib)

src/llm/cache.py
  - CachedMeteredLLMClient(inner, cache_path, role="unknown", offline=False,
                           price_in_mxn_per_1k, price_out_mxn_per_1k)
  - key = sha256(model + "\x00" + system_prompt + "\x00" + user_prompt)
  - hit: devuelve del cache, cached=True, NO incrementa llm_calls ni costo
  - miss con offline=True: lanza LLMOfflineCacheMiss con la key, nunca sale a la red
  - miss normal: llama, guarda en cache, registra LLMCallRecord
  - el cache se persiste como JSON ordenado por key (determinista) con escritura atomica
  - propiedades: call_count, total_mxn_cost, cost_by_role, records

src/llm/repair.py
  - repairing_complete(client, system_prompt, user_prompt, validator, max_retries=1)
  - si validator(texto) lanza, reintenta UNA vez agregando al user_prompt el error exacto
    y "Devuelve UNICAMENTE JSON valido que cumpla el esquema"
  - si vuelve a fallar, propaga. Hoy un JSON malo mata el caso entero; esto lo arregla.

REGLAS
  - nada de estado global, nada de singletons
  - los precios entran por parametro, no hardcodeados en la logica
  - cero dependencias nuevas obligatorias en requirements.txt
    (google-genai va en un requirements-llm.txt aparte)

TESTS: tests/test_llm_cache.py con un FakeLLMClient que cuenta invocaciones:
  - mismo prompt dos veces => 1 sola llamada al inner, la segunda cached=True
  - offline=True con cache vacio => LLMOfflineCacheMiss
  - offline=True con cache poblado => reproduce sin tocar el inner
  - call_count y total_mxn_cost correctos
  - el archivo de cache es byte-identico al regenerarlo con las mismas entradas
  - repairing_complete reintenta exactamente una vez y despues propaga
```

## TAREA A3 — Reglas legales reales en el RuleRegistry

```
TAREA: crear src/rules/official_rules.py y cablearlo

Contexto: src/rules/rule_registry.py existe pero esta VACIO en runtime. Por eso
src/gates/evidence_gate.py siempre falla con "requested rule_id is missing or not valid"
y el sistema no puede emitir ni un solo finding. rule_broken es campo obligatorio del
submission oficial y debe ser un articulo concreto, no una descripcion estadistica.

CREAR build_official_rule_registry() -> RuleRegistry con al menos estas reglas,
cada una como RuleDefinition(rule_id, title, source, source_reference, applies_to,
supports, does_not_prove, requirements, exceptions):

  RULE-SAT-69B
    source "SAT", source_reference "Codigo Fiscal de la Federacion, Articulo 69-B"
    applies_to ["phantom_vendor"]
    supports: el RFC fue publicado en el listado de contribuyentes que emiten comprobantes
              sin contar con activos, personal o infraestructura
    does_not_prove: que una operacion concreta sea simulada; que toda factura del emisor
              sea falsa; el estado definitivo si la publicacion es solo "presunto"
    requirements: la factura debe ser posterior a publication_date; registrar el status exacto
    exceptions: "desvirtuado" o sentencia favorable; operaciones anteriores a la publicacion

  RULE-CFF-29A
    source_reference "Codigo Fiscal de la Federacion, Articulos 29 y 29-A"
    applies_to ["phantom_vendor", "revenue_inflation"]
    sobre requisitos de los comprobantes fiscales y la correspondencia con la operacion real

  RULE-LFPIORPI-MATERIALIDAD
    applies_to ["phantom_vendor", "kickback"]
    sobre materialidad de la operacion: debe existir soporte documental (PO, contrato, entregable)

  RULE-INTERNAL-APPROVAL-THRESHOLD
    source "Internal control policy (INFERRED FROM ESTATE)"
    applies_to ["threshold_splitting"]
    IMPORTANTE: esta regla debe declarar explicitamente en does_not_prove y en requirements
    que el umbral NO viene dado por el reto y fue inferido del cambio de aprobador observado
    en purchase_orders.approver. No inventamos una ley: describimos una politica inferida
    y lo decimos en voz alta.

  RULE-CONFLICT-OF-INTEREST
    applies_to ["kickback"]
    sobre el beneficio economico a un empleado que participa en la aprobacion del gasto

  RULE-ACCOUNTING-CIRCULARITY
    applies_to ["round_tripping"]
    sobre operaciones circulares sin sustancia economica

  RULE-REVENUE-RECOGNITION
    applies_to ["revenue_inflation"]
    sobre ingresos reconocidos sin cobro ni entregable correspondiente

REGLAS
  - cada rule_id aparece en applies_to solo para los esquemas donde realmente aplica
    (el Evidence Gate lo verifica y debe poder rechazar un mal emparejamiento)
  - does_not_prove nunca puede estar vacio
  - no inventes numeros de articulo que no conozcas: si dudas, usa el nombre del ordenamiento
    sin numero y dilo en el titulo

INTEGRAR: que src/investigation/postverification_pipeline.py y el futuro src/main.py
reciban el registry ya construido. No cambies la firma publica de evaluate_gate.

TESTS: tests/test_official_rules.py
  - todas las reglas construyen y tienen does_not_prove no vacio
  - cada uno de los 5 scheme_type tiene al menos una regla aplicable
  - evaluate_gate rechaza una regla cuyo applies_to no incluye el scheme_type del target
```

## TAREA A4 — Calculo determinista del monto reclamado

```
TAREA: crear src/output/amount_builder.py

Contexto: OfficialVerifier y FindingBuilder reciben claimed_amount como entrada externa y
deliberadamente NO lo infieren (eso es correcto: separacion de responsabilidades).
Pero hoy NADIE lo calcula, asi que el pipeline no puede emitir findings.
Este modulo es la pieza que falta: deriva el monto desde el set exacto de exhibits,
de forma determinista y usando la misma regla per-table del validador oficial.

API
  build_claimed_amount(estate, exhibits: Sequence[EvidenceRef], scheme_type) -> AmountProposal

AmountProposal (pydantic):
  amount: float
  basis_table: str            # la tabla cuyo total se reclama
  contributing_records: list[str]
  per_table_sums: dict[str, float]
  calculation: str            # aritmetica legible: "invoices: INV-001 400.00 + INV-002 600.00 = 1000.00"
  warnings: list[str]

REGLAS
  - suma POR TABLA usando estate.get_record_amount (tablas con monto: invoices, bank_txns,
    purchase_orders, contracts)
  - elige la tabla base segun el esquema:
      phantom_vendor      -> invoices (lo facturado sin soporte)
      kickback            -> bank_txns, y SOLO las transferencias del tramo del kickback
      round_tripping      -> bank_txns, el monto del tramo mas pequeño del ciclo
                             (es el maximo que realmente puede haber circulado)
      threshold_splitting -> invoices del cluster
      revenue_inflation   -> invoices emitidas por la empresa sin cobro
  - si dos tablas dan totales que difieren mas del 2%, agrega un warning explicito
  - NUNCA inventar un monto si ningun exhibit cita una tabla con monto: devolver
    amount = 0.0 y un warning, para que el verificador falle cerrado
  - ordenar contributing_records de forma estable

El campo calculation se usa tal cual en la seccion "Reconciliation" del case file,
que es requisito del formato oficial.

TESTS: tests/test_amount_builder.py
  - el monto reconcilia con src.output.reconciliation.reconcile_pesos al 2%
  - citar factura + transferencia del mismo dinero no duplica el monto
  - exhibits sin tabla de monto => amount 0 con warning
  - determinismo: mismo input, mismo output
```

## TAREA A5 — CLI end-to-end

```
TAREA: crear src/main.py y src/adapters/estate_csv.py

Contexto: existe todo el pipeline por piezas (estate_pipeline -> preverification ->
postverification -> finding_builder -> submission_builder) pero NADA los une.
No hay forma de correr el sistema. Los jueces nos van a dar una ruta de estate en runtime.

src/adapters/estate_csv.py
  load_estate(path: str|Path) -> EstateRepository
  - si termina en .db o .sqlite: EstateRepository directo
  - si termina en .zip: descomprime los 8 CSV a una DB SQLite temporal con el esquema oficial
    (usa src.core.estate.OFFICIAL_COLUMNS para crear las tablas) y devuelve el repo
  - si es un directorio con los 8 .csv: igual
  - tipos: montos a REAL, el resto TEXT. CLABE y RFC SIEMPRE como texto, nunca numerico.
  - error claro si falta una tabla o una columna

src/main.py  (argparse, sin rutas hardcodeadas)
  python -m src.main --estate <ruta> --seed N --out runs/N/
      [--offline] [--max-llm-calls N] [--max-cases N] [--provider gemini|ollama|none]
      [--enable-gnn]

FLUJO
  1. load_estate(--estate)
  2. construir el LLMClient: provider -> CachedMeteredLLMClient(cache=runs/N/llm_cache.json,
     offline=--offline). Con --provider none usar un cliente que solo lee cache
     (util para replay puro).
  3. run_estate_preverification(...)
  4. para cada caso ready_for_verification:
       - build_claimed_amount(...)
       - seleccionar rule_id: la regla del registry cuyo applies_to incluye el scheme_type
         (si hay varias, la mas especifica; deja la eleccion en una funcion separada y testeable)
       - run_postverification_pipeline(...)
       - si gate autoriza: build_finding(...) y agregar a findings
       - si NO autoriza: generar una entrada de leads_not_pursued (ver TAREA B4)
  5. para cada caso blocked o error: tambien generar leads_not_pursued
  6. build_submission(findings, leads, run_metadata, seed) y write_submission_json a
     runs/N/submission.json
  7. escribir runs/N/decision_log.jsonl y runs/N/trace.jsonl

run_metadata REAL:
  llm_calls = client.call_count, mxn_cost = client.total_mxn_cost,
  wall_clock_seconds medido con perf_counter, cost_by_role = client.cost_by_role,
  deterministic = True si el run fue 100% cache hits o si offline=True

SALIDA EN CONSOLA: findings emitidos, leads cerrados, los 3 numeros, y la ruta del submission.

ACEPTACION
  python -m src.main --estate data/estate_42.db --seed 42 --out runs/42/
  python official_materials/student-materials/forensic-auditor/validate_format.py \
      --submission runs/42/submission.json --estate data/estate_42.db
  => PASS

  Correr dos veces el mismo seed produce submission.json identico (sha256).
  La segunda corrida con --offline funciona sin red.

TESTS: tests/test_main_cli.py
  - end-to-end con un estate minimo y un LLMClient falso determinista
  - carga desde .db, desde .zip y desde directorio producen el mismo repo
  - --offline sin cache falla con un mensaje claro, no con un stacktrace opaco
```

---

# FASE B — Subir el score

## TAREA B1 — Detectores deterministas faltantes

```
TAREA: crear src/detectors/deterministic_documentary.py y ampliar deterministic_relational.py

Contexto: hoy hay 6 detectores. Cubren parcialmente phantom_vendor, kickback,
round_tripping y threshold_splitting, y NADA de revenue_inflation. Tampoco se usa
ledger, purchase_orders ni contracts como fuente primaria de señal.

Cada detector nuevo devuelve list[Observation] (src.core.models) y DEBE declarar
limitations, legitimate_alternatives y recommended_checks. Ninguno afirma fraude.
IDs deterministas con el mismo patron SHA-1 truncado que ya usan los otros modulos.

NUEVO src/detectors/deterministic_documentary.py
  detect_paid_invoices_without_documentary_support(estate)
      facturas con pago bancario asociado pero sin purchase_orders ni contracts del emisor
  detect_efos_match_after_publication(estate)
      facturas cuyo issue_date es POSTERIOR a efos_list.publication_date; separa status
      definitivo de presunto. Esta es la señal fuerte de phantom_vendor, mucho mas
      defendible que "aparece en la lista".
  detect_vendor_registered_after_first_invoice(estate)
      vendors.registered_date posterior o a menos de N dias de su primera factura
  detect_generic_or_malformed_rfc(estate)
      XAXX010101000 y RFCs que no cumplen longitud 12/13
  detect_invoice_ledger_mismatch(estate)
      factura sin asiento en ledger; asiento sin factura; debit != credit por invoice_uuid
  detect_invoice_total_arithmetic_error(estate)
      total != subtotal + iva mas alla de un centavo
  detect_cancelled_invoice_paid(estate)
      invoices.status == 'cancelado' con bank_txn asociado
  detect_payment_before_invoice(estate)
      bank_txn.date anterior a invoices.issue_date de la factura que dice liquidar
  detect_duplicate_payments(estate)
      mismo to_clabe y mismo amount dentro de una ventana corta

AMPLIAR src/detectors/deterministic_relational.py
  detect_payment_then_employee_transfer(estate, max_days=3, min_ratio=0.02, max_ratio=0.40)
      cadena completa del kickback: pago empresa->vendor seguido de vendor->empleado.
      Guarda en facts: txn_in, txn_out, ratio, lag_days.
      Hoy solo detectamos la segunda pata; esta es la señal que realmente sostiene el caso.
  detect_approval_threshold_change(estate)
      infiere el umbral de aprobacion buscando el monto donde cambia el approver en
      purchase_orders. Devuelve el umbral inferido, el aprobador de abajo, el de arriba y
      el soporte muestral. limitations DEBE decir que es un umbral inferido, no una politica dada.
  detect_split_below_inferred_threshold(estate)
      usa el umbral inferido: clusters del mismo vendor en ventana corta, todos por debajo,
      cuya suma lo supera.
  detect_cycle_with_amount_and_time_continuity(estate, tolerance=0.15)
      sobre los ciclos que ya encuentra bank_graph: exige fechas monotonas y montos de cada
      tramo dentro de la tolerancia respecto al anterior. Solo estos son candidatos serios
      de round_tripping.

NUEVO src/detectors/deterministic_revenue.py
  detect_issued_invoices_without_collection(estate, company_rfc)
      facturas donde la empresa es issuer_rfc, con asiento de ingreso en ledger,
      sin bank_txn de entrada correspondiente
  detect_period_end_issuance_spike(estate, company_rfc)
      concentracion anomala de emision en los ultimos dias del periodo comparada con la
      mediana del resto del periodo (estadistica robusta, mediana/MAD, sin sklearn)
  detect_cancelled_after_period_end(estate, company_rfc)
      facturas emitidas antes del cierre y canceladas poco despues

  company_rfc NO se hardcodea: se infiere como el receiver_rfc mas frecuente en invoices,
  y esa inferencia se declara como limitation.

INTEGRAR: llamar los tres modulos desde src/investigation/estate_pipeline.py.

TESTS: un archivo por modulo, con fixtures SQLite minimas, casos positivos y
casos negativos explicitos (el decoy que NO debe disparar).
```

## TAREA B2 — Filtros anti-decoy deterministas

```
TAREA: crear src/detectors/decoy_filters.py

Contexto: el reto dice literal que las falsas acusaciones contra decoys pesan al menos
tanto como el recall, y que un sistema que acusa a todos saca mala nota. Ademas cada
lead que matemos aqui es LLM que no gastamos (llm_calls se califica).

Estos filtros corren DESPUES de los detectores y ANTES del Investigator.
No borran leads: producen un DecoyAssessment que baja prioridad y, si es concluyente,
cierra el lead con una razon especifica.

API
  assess_lead(estate, lead, observations) -> DecoyAssessment

DecoyAssessment (pydantic):
  lead_id, checks: list[DecoyCheck], recommend_close: bool, close_reason: str|None,
  priority_penalty: float

DecoyCheck: check_id, statement, result Literal["explains","does_not_explain","inconclusive"],
            evidence: list[EvidenceRef], detail: str

CHECKS
  CONTRACT-COVERAGE
     existe contract del vendor cuyo value y start_date explican la cadencia y el monto
     de los pagos repetidos. Si el contrato cubre exactamente el patron: explains.
  PO-COVERAGE
     cada factura del cluster tiene un purchase_order compatible en monto (±2%) y fecha.
  BASE-RATE
     el patron señalado es N de N o N de M? Si el vendor tiene 300 pagos identicos y
     señalamos 6, la interpretacion cambia. Devuelve la proporcion en detail.
  BANK-INSTITUTION-ONLY
     dos CLABEs comparten los 3 primeros digitos (misma institucion) pero son cuentas
     distintas y NO hay ninguna transferencia entre ellas.
     El juez pregunta exactamente esto: "What if the employee just happens to bank at
     the same institution?" Debe haber una respuesta en el log.
  EFOS-TIMING
     todas las facturas del vendor son ANTERIORES a la publication_date del EFOS,
     o el status es "presunto" sin definitivo.
  LEDGER-CORRECTION
     el pago duplicado tiene un asiento de correccion o reverso en ledger.
  CYCLE-DOCUMENTED
     cada tramo del ciclo tiene factura + PO + contrato que lo respalda.

REGLAS
  - recommend_close SOLO si al menos un check da "explains" y ninguno da "does_not_explain"
  - close_reason debe nombrar el registro concreto que lo cierra:
    "Los 12 pagos de $44,080.00 estan cubiertos por el contrato CTR-0021 (valor anual
     $528,960.00, inicio 2025-01-01); fechas y montos coinciden con una cuota mensual fija."
    NUNCA "insufficient evidence".
  - todo es determinista, sin LLM

TESTS: tests/test_decoy_filters.py
  - cada decoy de la TAREA A1 dispara su check y produce recommend_close=True
  - cada esquema real de la TAREA A1 NO produce recommend_close=True
  - close_reason siempre cita al menos un record_id
```

## TAREA B3 — Verificadores sustantivos para los 5 esquemas

```
TAREA: ampliar src/verifier/official_verifier.py

Contexto: hoy solo phantom_vendor tiene verificacion sustantiva
(PHANTOM-EFOS-INVOICE-LINK). Para los otros 4 esquemas el verificador emite un check
"unresolved" y el Evidence Gate bloquea. Resultado: el sistema NO PUEDE emitir findings
de 4 de los 5 tipos. Esto topa el recall.

Refactoriza a un registro de verificadores por esquema, manteniendo la API publica
OfficialVerifier().verify(case_state, target_hypothesis_id, estate, claimed_amount).

SCHEME_VERIFIERS: dict[SchemeType, Callable[..., VerificationCheck]]

Cada verificador es 100% determinista, lee del estate y devuelve un VerificationCheck
critico con status verified | failed | unresolved y un campo calculation legible.

  PHANTOM-EFOS-INVOICE-LINK          (ya existe; ENDURECER)
      ademas de la interseccion de RFC, exigir que al menos una factura citada tenga
      issue_date POSTERIOR a efos_list.publication_date. Si no, status unresolved con
      la razon explicita.

  KICKBACK-PAYMENT-CHAIN
      verified si entre los exhibits hay: bank_txn empresa->CLABE de vendor,
      bank_txn CLABE de vendor->CLABE de empleado, fechas ordenadas, lag <= N dias,
      y el empleado existe en employees. calculation debe mostrar montos, ratio y lag.

  ROUNDTRIP-CYCLE-CONTINUITY
      verified si los bank_txns citados forman un ciclo dirigido cerrado, con fechas
      monotonas no decrecientes y cada monto dentro de ±15% del tramo anterior.
      Si solo hay ciclo estructural sin continuidad: unresolved, nunca verified.

  SPLIT-BELOW-INFERRED-THRESHOLD
      verified si las facturas citadas son del mismo issuer, caen en una ventana <= N dias,
      todas por debajo del umbral inferido, su suma lo supera, y existe evidencia del
      cambio de aprobador. calculation DEBE decir que el umbral es inferido del estate.

  REVENUE-NO-COLLECTION
      verified si las facturas citadas tienen a la empresa como issuer_rfc, tienen asiento
      de ingreso en ledger, y NO existe bank_txn de entrada que las liquide.

REGLAS
  - ningun verificador puede devolver verified basandose en ausencia de datos como prueba
    positiva, salvo REVENUE-NO-COLLECTION, donde la ausencia de cobro ES el hecho verificado;
    ahi el calculation debe decir explicitamente "no se encontro bank_txn de entrada en el
    estate suministrado".
  - si el scheme_type no tiene verificador: el comportamiento actual (unresolved) se mantiene.

AMPLIAR src/gates/evidence_gate.py para exigir el check sustantivo correspondiente
al scheme_type, en vez del hardcode a PHANTOM-EFOS-INVOICE-LINK.

TESTS: tests/test_official_verifier_schemes.py, un caso verified y un caso failed/unresolved
por esquema, mas un test que confirma que el gate sigue bloqueando esquemas sin verificador.
```

## TAREA B4 — Money trail, leads_not_pursued y FindingBuilder generico

```
TAREA: ampliar src/output/finding_builder.py y crear src/output/money_trail.py
       y src/output/leads_builder.py

Contexto: tres huecos que cuestan puntos directos.
  - finding_builder solo soporta phantom_vendor y siempre emite money_trail=[]
  - el case file exige el money trail como DIAGRAMA; prosa sola topa Clarity en 3
  - leads_not_pursued esta vacio y el juez GARANTIZADO va a preguntar por una entidad de ahi

src/output/money_trail.py
  build_money_trail(estate, exhibits, scheme_type) -> list[MoneyTrailStep]
  - construye la cadena desde los bank_txns citados
  - resuelve cada CLABE a su dueño: vendors.rfc -> "RFC:...", employees.emp_id -> "EMP:...",
    y si no hay dueño conocido, "CLABE:<clabe>" SOLO dentro del money trail
    (from/to son texto libre en el schema oficial; entities NO lo es)
  - ordena por fecha y garantiza continuidad: paso[i].to == paso[i+1].from
  - cada paso cita el exhibit_id del bank_txn correspondiente
  - si no se puede construir una cadena continua, devuelve [] en vez de inventar pasos

src/output/leads_builder.py
  build_leads_not_pursued(case_outcomes, decoy_assessments, post_results) -> list[LeadNotPursued]
  Para cada caso que NO produjo finding, una entrada con:
    entity     la entidad principal del lead, con prefijo oficial
    signal     el signal_type del detector que abrio el lead (nombre real, no generico)
    reason     razon ESPECIFICA construida desde lo que realmente paso:
               - si lo cerro un decoy filter: su close_reason (que ya cita records)
               - si lo cerro el Challenger: strongest_legitimate_alternative + que se verifico
               - si lo cerro el Method Critic: el material_blocker concreto
               - si lo bloqueo el Verifier: que check fallo y por que
               - si fue presupuesto: decirlo honestamente, "no investigado por limite de
                 llamadas; señal registrada"
    tool_calls_made  [r.action_name for r in case_state.actions_taken]  (esto distingue un
                     lead investigado de uno saltado, y el reto lo dice explicitamente)
    closed_by  investigator | challenger | validator
  PROHIBIDO emitir "insufficient evidence" como reason.

src/output/finding_builder.py
  - generalizar: SCHEME_BUILDERS por scheme_type en vez del hardcode a phantom_vendor
  - cada builder produce entities, narrative y rule_broken propios del esquema
  - entities: tomarlas de VerificationCheck.evidence del check sustantivo, no reconstruirlas
    desde el estate (paga la deuda tecnica marcada en el codigo actual)
  - narrative: lenguaje llano, <=150 palabras, que cuente QUE PASO y CUANTO,
    sin afirmar intencion criminal. Un juez no tecnico debe seguirla sin ayuda.
  - money_trail: build_money_trail(...)
  - confidence: "proven" solo si TODOS los checks criticos son verified, hay >=3 exhibits
    de al menos 2 tablas distintas, y el money trail es continuo. Si no, "probable".
    Documenta este criterio en el docstring: es la definicion operativa del equipo.

TESTS
  - money trail continuo y validado por SubmissionFinding (el modelo ya valida continuidad)
  - un finding por cada uno de los 5 esquemas pasa validate_format.py
  - ningun leads_not_pursued.reason contiene "insufficient evidence"
  - todo reason cita al menos un record_id o un nombre de check
```

---

# FASE C — Lo que el juez ve

## TAREA C1 — Case file con diagrama

```
TAREA: crear src/report/case_file.py

Contexto: el case file es EL artefacto que el juez lee. Su estructura esta fijada en
official_materials/.../case_file_structure.md y se califica que un lector NO TECNICO
pueda seguirlo solo. El money trail DEBE renderizarse como diagrama: prosa sola topa
Clarity en 3. Debe generarse SIN RED.

API
  render_case_file(submission, estate, run_context) -> str   # HTML autocontenido
  write_case_file(html, path)

SECCIONES, EN ESTE ORDEN EXACTO
  1. Header: nombre de la empresa (el receiver_rfc mas frecuente, con su legal_name si
     esta en vendors), periodo de auditoria (min/max de fechas del estate), seed,
     llm_calls, costo MXN, wall-clock en segundos, y si el run es determinista.
  2. Executive summary: parrafo llano + tabla
       Findings (conteo con niveles de confianza) | Total exposure (pesos) |
       Leads investigated and closed (conteo)
  3. Una seccion por finding, en este orden interno:
       - Heading: nombre y id de la entidad + tipo de esquema
       - Rule broken: el articulo concreto
       - Amount and confidence
       - What happened: la narrative (<150 palabras)
       - Money trail: DIAGRAMA (ver abajo)
       - Exhibits: tabla exhibit_id | source_table | record_id | note
       - Reconciliation: la aritmetica de AmountProposal.calculation
       - Adversarial review: que argumento el Challenger y por que el finding sobrevivio
         (el reto dice: "a finding that no one tried to break is weaker than one that was
         attacked and held")
  4. Leads not pursued: EN EL CUERPO, no en apendice. Tabla o tarjetas con
     entity | signal | reason | tools called | closed by.
     Debe poder leerse la respuesta en menos de 10 segundos.
  5. Method and limits: arquitectura en pocas frases, que quedo fuera de alcance,
     QUE NO PUEDE DETECTAR el sistema, y como regenerar el archivo.

DIAGRAMA DEL MONEY TRAIL
  SVG inline generado en Python, sin librerias externas ni CDN:
  cajas por entidad, flechas etiquetadas con monto y fecha, y el exhibit_id bajo cada flecha.
  Layout horizontal, con wrap si hay mas de 4 pasos.
  Alternativa aceptable: bloque <pre class="mermaid"> ADEMAS del SVG, nunca en lugar de el
  (mermaid necesita JS y puede no renderizar offline).

REGLAS
  - CSS inline, cero recursos externos, cero fetch. El archivo debe abrir con la red apagada.
  - legible en impresion y en pantalla
  - si findings esta vacio: decirlo claramente y explicar que se investigo y por que se cerro.
    "No encontramos nada" con evidencia es un resultado legitimo y se califica mejor que inventar.

INTEGRAR en src/main.py: escribir runs/<seed>/case_file.html siempre.

TESTS: tests/test_case_file.py
  - las 5 secciones estan presentes y en orden
  - hay un <svg> por cada finding con money trail
  - no hay ningun http:// o https:// en el HTML generado
  - un submission sin findings genera un case file valido
```

## TAREA C2 — Harness de evaluacion

```
TAREA: crear eval/run_evaluation.py y eval/metrics.py

ATENCION MAXIMA A LA REGLA DE AISLAMIENTO:
este es el UNICO lugar del proyecto donde puede aparecer la palabra ground_truth o
answer_key. Nada bajo src/ puede importar de eval/. Nada de eval/ puede ser importado
por src/. Los jueces corren `grep -r 'ground_truth' src/ --include='*.py'` y si aparece,
Results queda topado en 2.

eval/metrics.py
  score_submission(submission_dict, answer_key_dict) -> EvaluationResult
    schemes_planted, schemes_found, recall_pct
    decoys_planted, decoys_accused, false_accusation_rate_pct
    peso_claimed, peso_actual, peso_reconciles
    matched: list[(scheme_id, finding_index)]
    missed: list[scheme_id]
    false_accusations: list[entity]

  Criterio de match (declararlo en el docstring, es una decision del equipo):
    un finding cuenta como encontrado si scheme_type coincide Y al menos una entidad
    del finding esta en las entities del scheme del answer key.
  Criterio de falsa acusacion:
    un finding cuya entidad principal esta en la lista de decoys, o que no corresponde
    a ningun scheme plantado.

eval/run_evaluation.py
  python eval/run_evaluation.py --seeds 101 102 103 104 105 --out eval/results_table.csv
  - por cada seed: genera el estate con dev/generate_estate.py, corre src.main,
    puntua contra dev/answer_key_<seed>.json
  - emite un CSV con las columnas EXACTAS de
    official_materials/.../results_table_template.csv:
    seed,schemes_planted,schemes_found,recall_pct,decoys_planted,decoys_accused,
    false_accusation_rate_pct,peso_claimed,peso_actual,peso_reconciles,llm_calls,
    mxn_cost,wall_clock_s
    mas una fila TOTAL
  - imprime tambien un desglose de recall POR TIPO DE ESQUEMA (para saber donde estamos flojos)

DISCIPLINA DE SEEDS (va en el pitch):
  seeds de tuning: 1..10
  seeds de reporte: 101..105  (minimo 5, disjuntos de los de tuning)
  El script debe rechazar con error si le pasan un seed de tuning en --seeds para reporte,
  salvo que se le pase --allow-tuning-seeds explicitamente.

TESTS: tests/test_eval_metrics.py  (los tests SI pueden importar de eval/, solo src/ no puede)
  - recall 100% / 0%
  - una falsa acusacion contra un decoy se cuenta bien
  - la reconciliacion de pesos usa la misma regla per-table al 2%
```

---

# FASE D — Limpieza e higiene

## TAREA D1 — Retirar el codigo legacy y arreglar la suite

```
TAREA: limpieza. Cambios pequeños, alto impacto en percepcion y reproducibilidad.

1. CODIGO MUERTO CON ESQUEMA FALSO
   src/detectors/tabulares.py, src/detectors/bayes_tabular.py y
   src/verifier/tabular_verifier.py usan un esquema que NO EXISTE en el reto
   (invoice_id, tabla payments, vendor_id), importan sklearn (que no esta en
   requirements.txt) y ejecutan DataFrames de demo a nivel de modulo al importarse.
   Ningun modulo de produccion los usa.

   Accion: moverlos a legacy/ en la raiz del repo, con un legacy/README.md de 5 lineas
   que diga que son prototipos de la fase exploratoria, que usan un esquema previo al
   oficial y que no forman parte del pipeline. Borra sus imports y tests si quedan rotos.
   Razon: un juez que hace grep en src/ los va a ver y va a pensar que el sistema
   corre sobre un esquema inventado.

2. TESTS DE GNN QUE ROMPEN LA COLECCION
   tests/test_gnn_discovery_adapter.py, test_gnn_graph_builder.py y test_gnn_model.py
   hacen `import torch` a nivel de modulo. En una instalacion base (solo requirements.txt)
   `pytest` FALLA EN COLLECTION y no corre ni un test.

   Accion: al inicio de cada uno de esos modulos,
     torch = pytest.importorskip("torch")
     pytest.importorskip("torch_geometric")
   Verificacion: `pytest -q` sin torch instalado debe pasar limpio, saltandose esos modulos.

3. PAQUETES
   Añadir src/__init__.py, src/detectors/__init__.py, src/verifier/__init__.py
   (vacios o con un docstring). Hoy son namespace packages implicitos, inconsistentes
   con el resto del arbol.

4. REQUIREMENTS
   requirements.txt: solo lo que el pipeline determinista necesita de verdad.
   requirements-gnn.txt: torch + torch-geometric (ya existe).
   requirements-llm.txt: nuevo, google-genai.
   Documentar los tres en el README.

5. CI LOCAL
   scripts/check.sh (y check.ps1) que corra en este orden:
     pytest -q
     grep -r 'ground_truth' src/ --include='*.py'   # debe salir vacio, si no, exit 1
     grep -rn 'C:\\\\\|/home/\|/Users/' src/         # rutas hardcodeadas, debe salir vacio
   Documentarlo en CLAUDE.md.

ACEPTACION: pytest -q pasa en una maquina limpia con solo requirements.txt instalado,
y los tres greps salen vacios.
```

## TAREA D2 — Leads mas inteligentes

```
TAREA: mejorar src/investigation/lead_builder.py

Contexto: hoy build_leads agrupa las observaciones por observation.entities[0].
Dos problemas reales:
  - para una observacion de ciclo bancario, entities[0] es un "CLABE:...", que no es una
    entidad oficial. Eso crea leads con sujeto no reportable.
  - los jueces plantan esquemas ENTRELAZADOS (dos esquemas compartiendo una entidad).
    Agrupar todo por la misma entidad los funde en un solo lead gigante y perdemos recall.

CAMBIOS

1. Normalizacion de entidad antes de agrupar:
   resolve_subject(estate, observation) -> str
     - CLABE: -> el RFC del vendor o el EMP del empleado dueño de esa cuenta
     - si la CLABE no tiene dueño conocido en el estate, se conserva como CLABE: pero
       el lead se marca con una limitacion "sujeto sin dueño identificado en el estate"
     - prefiere siempre RFC:/EMP: como sujeto del lead

2. Separacion por familia de señal:
   agrupar por (sujeto_normalizado, familia_de_señal) donde familia es una de:
     documentary | banking_flow | structural_cycle | invoice_clustering | revenue
   Asi un vendor que participa en un kickback Y en threshold_splitting genera dos leads
   distintos, que es exactamente el caso entrelazado que los jueces plantan.

3. Prioridad determinista y explicable (NO un score de IA):
   lead_priority(lead, observations, estate, decoy_assessment) -> LeadPriority
     señales independientes  usa src.agents.method_critic.find_evidence_dependencies para
                             NO contar dos veces observaciones que salen de las mismas filas
     exposicion en pesos     suma de los montos de los registros citados
     penalizacion decoy      desde DecoyAssessment.priority_penalty
     cobertura documental    cuantas tablas distintas respaldan el lead
   Devuelve un numero Y una explicacion en texto de como se compuso.
   Esa explicacion va al case file. Un score sin explicacion no sirve aqui.

4. build_leads acepta max_leads y devuelve los leads ordenados por prioridad,
   ademas de la lista completa, para que src/main.py pueda gastar LLM solo en el top-K
   y aun asi reportar los demas en leads_not_pursued.

REGLA: todo sigue siendo determinista. Empates se rompen por lead_id.

TESTS: tests/test_lead_builder_priority.py
  - un CLABE con dueño conocido produce un lead con sujeto RFC:/EMP:
  - un vendor con señales de dos familias produce dos leads
  - la prioridad no cuenta dos veces dos observaciones que citan el mismo record
  - el orden es estable entre corridas
```

---

## Orden recomendado y por que

| # | Tarea | Desbloquea |
|---|---|---|
| 1 | A1 generador | todo lo demas: sin datos no hay nada que medir |
| 2 | A2 LLM client | el pipeline agentic + determinismo + replay + los 3 numeros |
| 3 | A3 reglas | el Evidence Gate deja de bloquear siempre |
| 4 | A4 amount | el Verifier deja de quedarse en `unresolved` |
| 5 | A5 CLI | primer `submission.json` que pasa el validador oficial |
| 6 | B3 verificadores | recall de 1/5 esquemas a 5/5 |
| 7 | B1 detectores | encontrar mas cosas |
| 8 | B2 anti-decoy | no acusar de mas, y gastar menos LLM |
| 9 | B4 money trail + leads | Clarity + la pregunta garantizada del juez |
| 10 | C1 case file | el artefacto que se califica |
| 11 | C2 eval | la tabla de resultados del pitch |
| 12 | D1, D2 | higiene y recall en casos entrelazados |

Despues de A5 ya existe un entregable valido. Todo lo de B en adelante sube el score.
Si el tiempo se acaba, **A1→A5 + C1 es el minimo presentable**.

---

## Tres cosas que NO hay que hacer

1. **No pedirle todo en un prompt.** El agente pierde el hilo del IVA, se olvida de vincular
   ledger con invoice, o le mete una columna `is_fraud` a SQLite violando el esquema oficial.
   Una tarea a la vez, con su test.
2. **No dejar que el LLM calcule montos.** Cada peso del case file lo reconstruye Python
   desde filas citadas. Es literalmente lo que se califica.
3. **No tocar `src/` con nada que huela a ground truth.** Ni una variable, ni un comentario,
   ni un nombre de archivo. El grep de los jueces no distingue matices.
