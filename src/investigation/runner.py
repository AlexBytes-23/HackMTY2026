from __future__ import annotations

import json

from src.core.estate import (
    EstateRepository,
)

from src.core.models import (
    ActionRecord,
    CaseEvidence,
    CaseState,
    Hypothesis,
    InvestigationLoopResult,
    InvestigatorDecision,
)

from src.investigation.action_bank import (
    execute_action,
)

from src.investigation.investigator import (
    LLMClient,
    investigate_case,
)

def _merge_hypotheses(
    existing: list[Hypothesis],
    incoming: list[Hypothesis],
) -> list[Hypothesis]:
    """
    Actualiza hipótesis por ID sin borrar
    silenciosamente hipótesis anteriores.
    """

    merged = {
        hypothesis.hypothesis_id:
            hypothesis.model_copy(deep=True)
        for hypothesis in existing
    }

    for hypothesis in incoming:
        merged[hypothesis.hypothesis_id] = (
            hypothesis.model_copy(deep=True)
        )

    return list(merged.values())


def _action_signature(
    action_name: str,
    arguments: dict,
) -> str:
    """
    Representación estable de una acción.

    Misma acción + mismos argumentos
    producen la misma firma.
    """

    serialized_arguments = json.dumps(
        arguments,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )

    return (
        f"{action_name}:"
        f"{serialized_arguments}"
    )

def apply_investigator_decision(
    case_state: CaseState,
    decision: InvestigatorDecision,
    estate: EstateRepository,
) -> CaseState:
    """
    Aplica UNA decisión del Investigator
    sobre el CaseState.

    Si pidió investigar:
        ejecuta Action Bank
        guarda el resultado
        devuelve el caso actualizado.

    Las demás decisiones sólo cambian
    el estado del caso.
    """

    # Trabajamos sobre una copia para no modificar
    # silenciosamente el objeto original.
    updated = case_state.model_copy(
        deep=True
    )

    # El Investigator puede actualizar
    # las hipótesis actuales.
    updated.hypotheses = _merge_hypotheses(
    existing=updated.hypotheses,
    incoming=decision.hypotheses,
    )

    resolved = set(
    decision.resolved_unknowns
    )

    updated.unknowns = [
        unknown
        for unknown in updated.unknowns
        if unknown not in resolved
    ]

    for unknown in decision.new_unknowns:

        if unknown not in updated.unknowns:
            updated.unknowns.append(
                unknown
            )

    # ========================================================
    # SEGUIR INVESTIGANDO
    # ========================================================

    if decision.decision == "investigate":

        if decision.next_action is None:
            raise ValueError(
                "Investigation decision "
                "requires next_action."
            )

        proposed = (
            decision.next_action
        )

        result = execute_action(
            action_name=proposed.action_name,
            estate=estate,
            arguments=proposed.arguments,
        )

        step = (
            len(updated.actions_taken)
            + 1
        )

        produced_evidence_ids: list[str] = []

        # --------------------------------------------------------
        # Ejecutar una consulta correctamente NO significa que haya
        # producido evidencia positiva del estate. Un resultado vacío
        # sigue siendo útil y queda auditado en ActionRecord, pero no
        # debe convertirse en CaseEvidence sin registros fuente que lo
        # respalden.
        # --------------------------------------------------------

        if result.success and result.evidence_refs:

            evidence_id = (
                f"EV-ACTION-{step:04d}"
            )

            evidence = CaseEvidence(
                evidence_id=evidence_id,
                statement=result.summary,
                direction="neutral",
                source_refs=result.evidence_refs,
                produced_by=proposed.action_name,
            )

            updated.evidence.append(
                evidence
            )

            produced_evidence_ids.append(
                evidence_id
            )


        # --------------------------------------------------------
        # Siempre guardamos el intento, funcione o no.
        # --------------------------------------------------------

        action_record = ActionRecord(
            step=step,
            action_name=proposed.action_name,
            arguments=proposed.arguments,
            reason=proposed.reason,
            question_resolved=(
                proposed.question_resolved
            ),
            result_summary=result.summary,
            result_data=result.data,
            success=result.success,
            errors=result.errors,
            produced_evidence_ids=(
                produced_evidence_ids
            ),
        )

        updated.actions_taken.append(
            action_record
        )

        updated.status = "investigating"

        return updated

    # ========================================================
    # PEDIR CHALLENGER
    # ========================================================

    if decision.decision == "request_review":

        updated.status = (
            "ready_for_review"
        )

        return updated

    # ========================================================
    # PASAR A VERIFIER
    # ========================================================

    if (
        decision.decision
        == "ready_for_verification"
    ):

        updated.status = (
            "ready_for_verification"
        )

        return updated

    # ========================================================
    # CERRAR SIN CONCLUSIÓN
    # ========================================================

    if (
        decision.decision
        == "close_inconclusive"
    ):

        updated.status = "closed"

        return updated

    raise ValueError(
        "Unknown Investigator decision."
    )

def run_investigation_loop(
    case_state: CaseState,
    estate: EstateRepository,
    llm_client: LLMClient,
    max_steps: int = 6,
) -> InvestigationLoopResult:
    """
    Ejecuta varias iteraciones del Investigator.

    Ciclo:

        THINK
        ↓
        ACT
        ↓
        OBSERVE
        ↓
        UPDATE
        ↓
        THINK otra vez
    """

    if max_steps < 1:
        raise ValueError(
            "max_steps debe ser al menos 1."
        )

    current = case_state.model_copy(
        deep=True
    )

    decisions: list[
        InvestigatorDecision
    ] = []

    # También contamos acciones que el caso
    # ya hubiera ejecutado antes de entrar aquí.
    seen_actions = {
        _action_signature(
            record.action_name,
            record.arguments,
        )
        for record in current.actions_taken
    }

    for _ in range(max_steps):

        # ====================================================
        # 1. THINK
        # ====================================================

        decision = investigate_case(
            case_state=current,
            llm_client=llm_client,
        )

        decisions.append(
            decision
        )

        # ====================================================
        # 2. INVESTIGATE
        # ====================================================

        if decision.decision == "investigate":

            if decision.next_action is None:
                raise ValueError(
                    "El Investigator pidió investigar "
                    "pero no proporcionó next_action."
                )

            signature = _action_signature(
                decision.next_action.action_name,
                decision.next_action.arguments,
            )

            # -----------------------------------------------
            # Evitar repetición exacta
            # -----------------------------------------------

            if signature in seen_actions:

                return InvestigationLoopResult(
                    case_state=current,
                    decisions=decisions,
                    iterations=len(decisions),
                    stop_reason="repeated_action",
                )

            seen_actions.add(
                signature
            )

            current = apply_investigator_decision(
                case_state=current,
                decision=decision,
                estate=estate,
            )

            # Volvemos arriba.
            # El Investigator recibirá ahora
            # el CaseState actualizado.
            continue

        # ====================================================
        # 3. OTRAS DECISIONES
        # ====================================================

        current = apply_investigator_decision(
            case_state=current,
            decision=decision,
            estate=estate,
        )

        if decision.decision == "request_review":

            return InvestigationLoopResult(
                case_state=current,
                decisions=decisions,
                iterations=len(decisions),
                stop_reason="request_review",
            )

        if (
            decision.decision
            == "ready_for_verification"
        ):

            return InvestigationLoopResult(
                case_state=current,
                decisions=decisions,
                iterations=len(decisions),
                stop_reason=(
                    "ready_for_verification"
                ),
            )

        if (
            decision.decision
            == "close_inconclusive"
        ):

            return InvestigationLoopResult(
                case_state=current,
                decisions=decisions,
                iterations=len(decisions),
                stop_reason="close_inconclusive",
            )

    # ========================================================
    # Presupuesto agotado
    # ========================================================

    return InvestigationLoopResult(
        case_state=current,
        decisions=decisions,
        iterations=len(decisions),
        stop_reason="max_steps_reached",
    )