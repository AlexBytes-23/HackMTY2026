# Prompt de unificación y cierre — para Claude Code en terminal

## Cómo usarlo

1. Asegúrate de que **no haya otra sesión de Claude escribiendo en este working tree**.
   Dos agentes commiteando al mismo índice de git se corrompen los diffs.
2. Abre `claude` en la raíz del repo y pega el bloque completo de abajo.
3. Claude va a parar en cada **PUERTA** y reportar. Responde `continúa` para avanzar.
4. Cuando pida permiso para algo destructivo de git (reset de `main`, dejar de trackear archivos,
   force-push), **léelo antes de decir que sí**. Esa es la parte irreversible.

---

````text
MISIÓN: unificar y terminar el Forensic Auditor de HackMTY 2026 (Infosys Track 2)
hasta dejarlo presentable ante jueces.

Trabaja en español. Escribe archivos reales, no resúmenes en el chat.

════════════════════════════════════════════════════════════════════════
PASO 0 — ORIÉNTATE. NO CONFÍES EN LA DOCUMENTACIÓN.
════════════════════════════════════════════════════════════════════════

Este repo se movió muy rápido con varios agentes trabajando en paralelo.
docs/CODEBASE_MAP.md puede estar desactualizado. Empieza por medir el estado real:

  git branch --show-current
  git log --oneline -15
  git status --short
  git branch -a
  git rev-list --left-right --count main...HEAD
  git diff --stat HEAD main | tail -30
  ls dev/ dev_eval/ eval/ src/ 2>/dev/null
  pytest -q 2>&1 | tail -20
  grep -rn 'ground_truth\|answer_key' src/ --include='*.py'
  git ls-files | grep -E '\.pyc$|answer_key|\.db$'

Antes de tocar nada, verifica que no haya otra sesión de Claude escribiendo aquí
(ListAgents). Si la hay, dime y espera: no compartas el índice de git con otro agente.

Después lee, en este orden:
  CLAUDE.md
  docs/CODEBASE_MAP.md
  official_materials/student-materials/forensic-auditor/submission_schema.json
  official_materials/student-materials/forensic-auditor/case_file_structure.md
  official_materials/student-materials/forensic-auditor/ground_truth_schema.json
  official_materials/student-materials/forensic-auditor/validate_format.py
  official_materials/student-materials/forensic-auditor/README.md

Y el código que exista hoy de: src/run_audit.py, src/llm/, dev/estate_generator.py,
src/rules/, src/investigation/claim_builder.py, src/output/, src/verifier/, src/gates/.

PUERTA 0 — Repórtame en máximo 20 líneas:
  · en qué rama estamos y qué tiene main que no tenga esta rama, y al revés
  · qué falta REALMENTE de lo que el reto califica (no lo que dice el doc)
  · qué está duplicado o en conflicto entre módulos
  · si pytest pasa limpio y si los greps salen vacíos
Luego PARA y espera mi "continúa".

════════════════════════════════════════════════════════════════════════
LO QUE NO SE ROMPE NUNCA
════════════════════════════════════════════════════════════════════════

 1. Nada bajo src/ puede leer ground truth ni answer keys. Los jueces corren
    `grep -r 'ground_truth' src/ --include='*.py'`. Si aparece, Results se topa en 2.
    El answer key vive en dev/ y solo lo lee eval/. eval/ jamás se importa desde src/.
 2. La aritmética es de Python, nunca del LLM. Cada peso se reconstruye desde filas citadas.
 3. Ningún finding sin autorización del Evidence Gate.
 4. Nada llega al Verifier sin pasar por Challenger + Method Critic.
 5. El LLM solo puede pedir acciones del Action Bank.
 6. Prohibido inventar leyes, umbrales o políticas: solo las del RuleRegistry. Si un umbral
    se infirió del estate, hay que decir en voz alta que es inferido.
 7. "No se encontró registro en el estate suministrado" nunca es "no existe".
 8. Toda afirmación cita source_table + record_id que existen de verdad.
 9. Un score siempre viaja con su score_semantics. Nunca es una probabilidad de fraude.
10. Sin rutas hardcodeadas: el estate entra por parámetro en runtime.
11. El esquema del estate es inmutable: 8 tablas, columnas exactas, nada de is_fraud.
12. Determinismo: mismo seed, mismo output byte a byte.

Reglas de salida que el validador oficial castiga:
  · peso_amount reconcilia POR TABLA, tolerancia 2% sobre max(mejor_total, 1).
    Citar la factura Y la transferencia que la liquidó NO es dinero doble.
  · mínimo 3 exhibits, exhibit_id únicos, record_id existente.
  · narrative <= 150 palabras, lenguaje llano para un juez no técnico.
  · rule_broken = artículo concreto, no "patrón estadístico".
  · entities con prefijo RFC:/EMP:. CLABE: es interno y NUNCA sale en entities.
  · money_trail presente y continuo: paso[i].to == paso[i+1].from.
  · run_metadata con llm_calls, mxn_cost, wall_clock_seconds reales.
  · leads_not_pursued con razón ESPECÍFICA que nombre la evidencia revisada + tool_calls_made.
    "insufficient evidence" está prohibido como razón.

════════════════════════════════════════════════════════════════════════
FASE 1 — UNIFICACIÓN DE GIT
════════════════════════════════════════════════════════════════════════

Hecho conocido al momento de escribir esto (VERIFÍCALO, pudo cambiar):

  main (== origin/main, 7cf07e8) NO contiene la arquitectura. Es la rama vieja de un
  compañero y tiene tres cosas problemáticas:

  a) generate_estate.py en la raíz (~1032 líneas) que genera un esquema NO OFICIAL:
     vendors con PK vendor_id en vez de rfc, `clabe` en vez de bank_clabe,
     invoices con amount/tax/total_amount en vez de subtotal/iva/total más un
     invoice_id extra junto a uuid, bank_txns con origin_account/destination_account
     en vez de from_clabe/to_clabe, efos_list con `situation` en vez de `status`,
     y ledger sin invoice_uuid/cost_center/approver.
     EstateRepository rechazaría esa DB. NO es sustituto de dev/estate_generator.py.
  b) dev_answer_key.json TRACKEADO EN LA RAÍZ, en formato no oficial
     (scheme_type "PHANTOM_VENDOR_EFOS", vendor_id, is_fraud, total_exposure).
     También trackea data/dev_estate.db y un .pyc.
  c) src/agents/challenger.py en su versión mock temprana. Un merge ingenuo de main
     SOBRESCRIBIRÍA el Challenger adversarial real, y esto es peor que un conflicto de
     archivos: src/gates/evidence_gate.py importa ChallengerOutcome desde ese módulo y
     decide con él (legitimate_alternative -> decline_hypothesis, needs_more_evidence ->
     need_more_work). El mock no tiene ese enum ni la validación de evidence_ids ni la
     restricción al Action Bank, así que el merge dejaría al Evidence Gate SIN capacidad
     de rechazar una hipótesis, en silencio. Es una regresión de corrección disfrazada de
     resolución de conflicto. Por eso main se resetea a la rama de integración, no se mergea.

QUÉ HACER:

1.1 Consolidar el trabajo real en UNA rama de integración. Verifica con
    `git branch --no-merged` que no quede trabajo vivo en ramas feature/* locales
    ni en origin/feature/frontend ni origin/feature/graph-gnn. Si hay algo que valga,
    dime qué es antes de integrarlo.

1.2 De main, RESCATAR SOLO ideas, nunca archivos:
    · si su generador tiene lógica de Faker / inyección de esquemas mejor que la
      de dev/estate_generator.py, pórtala al esquema oficial. Si no, ignóralo.
    · si su challenger tiene el snippet real de google-genai y nuestro src/llm/
      no lo tiene, extráelo a src/llm/gemini.py. NO toques src/agents/challenger.py.

1.3 Dejar de trackear artefactos generados: dev_answer_key.json, data/*.db,
    cualquier .pyc, dev/estates/ y runs/. Añádelos a .gitignore.
    ESTO ES DESTRUCTIVO PARA EL HISTORIAL DEL ÍNDICE: pídeme permiso explícito antes,
    dime exactamente qué comando vas a correr, y usa `git rm --cached`, nunca `git rm`.

1.4 Cuarentena del código legacy con esquema falso:
    src/detectors/tabulares.py, src/detectors/bayes_tabular.py,
    src/verifier/tabular_verifier.py.
    Muévelos a legacy/ con un README de 5 líneas que diga que son prototipos previos
    al esquema oficial y que no forman parte del pipeline.
    Razón: un juez que hace grep en src/ va a creer que corremos sobre un esquema inventado.

1.5 Decidir el destino de dev_eval/ y docs/superpowers/ (hoy sin trackear).
    NO los borres. Pregúntame qué son antes de moverlos.
    Si dev_eval/generate_robust_realism_estate.py es un segundo generador, hay que
    elegir UNO como canónico y documentar por qué; dos generadores divergentes es
    exactamente el bug que nos va a morder en la demo.

1.6 Actualizar main a la rama de integración SOLO cuando yo lo autorice, y
    explicándome antes si va a ser fast-forward, merge o reset. Nunca force-push
    sin mi sí explícito.

PUERTA 1 — pytest verde, los tres greps vacíos
(`ground_truth` en src/, rutas absolutas en src/, .pyc trackeados),
`git status` limpio salvo lo que acordamos. Repórtame y para.

════════════════════════════════════════════════════════════════════════
FASE 2 — UNIFICACIÓN DE CÓDIGO
════════════════════════════════════════════════════════════════════════

Objetivo: que exista UNA sola forma de hacer cada cosa.

2.1 UN entrypoint. Si hay más de uno (src/run_audit.py, src/main.py, scripts sueltos),
    consolida en uno y documenta la invocación exacta en CLAUDE.md:
      python -m src.run_audit --estate <ruta> --seed N --out runs/N/ [--offline]
    Debe aceptar .db, estate_csv.zip y un directorio con los 8 CSV. Sin rutas hardcodeadas.

2.2 UN generador y UN formato de answer key, el de ground_truth_schema.json:
    { seed, company_rfc, schemes[{scheme_id,type,entities,supporting_invoices,
      supporting_txns,peso_amount,difficulty}], decoys[{entity,signal,why_innocent,invoices}] }
    Si hay answer keys en otro formato, conviértelos o bórralos. No dejes dos.

2.3 Modelos duplicados. En src/core/models.py existen ChallengerReview y MethodReview
    que son stubs muertos: los reales viven en src/agents/. Elimina los stubs o
    márcalos como deprecados, pero que no haya dos definiciones del mismo contrato.
    Busca duplicaciones equivalentes en el resto del árbol.

2.4 UNA fuente de verdad para la reconciliación de pesos: src/output/reconciliation.py.
    Si alguien reimplementó la tolerancia del 2% en otro lado, haz que la importe.

2.5 Cobertura de los 5 esquemas end-to-end. Para cada uno de
    phantom_vendor, kickback, round_tripping, threshold_splitting, revenue_inflation
    debe existir la cadena completa: detector -> verificador sustantivo determinista ->
    rama del finding builder -> regla aplicable en el RuleRegistry.
    Hazme una tabla de 5 filas y 4 columnas marcando qué eslabón falta en cada uno.
    Completa los que falten. Un esquema sin verificador sustantivo NUNCA puede emitir
    finding, así que cada hueco ahí es recall perdido directo.

2.6 __init__.py faltantes en src/, src/detectors/, src/verifier/ si siguen faltando.

PUERTA 2 — la tabla de 5x4 completa, pytest verde. Repórtame y para.

════════════════════════════════════════════════════════════════════════
FASE 3 — LO QUE EL JUEZ VE
════════════════════════════════════════════════════════════════════════

3.1 money_trail real, no lista vacía. Construido desde los bank_txns citados,
    resolviendo cada CLABE a su dueño (RFC:/EMP:), ordenado por fecha, continuo,
    y cada paso citando el exhibit_id del bank_txn. Si no se puede armar una cadena
    continua, devuelve lista vacía en vez de inventar pasos.

3.2 leads_not_pursued generado automáticamente para TODO caso que no produjo finding:
      entity           entidad principal con prefijo oficial
      signal           el signal_type real del detector que abrió el lead
      reason           específica, construida de lo que pasó de verdad:
                       el close_reason del filtro anti-decoy (que ya cita records), o
                       la alternativa legítima del Challenger y cómo se verificó, o
                       el material_blocker del Method Critic, o
                       qué check del Verifier falló y por qué, o
                       "no investigado por límite de llamadas; señal registrada"
      tool_calls_made  [r.action_name for r in case_state.actions_taken]
      closed_by        investigator | challenger | validator
    Prohibido "insufficient evidence". Toda razón cita un record_id o un nombre de check.

3.3 Case file HTML autocontenido, sin red, con las 5 secciones del formato oficial
    EN ORDEN: Header (empresa, periodo, seed, llm_calls, costo MXN, wall-clock,
    determinista sí/no) · Executive summary con su tabla · una sección por finding
    (heading, rule broken, monto y confianza, qué pasó <150 palabras, MONEY TRAIL COMO
    DIAGRAMA, tabla de exhibits, aritmética de reconciliación, y qué argumentó la revisión
    adversarial y por qué el finding sobrevivió) · Leads not pursued EN EL CUERPO ·
    Method and limits (incluyendo QUÉ NO PUEDE DETECTAR el sistema).

    El diagrama es SVG inline generado en Python. Cero recursos externos, cero fetch,
    CSS inline. Prosa sola topa Clarity en 3, así que esto no es opcional.
    Si findings está vacío, el case file debe decirlo claramente y explicar qué se
    investigó y por qué se cerró: "no encontramos nada" con evidencia es un resultado
    legítimo y puntúa mejor que inventar.

3.4 Determinismo y replay verificados de verdad:
      correr el mismo seed dos veces produce submission.json y case_file.html idénticos
      (compara sha256); correr con --offline reproduce el run sin red.

PUERTA 3 — enséñame la ruta del case file generado y los sha256 de dos corridas
del mismo seed. Repórtame y para.

════════════════════════════════════════════════════════════════════════
FASE 4 — NÚMEROS PARA EL PITCH
════════════════════════════════════════════════════════════════════════

4.1 eval/ (fuera de src/, y src/ jamás lo importa) que por cada seed genere el estate,
    corra el auditor y puntúe contra el answer key. Emite un CSV con las columnas
    EXACTAS de official_materials/.../results_table_template.csv, más fila TOTAL,
    más un desglose de recall POR TIPO DE ESQUEMA.

    TRAMPA CONOCIDA: detect_directed_bank_transfer_cycles emite entities como
    CLABE:<clabe>, no RFC:. El answer key usa RFC:/EMP:. Si el harness compara contra
    las entities crudas de la observación, los ciclos NUNCA van a hacer match y el
    recall de round_tripping va a salir falsamente en cero. Resuelve CLABE -> dueño
    (vendors.bank_clabe / employees.bank_clabe) antes de comparar. Verifica también
    que ningún CLABE: se filtre a submission.findings[].entities, que solo admite RFC:/EMP:.

4.2 Disciplina de seeds, que va en el pitch:
      tuning: 1..10        reporte: 101..105 (mínimo 5, disjuntos)
    El script rechaza seeds de tuning en el reporte salvo --allow-tuning-seeds.

4.3 Corre la evaluación real y dame la tabla. Si el recall es bajo o la tasa de falsa
    acusación es alta, dime DÓNDE está el problema (qué esquema, qué decoy) antes de
    tocar umbrales. No ajustes parámetros contra los seeds de reporte: eso es tuning
    disfrazado y es justo lo que el reto prohíbe.

4.4 Actualiza docs/CODEBASE_MAP.md y CLAUDE.md contra el árbol final. El mapa fue
    escrito contra el commit 98194a5 y seguro ya no describe la realidad.

PUERTA 4 — la tabla de resultados y los tres números del run. Repórtame y para.

════════════════════════════════════════════════════════════════════════
FASE 5 — ENSAYO DE DEMO
════════════════════════════════════════════════════════════════════════

Verifica y repórtame cada punto con evidencia, no con un "sí":

  [ ] python -m src.run_audit --estate <estate_nuevo> --seed N --out runs/N/ corre completo
  [ ] validate_format.py --submission runs/N/submission.json --estate <estate> => PASS
  [ ] mismo seed dos veces => sha256 idéntico
  [ ] --offline con la red apagada reproduce el run
  [ ] case_file.html abre en navegador, 5 secciones en orden, diagrama visible
  [ ] tabla de resultados con >=5 seeds held-out, recall Y tasa de falsa acusación, seeds nombrados
  [ ] llm_calls, mxn_cost, wall_clock_seconds visibles en el header del case file
  [ ] pytest verde en instalación limpia con solo requirements.txt
  [ ] grep -r 'ground_truth' src/ --include='*.py' vacío

Y prepárame las respuestas, citando el archivo y la línea del log de donde salen:
  · "¿por qué no marcaste al vendor X?"
  · "¿qué tan confiado estás en el finding 2?"
  · "¿y si el empleado solo tiene cuenta en el mismo banco?"
  · "¿qué pasa si cambio este input?"
  · "¿qué NO puede detectar esto?"
  · "¿qué recortaron y por qué?"
Responder desde el log en menos de 10 segundos también se califica. Re-correr el
sistema es la respuesta equivocada aunque el número sea correcto.

════════════════════════════════════════════════════════════════════════
CÓMO TRABAJAR
════════════════════════════════════════════════════════════════════════

· Una fase a la vez. Para en cada PUERTA y espera mi "continúa".
· Commit por unidad de trabajo, mensajes convencionales (feat:, fix:, refactor:, docs:).
  Nunca commitees a main sin autorización mía.
· Ningún módulo nuevo sin su test en tests/test_<modulo>.py.
· No refactorices lo que no te pedí. Si ves algo mal fuera de alcance, anótalo y sigue.
· Si algo del reto y algo del código se contradicen, gana el reto: dímelo y para.
· Si una fase se vuelve más grande de lo que parecía, dime el alcance real antes de
  empezar, no a la mitad.
· Antes de cualquier operación destructiva (git reset, rm, git rm, force-push,
  sobrescribir un archivo que no escribiste tú), pídeme permiso y dime el comando exacto.

PRIORIDAD SI SE ACABA EL TIEMPO:
  Fase 1 y 2 (unificación) + 3.3 (case file con diagrama) es el mínimo presentable.
  Un sistema que encuentra menos pero se explica solo puntúa más que uno que encuentra
  más y no puede defenderlo.
````

---

## Notas para ti (no van en el prompt)

- **La parte irreversible es 1.3 y 1.6.** Dejar de trackear `dev_answer_key.json` y actualizar `main`.
  Léelo bien cuando Claude te pida permiso.
- **`dev_eval/` y `docs/superpowers/`**: están sin trackear y no sé qué son. Claude te va a preguntar.
  Si `dev_eval/generate_robust_realism_estate.py` es un segundo generador, decide cuál es el bueno
  antes de la Fase 2 — dos generadores divergentes rompen la evaluación en silencio.
- **Si hay otra sesión de Claude trabajando**, espera a que termine antes de pegar esto.
  Dos agentes en el mismo índice de git se corrompen los commits.
