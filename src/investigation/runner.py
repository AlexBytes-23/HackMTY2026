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

def _merge_evidence_ids(
    existing_ids: list[str],
    incoming_ids: list[str],
) -> list[str]:
    """
    Une dos listas de evidence_ids preservando
    el orden y sin duplicar.
    """

    merged = list(existing_ids)

    for evidence_id in incoming_ids:

        if evidence_id not in merged:
            merged.append(evidence_id)

    return merged


def _merge_hypotheses(
    existing: list[Hypothesis],
    incoming: list[Hypothesis],
) -> list[Hypothesis]:
    """
    Actualiza hipótesis por ID sin borrar
    silenciosamente hipótesis anteriores.

    El Investigator SÍ puede reformular el enunciado,
    el scheme_type o el status de una hipótesis.

    Lo que NO puede hacer es des-citar evidencia que
    Python ya registró: omitir un evidence_id en la
    respuesta del LLM no es una decisión forense, es
    una omisión. Por eso las listas de evidencia se
    unen en lugar de reemplazarse.
    """

    merged = {
        hypothesis.hypothesis_id:
            hypothesis.model_copy(deep=True)
        for hypothesis in existing
    }

    for hypothesis in incoming:

        updated_hypothesis = (
            hypothesis.model_copy(deep=True)
        )

        previous = merged.get(
            hypothesis.hypothesis_id
        )

        if previous is not None:

            updated_hypothesis.supporting_evidence_ids = (
                _merge_evidence_ids(
                    previous.supporting_evidence_ids,
                    updated_hypothesis.supporting_evidence_ids,
                )
            )

            updated_hypothesis.counter_evidence_ids = (
                _merge_evidence_ids(
                    previous.counter_evidence_ids,
                    updated_hypothesis.counter_evidence_ids,
                )
            )

        merged[hypothesis.hypothesis_id] = (
            updated_hypothesis
        )

    return list(merged.values())


def _make_case_evidence_citable(
    case_state: CaseState,
) -> list[str]:
    """
    Garantiza que toda evidencia del caso sea CITABLE
    por las hipótesis vivas del caso.

    Por qué hace falta:

    El Verifier sólo mira supporting_evidence_ids. Pero
    el LLM no puede mantener esa lista por sí solo:

    - Los evidence_id de las acciones los acuña Python
      DESPUÉS de que el Investigator decidió, así que el
      ID todavía no existe cuando el modelo responde.
    - La evidencia semilla (las observaciones que abrieron
      el Lead) se pierde en cuanto el modelo devuelve una
      hipótesis nueva con la lista vacía.

    En ambos casos la evidencia quedaba huérfana y el caso
    moría en el gate por "fewer than 3 valid unique exhibits",
    aunque los registros estuvieran ahí.

    Qué NO es esto:

    No es una autorización, ni una valoración, ni una
    afirmación de que el registro sostenga la hipótesis.
    Sólo lo vuelve citable. El verificador determinista
    sigue siendo el único que decide si el registro prueba
    algo, y el gate sigue fallando cerrado.

    Se respeta la designación explícita del LLM: si ya marcó
    una evidencia como contraria, no se mueve a soporte.

    Devuelve los hypothesis_id afectados, para el rastro
    auditable.
    """

    all_evidence_ids = [
        evidence.evidence_id
        for evidence in case_state.evidence
    ]

    attached_to: list[str] = []

    for hypothesis in case_state.hypotheses:

        if hypothesis.status not in {"open", "supported"}:
            continue

        changed = False

        for evidence_id in all_evidence_ids:

            if evidence_id in hypothesis.supporting_evidence_ids:
                continue

            # El LLM la declaró contraria: se queda ahí.
            if evidence_id in hypothesis.counter_evidence_ids:
                continue

            hypothesis.supporting_evidence_ids.append(
                evidence_id
            )

            changed = True

        if changed:
            attached_to.append(
                hypothesis.hypothesis_id
            )

    return attached_to


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

    # Una hipótesis nueva llega con la lista de evidencia vacía,
    # así que la evidencia semilla del Lead quedaría huérfana.
    # Ver _make_case_evidence_citable.
    _make_case_evidence_citable(updated)

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

        attached_to_hypotheses: list[str] = []

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
            # Hacer citable lo que acabamos de obtener.
            # Ver _make_case_evidence_citable.
            # --------------------------------------------------------

            attached_to_hypotheses = (
                _make_case_evidence_citable(
                    updated
                )
            )


        # --------------------------------------------------------
        # Siempre guardamos el intento, funcione o no.
        # --------------------------------------------------------

        # Dejamos por escrito a qué hipótesis quedó citada
        # la evidencia. El rastro debe permitir reconstruir
        # por qué el Verifier vio un exhibit concreto.
        result_summary = result.summary

        if attached_to_hypotheses:
            result_summary = (
                f"{result_summary} "
                "Registro citable para la(s) hipótesis "
                f"{', '.join(attached_to_hypotheses)}."
            )

        action_record = ActionRecord(
            step=step,
            action_name=proposed.action_name,
            arguments=proposed.arguments,
            reason=proposed.reason,
            question_resolved=(
                proposed.question_resolved
            ),
            result_summary=result_summary,
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