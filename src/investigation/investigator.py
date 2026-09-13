from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError

from src.core.models import (
    CaseState,
    InvestigatorDecision,
)

from src.investigation.action_bank import (
    ActionDefinition,
    list_available_actions,
)

from src.llm.json_text import strip_code_fences

# ============================================================
# INTERFAZ DEL MODELO
# ============================================================

class LLMClient(Protocol):
    """
    Contrato mínimo para cualquier modelo.

    Después podremos conectar:
    - Gemini
    - Ollama
    - otro proveedor

    Investigator no necesita saber cuál.
    """

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        ...


# ============================================================
# SYSTEM PROMPT
# ============================================================

INVESTIGATOR_SYSTEM_PROMPT = """
Eres el Investigator de un sistema de auditoría forense.

Tu función NO es clasificar automáticamente fraude.
Tu función es dirigir una investigación de forma racional,
económica, reproducible y adversarial.

REGLAS FUNDAMENTALES:

1. Una anomalía no es fraude.

2. Distingue siempre:
   - KNOWN: hechos presentes en la evidencia.
   - INFERENCE: interpretaciones posibles.
   - UNKNOWN: cosas todavía no comprobadas.

3. No inventes hechos, IDs, contratos, movimientos,
   personas ni documentos.

4. Sólo puedes pedir acciones existentes en el Action Bank.

5. Antes de pedir una acción, pregunta:
   "¿Cómo podría cambiar mi conclusión el resultado
   de esta acción?"

6. Prefiere acciones con alto valor informativo:
   acciones cuyo resultado pueda fortalecer O debilitar
   una hipótesis.

7. Busca activamente evidencia contraria.
   No investigues únicamente para confirmar tu primera idea.

8. Una explicación legítima plausible debe investigarse
   cuando exista una acción razonable que permita comprobarla.

9. No confundas:
   correlación,
   proximidad temporal,
   monto parecido,
   mismo banco,
   anomalía estadística,
   ciclo financiero,
   con prueba de fraude.

10. No repitas una acción que ya fue ejecutada
    salvo que exista una razón concreta para hacerlo.

11. Si una hipótesis cambia debido a nueva evidencia,
    actualízala explícitamente.

12. Si existen huecos interpretativos importantes,
    solicita continuar investigando.

13. Si la hipótesis ya está razonablemente desarrollada
    y necesita revisión adversarial, usa:
    request_review

14. Si el caso ya resistió investigación y sólo falta
    comprobar hechos objetivamente, usa:
    ready_for_verification

15. Si no existe suficiente evidencia y tampoco queda una
    acción útil razonable, usa:
    close_inconclusive

16. Nunca declares culpabilidad.
    El Investigator no autoriza findings finales.

17. Cuando nueva evidencia resuelva una pregunta pendiente,
    inclúyela en resolved_unknowns.

18. No elimines silenciosamente hipótesis anteriores.
    Si una hipótesis deja de ser plausible, márcala como
    rejected o inconclusive.

Debes devolver SOLAMENTE JSON válido.
"""


# ============================================================
# SERIALIZAR EL CASE STATE
# ============================================================

def case_state_to_context(
    case_state: CaseState,
) -> dict[str, Any]:
    """
    Convierte el estado interno del caso a un objeto
    explícito para el LLM.

    No enviamos objetos Python opacos.
    """

    return case_state.model_dump(
        mode="json"
    )


def actions_to_context(
    actions: list[ActionDefinition],
) -> list[dict[str, Any]]:
    """
    Le muestra al modelo qué herramientas existen,
    para qué sirven y qué NO prueban.
    """

    return [
        action.model_dump(
            mode="json"
        )
        for action in actions
    ]


# ============================================================
# CREAR PROMPT
# ============================================================

def build_investigator_prompt(
    case_state: CaseState,
    available_actions: list[
        ActionDefinition
    ] | None = None,
) -> str:

    if available_actions is None:
        available_actions = (
            list_available_actions()
        )

    context = {
        "case_state": (
            case_state_to_context(
                case_state
            )
        ),

        "available_actions": (
            actions_to_context(
                available_actions
            )
        ),

        "required_output": {
            "decision": (
                "investigate | request_review | "
                "ready_for_verification | "
                "close_inconclusive"
            ),

            "current_assessment": (
                "Resumen breve de qué sabemos, "
                "qué inferimos y qué sigue siendo desconocido."
            ),

            "hypotheses": [
                {
                    "hypothesis_id": "H-001",

                    "statement": "...",

                    "scheme_type": (
                        "phantom_vendor | kickback | "
                        "round_tripping | "
                        "threshold_splitting | "
                        "revenue_inflation | null"
                    ),

                    "status": (
                        "open | supported | "
                        "rejected | inconclusive"
                    ),

                    "supporting_evidence_ids": [],

                    "counter_evidence_ids": [],

                    "unresolved_questions": [],
                }
            ],

            "next_action": {
                "action_name": (
                    "nombre exacto de una acción "
                    "existente en el Action Bank"
                ),

                "arguments": {},

                "reason": (
                    "por qué esta acción tiene "
                    "valor informativo"
                ),

                "question_resolved": (
                    "qué pregunta concreta intenta resolver"
                ),
            },

            "new_unknowns": [],

            "resolved_unknowns": [],

            "reason": (
                "justificación breve y auditable "
                "de la decisión"
            ),
        },
    }

    return (
        "Analiza el siguiente caso.\n\n"
        + json.dumps(
            context,
            ensure_ascii=False,
            indent=2,
        )
        + "\n\n"
        "Si decision no es 'investigate', "
        "next_action debe ser null."
    )

# ============================================================
# VALIDAR LA RESPUESTA DEL LLM
# ============================================================

def parse_investigator_decision(
    raw_response: str,
) -> InvestigatorDecision:
    """
    Nunca confiamos directamente en texto del LLM.

    Primero:
        texto → JSON

    Después:
        JSON → modelo Pydantic validado

    El modelo suele envolver su JSON en un bloque markdown (```json ...```),
    incluso cuando se le pide JSON explícitamente, así que quitamos la valla
    antes de parsear: de lo contrario la primera llamada real falla y el caso
    se registra como error de ejecución.
    """

    try:
        data = json.loads(
            strip_code_fences(
                raw_response
            )
        )

    except json.JSONDecodeError as error:
        raise ValueError(
            "El Investigator no devolvió "
            f"JSON válido: {error}"
        ) from error

    try:
        return InvestigatorDecision(
            **data
        )

    except ValidationError as error:
        raise ValueError(
            "La respuesta del Investigator "
            "no cumple el contrato esperado: "
            f"{error}"
        ) from error


# ============================================================
# COMPROBAR QUE LA ACCIÓN EXISTA
# ============================================================

def validate_requested_action(
    decision: InvestigatorDecision,
    available_actions: list[
        ActionDefinition
    ] | None = None,
) -> None:
    """
    Impide que el LLM invente herramientas.
    """

    if decision.decision != "investigate":
        return

    if decision.next_action is None:
        raise ValueError(
            "decision='investigate' requiere "
            "next_action."
        )

    if available_actions is None:
        available_actions = (
            list_available_actions()
        )

    allowed_names = {
        action.name
        for action in available_actions
    }

    requested = (
        decision.next_action.action_name
    )

    if requested not in allowed_names:
        raise ValueError(
            f"El Investigator solicitó una acción "
            f"no permitida: {requested}"
        )


# ============================================================
# FUNCIÓN PÚBLICA
# ============================================================

def investigate_case(
    case_state: CaseState,
    llm_client: LLMClient,
    available_actions: list[
        ActionDefinition
    ] | None = None,
) -> InvestigatorDecision:
    """
    Una sola iteración del Investigator.

    Importante:

    Esta función NO ejecuta la acción.

    Sólo decide qué debería hacerse después.
    """

    if available_actions is None:
        available_actions = (
            list_available_actions()
        )

    prompt = build_investigator_prompt(
        case_state=case_state,
        available_actions=available_actions,
    )

    raw_response = llm_client.complete(
        system_prompt=(
            INVESTIGATOR_SYSTEM_PROMPT
        ),
        user_prompt=prompt,
    )

    decision = (
        parse_investigator_decision(
            raw_response
        )
    )

    validate_requested_action(
        decision,
        available_actions,
    )

    return decision

