# CLAUDE.md — Forensic Auditor (HackMTY 2026, Infosys Track 2)

**Antes de escribir código en este repo, lee [`docs/CODEBASE_MAP.md`](docs/CODEBASE_MAP.md).**
Ahí está el contrato oficial del reto, la arquitectura completa, el inventario módulo-por-módulo,
los gaps priorizados y el plan de trabajo. Este archivo solo tiene lo mínimo que no se puede olvidar.

Para tareas concretas listas para delegar, ver [`docs/ANTIGRAVITY_BRIEF.md`](docs/ANTIGRAVITY_BRIEF.md).

---

## Qué es esto

Un auditor forense agentic que recibe el estate contable de una empresa (8 tablas, esquema oficial
inmutable), investiga, sigue el dinero y entrega un expediente defendible — **sin acusar a nadie que
no pueda respaldar**. Los jueces lo corren sobre estates nunca vistos con hasta 5 esquemas de fraude
y 10 decoys entrelazados.

Los 5 esquemas: `phantom_vendor`, `kickback`, `round_tripping`, `threshold_splitting`, `revenue_inflation`.

---

## Las 12 reglas que no se rompen

1. **Nada en `src/` puede leer ground truth.** Los jueces corren
   `grep -r 'ground_truth' src/ --include='*.py'`. Si aparece, Results tope 2.
   El answer key vive en `dev/` y solo lo lee `eval/`.
2. **La aritmética es de Python, no del LLM.** Montos, sumas y matches los recalcula el verificador.
3. **Ningún finding sin `authorize_*` del Evidence Gate.**
4. **Nada llega al Verifier sin pasar por Challenger + Method Critic.**
5. **El LLM solo puede pedir acciones del Action Bank.** Python resuelve y ejecuta.
6. **Prohibido inventar ley.** Una regla legal solo se usa si viene del `RuleRegistry`.
7. **Ausencia en el estate ≠ ausencia en la realidad.** Se escribe "no se encontró registro en el
   estate suministrado", nunca "no existe".
8. **Toda afirmación cita `source_table` + `record_id` que existen.**
9. **Un score siempre viaja con su `score_semantics`.** Nunca se presenta como probabilidad de fraude.
10. **Sin rutas hardcodeadas.** El estate entra por parámetro en runtime.
11. **El esquema del estate es inmutable.** Nada de columnas `is_fraud` / `scheme_type` en la DB.
12. **Determinismo.** Mismo seed ⇒ mismo output, byte a byte. Orden explícito en todo iterable.

---

## Reglas de salida que el validador oficial castiga

- `peso_amount` reconcilia **por tabla**, tolerancia **2 %** (`max(mejor_total, 1)`).
  Citar la factura *y* la transferencia que la liquidó **no** es dinero doble.
- ≥ **3 exhibits** por finding, `exhibit_id` únicos, `record_id` existente.
- `narrative` ≤ **150 palabras**, lenguaje llano.
- `rule_broken` = artículo/regla concreta, no "patrón estadístico".
- `entities` con prefijo: `RFC:...` / `EMP:...`. `CLABE:` es interno, **nunca** sale en un finding.
- `money_trail` presente y continuo (`paso[i].to == paso[i+1].from`), cada paso cita un exhibit del finding.
- `run_metadata` con `llm_calls`, `mxn_cost`, `wall_clock_seconds` reales.
- `leads_not_pursued` con razón **específica** (nombra la evidencia revisada) y `tool_calls_made`.

---

## Comandos

```bash
pip install -r requirements.txt          # core
pip install -r requirements-gnn.txt      # opcional: torch + torch-geometric
pytest -q                                # 222 tests. OJO: hoy falla la colección si no hay
                                         # torch instalado (3 módulos de GNN lo importan a
                                         # nivel de módulo). Ver docs/CODEBASE_MAP.md §7.
grep -r 'ground_truth' src/ --include='*.py'   # debe salir vacío
python official_materials/student-materials/forensic-auditor/validate_format.py \
    --submission runs/<seed>/submission.json --estate <ruta_estate>
```

`.env` tiene `GEMINI_API_KEY` y está en `.gitignore`. **No commitearla, no imprimirla, no mandarla a ningún servicio.**

---

## Estado (commit 98194a5)

Funciona: estate repo, 6 detectores deterministas, ciclos bancarios, GNN opcional, loop agentic,
Challenger + Method Critic, verificador oficial, Evidence Gate, modelos de salida, submission writer.

Falta (bloqueante): generador de estate, `LLMClient` real, CLI end-to-end, reglas en el registry,
cálculo de `claimed_amount`, 4 de 5 esquemas, case file con diagrama, `leads_not_pursued`.

Detalle completo y priorizado en `docs/CODEBASE_MAP.md` §7–§8.

---

## Estilo

- Español en docstrings y comentarios nuevos si el módulo ya está en español; inglés si ya está en inglés.
  No traducir módulos existentes.
- Pydantic para todo contrato entre capas. Validar en Python lo que no se le confía al LLM.
- Cada detector nuevo debe declarar `limitations`, `legitimate_alternatives` y `recommended_checks`.
- Tests junto al módulo, en `tests/test_<modulo>.py`. No se mergea nada sin test.
