# LegLens — Auditor Forense Agéntico

**HackMTY 2026 · Reto Infosys, Track 2 (The Forensic Auditor)**

Un auditor forense que recibe el estate contable de una empresa, investiga, sigue el dinero
y entrega un expediente defendible — **sin acusar a nadie que no pueda respaldar**.

---

## La idea en una línea

> **La IA interpreta. Python verifica. La evidencia decide.**

El sistema distingue tres cosas que se confunden todo el tiempo:

```
anomalía  ≠  sospecha  ≠  fraude
```

El LLM decide **qué investigar a continuación**. No tiene autoridad para acusar.
Ningún hallazgo sale del sistema sin que Python lo haya recalculado contra los
registros originales del estate.

---

## Por qué eso importa, con un número

En una versión anterior de este repo, el sistema emitía un `submission.json` que
**pasaba el validador oficial** — contra el `.db` y contra el `.zip` — con dos hallazgos.
Uno de ellos era una **acusación falsa de $2,506,760.00 MXN** contra un proveedor inocente.

Formato perfecto. 50 % de precisión.

El proveedor (`RFC:GAL160126E3F`) sólo estaba **presunto** en la lista del 69-B, y sus cinco
facturas se emitieron **más de un año antes** de que se publicara el listado, todas amparadas
por órdenes de compra aprobadas bajo un contrato de 2023. Nuestro verificador comparaba
`efos_list.rfc` contra `invoices.issuer_rfc` y nada más: no leía `status` ni `publication_date`.

Ese número es la razón de ser de todo lo demás en este repo. Un validador de formato no
puede verlo. Por eso construimos nuestra propia respuesta correcta y un arnés que la mide.

Hoy ese caso se bloquea y aparece en `leads_not_pursued` con su razón documental.

---

## Arquitectura

```
estate.db
   └─> EstateRepository            esquema oficial estricto, única puerta al estate
        └─> Detectores deterministas + grafo bancario exacto + GNN opcional
             └─> Observations  ->  Leads  ->  CaseState
                  └─> Investigator          decide la siguiente acción (Action Bank)
                       └─> Challenger       ataca la hipótesis; ¿qué explicación legítima la explica?
                            └─> Method Critic   ataca el MÉTODO, no el negocio
                                 └─> claim_builder      monto por regla de alcance fija
                                      └─> OfficialVerifier   recalcula todo en Python
                                           └─> EvidenceGate      falla cerrado
                                                └─> FindingBuilder -> submission.json
```

Cada capa puede **negarse**. Ninguna puede saltarse la siguiente.

| Componente | Qué hace | Qué **no** hace |
|---|---|---|
| `EstateRepository` | Acceso único al estate, valida el esquema oficial | No decide qué es sospechoso |
| Detectores | Señales factuales y relacionales | Una señal no es una acusación |
| GNN (opcional) | Descubrimiento y priorización | **Nunca es prueba.** Su score no es una probabilidad de fraude |
| Investigator | Elige la siguiente acción útil | No autoriza hallazgos |
| Challenger | Construye la explicación legítima más fuerte | No verifica montos |
| Method Critic | Detecta doble conteo, correlación≠causalidad, ley inventada | No decide culpabilidad |
| OfficialVerifier | Resuelve cada exhibit contra el estate, reconcilia pesos | No interpreta |
| EvidenceGate | Autoriza o rechaza. **Falla cerrado** | Nunca llega a `proven` hoy |

---

## Cómo correrlo

```bash
pip install -r requirements.txt          # núcleo
pip install -r requirements-gnn.txt      # opcional: torch + torch-geometric
pip install -r requirements-ui.txt       # opcional: interfaz de escritorio
```

### Auditar un estate

```bash
python -m src.run_audit \
    --estate ruta/al/estate.db \
    --seed 42 \
    --out runs/42/submission.json \
    --replay grabaciones/sesion.jsonl
```

La ruta del estate **siempre entra por parámetro**. No hay rutas hardcodeadas.

### Generar un estate sintético con su answer key

```bash
python -m dev.generate_estate --seed 101 --out-dir dev/estates/101
```

Escribe `estate.db` y `ground_truth.json`. Mismo seed ⇒ **mismos bytes**.

### Interfaz de escritorio

```bash
python -m ui.leglens_app
```

### Validar el formato oficial

```bash
python official_materials/student-materials/forensic-auditor/validate_format.py \
    --submission runs/42/submission.json --estate ruta/al/estate.db
```

---

## Reproducibilidad y costo

- **Determinismo.** Mismo seed ⇒ mismo output, byte a byte. Orden explícito en todo iterable.
- **Replay sin red.** `--replay` reproduce una corrida completa con la conectividad apagada.
  `ReplayLLMClient` no contiene ninguna referencia a un proveedor ni ninguna llamada de red.
- **Costo real, nunca inventado.** `run_metadata` lleva `llm_calls`, `mxn_cost` y
  `wall_clock_seconds` **medidos**. `wall_clock_seconds` cubre la corrida completa —
  detectores, grafo, GNN y verificación — no sólo la latencia del LLM.
  Si el costo real es desconocido, el sistema **aborta** en lugar de serializar un `0` falso.

---

## Qué está verificado

```
551 tests pasando, 3 saltados (los de GNN, sin torch instalado)
```

- El camino completo `estate.db -> submission.json` pasa el **validador oficial**,
  con verificación de exhibits contra el estate.
- `grep -r 'ground_truth' src/ --include='*.py'` sale **vacío**. El answer key vive en
  `dev/` y sólo lo leen `dev_eval/` y las pruebas.
- Un arnés adversarial (`dev_eval/probe_pipeline.py`) falla con código distinto de cero
  ante cualquier autorización contra una entidad que no sea un esquema plantado.

---

## Qué NO detecta

Decirlo abiertamente puntúa mejor que insinuar una cobertura que no se puede defender.

| Esquema | Estado |
|---|---|
| `phantom_vendor` | **Verificador sustantivo completo.** Exige listado `definitivo` y facturas emitidas en o después de la publicación |
| `kickback` | **Verificador sustantivo completo.** Exige transferencia entre cuentas *distintas* del proveedor al empleado **y** que ese empleado apruebe una orden de compra citada de ese proveedor |
| `threshold_splitting` | Se detecta y levanta lead. **Sin verificador** ⇒ no se convierte en hallazgo |
| `round_tripping` | Se detecta el ciclo. **Verificador deliberadamente descartado** — ver abajo |
| `revenue_inflation` | Señalado. **Sin verificador** |

**Por qué descartamos `round_tripping`:** un ciclo dirigido de transferencias prueba que
existen esas transferencias, **no** que el mismo dinero regresó. El estate trae fechas sin
marca de tiempo intradía. En nuestro estate adversarial hay un señuelo (`RFC:ORB160721LW3`)
que es un ciclo benigno de 3 CLABEs **por la misma cuenta operativa** que un round trip real,
con topología idéntica. Sólo se distinguen por continuidad de montos (1.5 % vs 16×) y
proximidad de fechas (días vs meses). Sin esa prueba de continuidad en el detector, un
verificador acusaría al señuelo. Preferimos dejarlo como lead.

**`authorize_proven` es inalcanzable por diseño.** No existe un estándar de prueba
específico por esquema, así que el techo de confianza es `probable`.

---

### Los dos señuelos que el verificador de kickback resiste

Medido sobre la estate adversarial. Cada señuelo cae por una condición **distinta**, lo
que significa que las dos condiciones cargan peso y ninguna es redundante:

| Señuelo | Por qué no se acusa |
|---|---|
| El proveedor **es** el empleado — persona física con actividad empresarial, comparte los 18 dígitos del CLABE, con contrato y órdenes de compra | *"A shared account is consistent with the vendor and the employee being the same person."* Falla la primera condición: no hay transferencia entre cuentas distintas |
| Reembolso de gastos documentado del proveedor al empleado | *"Without the approval link the transfer is consistent with an ordinary documented payment."* Falla la segunda: ese empleado no aprueba sus órdenes de compra |

Ese texto sale del verificador, no de una narrativa — es citable tal cual en el case file.

El esquema real sí se autoriza, por **MXN 534,238.00**, que coincide exactamente con el
answer key.

## Las reglas que no se rompen

1. Nada en `src/` puede leer ground truth.
2. La aritmética es de Python, no del LLM.
3. Ningún hallazgo sin `authorize_probable` del Evidence Gate.
4. Nada llega al Verifier sin pasar por Challenger + Method Critic.
5. El LLM sólo puede pedir acciones del Action Bank.
6. Prohibido inventar ley. Sólo se cita una regla del `RuleRegistry`.
7. **Ausencia en el estate ≠ ausencia en la realidad.** Se escribe *"no se encontró registro
   en el estate suministrado"*, nunca *"no existe"*.
8. Toda afirmación cita `source_table` + `record_id` que existen.
9. Un score siempre viaja con su `score_semantics`. Nunca es una probabilidad de fraude.
10. Sin rutas hardcodeadas.
11. El esquema del estate es inmutable.
12. Determinismo.
13. `CLABE:` es interno. **Nunca** sale en un hallazgo.

---

## Estructura

```
src/            el auditor. Nunca importa dev/ ni eval/
  core/         EstateRepository y modelos compartidos
  detectors/    6 detectores deterministas
  graph/        grafo bancario exacto
  gnn/          descubrimiento neuronal opcional
  investigation/ Investigator, Action Bank, pipelines, claim_builder
  agents/       Challenger, Method Critic
  verifier/     OfficialVerifier
  gates/        EvidenceGate
  rules/        RuleRegistry (CFF Art. 69-B; segregacion de funciones)
  output/       FindingBuilder, SubmissionBuilder
  run_audit.py  CLI end-to-end
dev/            generador de estates + answer key   (fuera del alcance del agente)
dev_eval/       estate adversarial held-out + probe (fuera del alcance del agente)
ui/             interfaz de escritorio
tests/          551 pruebas pasando, 3 saltadas
```

---

## Limitaciones honestas

- Dos de los cinco esquemas tienen verificador sustantivo, así que el recall está acotado
  por diseño. Medido sobre la estate adversarial held-out: **2 de 5 esquemas encontrados,
  cero señuelos acusados** de 18 plantados.
  Preferimos pocos hallazgos defendibles a muchos indefendibles.
- La ausencia de un registro en el estate nunca se reporta como ausencia en la realidad.
- Múltiples detectores sobre los mismos registros **no** son corroboración independiente,
  y el Method Critic los detecta explícitamente.
- Un ciclo en el grafo no es prueba de que el mismo dinero volvió.
