"""Pre-analisis determinista que se entrega a los criticos adversariales.

Por que existe este modulo
==========================

"AI interprets. Python verifies. Evidence decides."

Los dos criticos (Challenger y Method Critic) recibian el ``CaseState`` completo
volcado a JSON y se esperaba que *notaran* a ojo cosas que en realidad son
aritmetica o estructura: cuantos registros distintos sostienen la hipotesis, si
tres detectores se apoyan en la misma transaccion, cuanto varian los montos de un
ciclo, si un score trae o no su ``score_semantics``.

Pedirle eso a un LLM sobre un volcado grande es exactamente donde falla. Aqui
Python lo calcula primero y le entrega al critico hechos numerados. El critico
sigue decidiendo -- este modulo nunca emite un veredicto, nunca dice "fraude" y
nunca propone un outcome.

Reglas que este modulo respeta
------------------------------

* No lee ground truth ni nada fuera del ``CaseState``.
* No consulta el estate: sólo interpreta lo que los detectores ya registraron.
* Determinista: todo iterable sale ordenado con clave explicita.
* Ausencia en el estate NO es ausencia en la realidad. Cuando reporta que algo
  no se encontro, lo dice con esas palabras.
* Un score nunca se convierte en probabilidad de fraude.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.core.models import CaseState, Observation


# Un LLM que quiere decir "sin scheme_type" escribe la CADENA "null" con una
# frecuencia alta, y a veces "none" o "". Eso es un artefacto de serializacion,
# no una decision del modelo: ninguna de esas cadenas es un scheme_type valido y
# todas significan lo mismo que JSON null. Normalizarlas NO debilita el contrato
# -- el enum sigue rechazando cualquier valor que no sea uno de los cinco
# esquemas oficiales. Dejarlo sin normalizar hacia que el caso completo muriera
# con un error de ejecucion, que no es ni un hallazgo ni una declinacion
# razonada: es un hueco en el expediente.
_NULL_LIKE_STRINGS = {"null", "none", "nil", "n/a", "na", ""}


def normalize_optional_enum(value: Any) -> Any:
    """Convierte las cadenas que un LLM usa para decir "nada" en ``None`` real."""

    if isinstance(value, str) and value.strip().lower() in _NULL_LIKE_STRINGS:
        return None
    return value


# Señales que los detectores emiten hoy. Se nombran explicitamente para que el
# analisis por señal falle de forma visible si un detector cambia su signal_type,
# en lugar de dejar de analizar en silencio.
CYCLE_SIGNAL = "directed_bank_transfer_cycle"
SHARED_IDENTIFIER_SIGNALS = (
    "vendor_employee_shared_clabe",
    "shared_vendor_clabe",
)
TRANSFER_SIGNAL = "vendor_to_employee_bank_transfer"
CLUSTER_SIGNAL = "short_window_similar_invoice_cluster"


# ==========================================================================
# INDEPENDENCIA DE LA EVIDENCIA
# ==========================================================================

def analyze_evidence_independence(case_state: CaseState) -> dict[str, Any]:
    """Cuantifica cuanta evidencia *distinta* sostiene realmente el caso.

    La pregunta que responde no es "cuantos items de evidencia hay" sino
    "cuantos registros del estate hay debajo". Tres detectores que disparan
    sobre la misma transaccion producen tres items y un solo hecho.
    """

    record_to_evidence: dict[tuple[str, str], set[str]] = {}
    record_to_detector: dict[tuple[str, str], set[str]] = {}

    for evidence in case_state.evidence:
        for ref in evidence.source_refs:
            key = (ref.source_table, str(ref.record_id))
            record_to_evidence.setdefault(key, set()).add(evidence.evidence_id)
            if evidence.produced_by:
                record_to_detector.setdefault(key, set()).add(evidence.produced_by)

    # Las observaciones tambien citan registros, y son la via por la que varios
    # detectores acaban apoyandose en la misma transaccion.
    for observation in case_state.observations:
        for ref in observation.evidence:
            key = (ref.source_table, str(ref.record_id))
            record_to_detector.setdefault(key, set()).add(observation.detector_name)

    shared_records = [
        {
            "source_table": table,
            "record_id": record_id,
            "evidence_ids": sorted(record_to_evidence.get((table, record_id), set())),
            "detectors": sorted(record_to_detector.get((table, record_id), set())),
            "detector_count": len(record_to_detector.get((table, record_id), set())),
        }
        for (table, record_id) in sorted(record_to_detector)
        if len(record_to_detector.get((table, record_id), set())) > 1
        or len(record_to_evidence.get((table, record_id), set())) > 1
    ]

    distinct_records = sorted(set(record_to_evidence) | set(record_to_detector))

    return {
        "distinct_underlying_records": len(distinct_records),
        "evidence_item_count": len(case_state.evidence),
        "observation_count": len(case_state.observations),
        "records_supporting_more_than_one_signal": shared_records,
        "note": (
            "evidence_item_count and observation_count are NOT measures of "
            "corroboration. distinct_underlying_records is the number of separate "
            "facts in the supplied estate. Where records_supporting_more_than_one_signal "
            "is non-empty, those signals are derived from the same record and are not "
            "independent of each other."
        ),
    }


# ==========================================================================
# AFIRMACIONES DE AUSENCIA
# ==========================================================================

def analyze_absence_claims(case_state: CaseState) -> dict[str, Any]:
    """Lista las consultas que se ejecutaron bien y no devolvieron ningun registro.

    Estas son precisamente las que se convierten en "no existe contrato" cuando
    lo unico que se puede afirmar es "no se encontro contrato en el estate
    suministrado".
    """

    empty_queries = [
        {
            "step": record.step,
            "action_name": record.action_name,
            "arguments": dict(sorted(record.arguments.items())),
            "question_it_was_meant_to_answer": record.question_resolved,
            "what_may_be_concluded": (
                "No matching record was found in the SUPPLIED ESTATE. This does not "
                "establish that no such record exists in reality, and it does not "
                "establish that the estate is complete."
            ),
        }
        for record in sorted(case_state.actions_taken, key=lambda r: r.step)
        if record.success and not record.produced_evidence_ids
    ]

    failed_queries = [
        {
            "step": record.step,
            "action_name": record.action_name,
            "errors": list(record.errors),
            "what_may_be_concluded": (
                "This query did not complete. Its silence is an execution outcome, "
                "not evidence about the subject."
            ),
        }
        for record in sorted(case_state.actions_taken, key=lambda r: r.step)
        if not record.success
    ]

    return {
        "successful_queries_returning_no_record": empty_queries,
        "queries_that_failed_to_execute": failed_queries,
    }


# ==========================================================================
# CONTINUIDAD DE CICLOS
# ==========================================================================

def _parse_iso(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def analyze_cycle_continuity(case_state: CaseState) -> list[dict[str, Any]]:
    """Mide, por ciclo detectado, que tan lejos esta de un same-money round trip.

    Un ciclo dirigido prueba que existen esas transferencias. NO prueba que el
    mismo dinero regreso. Los dos discriminadores son aritmetica pura:

    * dispersion relativa de los montos -- legs casi iguales son compatibles con
      un mismo importe moviendose; legs que difieren varias veces no lo son;
    * separacion temporal -- dias son compatibles, meses lo son mucho menos.

    Este modulo calcula ambos y NO fija un umbral. No existe un umbral legal ni
    interno que podamos citar, asi que inventarlo seria fabricar una regla. El
    critico juzga con los numeros a la vista.
    """

    results: list[dict[str, Any]] = []

    for observation in sorted(
        case_state.observations, key=lambda o: o.observation_id
    ):
        if observation.signal_type != CYCLE_SIGNAL:
            continue

        facts = observation.facts or {}
        raw_amounts = facts.get("transaction_amounts") or []
        amounts = [
            float(a) for a in raw_amounts if isinstance(a, (int, float))
        ]
        parsed_dates = [d for d in (_parse_iso(x) for x in (facts.get("transaction_dates") or [])) if d]

        entry: dict[str, Any] = {
            "observation_id": observation.observation_id,
            "account_cycle": list(facts.get("account_cycle") or []),
            "transaction_ids": list(facts.get("transaction_ids") or []),
            "leg_count": len(amounts),
            "amounts": sorted(amounts),
        }

        if amounts and min(amounts) > 0:
            entry["largest_to_smallest_leg_ratio"] = round(
                max(amounts) / min(amounts), 4
            )
            entry["relative_amount_spread"] = round(
                (max(amounts) - min(amounts)) / max(amounts), 4
            )
        else:
            entry["largest_to_smallest_leg_ratio"] = None
            entry["relative_amount_spread"] = None

        if len(parsed_dates) >= 2:
            entry["date_span_days"] = (max(parsed_dates) - min(parsed_dates)).days
        else:
            entry["date_span_days"] = facts.get("date_span_days")

        entry["what_this_does_not_establish"] = (
            "A directed cycle establishes that each transfer is recorded in the "
            "supplied estate. It does not establish that the same funds traversed "
            "the cycle. Legs that differ substantially in amount, or that are "
            "separated by long periods, are weaker support for a same-money "
            "interpretation. No threshold is applied here because none is supplied "
            "by the Rule Registry."
        )
        entry["ordering_limitation"] = (
            "The estate carries dates without intraday timestamps, so transfers "
            "sharing a date cannot be ordered within that day from this estate alone."
        )

        results.append(entry)

    return results


# ==========================================================================
# IDENTIFICADORES COMPARTIDOS
# ==========================================================================

def analyze_shared_identifier_claims(case_state: CaseState) -> list[dict[str, Any]]:
    """Separa "comparten un identificador" de "existe un flujo entre las partes".

    Un CLABE compartido entre vendor y employee es un hecho del estate. No
    establece propiedad, control ni colusion, y sobre todo no implica que haya
    habido una transferencia entre ellos. Aqui se dice explicitamente si en el
    caso hay o no una observacion de transferencia que acompañe al identificador.
    """

    transfer_pairs: set[tuple[str, str]] = set()
    for observation in case_state.observations:
        if observation.signal_type != TRANSFER_SIGNAL:
            continue
        facts = observation.facts or {}
        rfc = facts.get("rfc") or facts.get("vendor_rfc")
        emp = facts.get("emp_id")
        if rfc and emp:
            transfer_pairs.add((str(rfc), str(emp)))

    results: list[dict[str, Any]] = []

    for observation in sorted(
        case_state.observations, key=lambda o: o.observation_id
    ):
        if observation.signal_type not in SHARED_IDENTIFIER_SIGNALS:
            continue

        facts = observation.facts or {}
        rfc = facts.get("rfc") or facts.get("vendor_rfc")
        emp = facts.get("emp_id")

        has_transfer = bool(rfc and emp and (str(rfc), str(emp)) in transfer_pairs)

        results.append(
            {
                "observation_id": observation.observation_id,
                "signal_type": observation.signal_type,
                "entities": sorted(observation.entities),
                "a_transfer_between_these_parties_is_also_observed": has_transfer,
                "what_this_does_not_establish": (
                    "A shared identifier is a fact about the supplied estate. It does "
                    "not establish common ownership, control, or collusion, and it does "
                    "not establish that value moved between the parties. A vendor that "
                    "IS the employee (persona fisica con actividad empresarial) shares "
                    "an account legitimately."
                ),
                "discriminating_question": (
                    "Which records in the estate would distinguish a shared account "
                    "arising from an improper relationship from one arising from a "
                    "legitimate single-person supplier?"
                ),
            }
        )

    return results


# ==========================================================================
# SCORES
# ==========================================================================

def analyze_scores(case_state: CaseState) -> list[dict[str, Any]]:
    """Lista cada observacion con score y su semantica declarada.

    Un score sin ``score_semantics`` no puede interpretarse y se marca como tal.
    Ningun score es una probabilidad de fraude.
    """

    results: list[dict[str, Any]] = []

    for observation in sorted(
        case_state.observations, key=lambda o: o.observation_id
    ):
        if observation.score is None:
            continue

        results.append(
            {
                "observation_id": observation.observation_id,
                "detector_name": observation.detector_name,
                "score": observation.score,
                "score_semantics": observation.score_semantics,
                "semantics_declared": bool(observation.score_semantics),
                "what_this_does_not_establish": (
                    "This value is not a calibrated probability of fraud. It may not be "
                    "read as a percentage likelihood, compared across detectors, or "
                    "summed. If score_semantics is absent the number cannot be "
                    "interpreted at all and must not support a conclusion."
                ),
            }
        )

    return results


# ==========================================================================
# ALTERNATIVAS QUE LOS DETECTORES YA DECLARARON
# ==========================================================================

def collect_declared_alternatives(case_state: CaseState) -> list[dict[str, Any]]:
    """Recoge lo que cada detector ya declaro sobre sus propios limites.

    Cada ``Observation`` trae ``limitations``, ``legitimate_alternatives`` y
    ``recommended_checks`` escritos por quien construyo el detector. El Challenger
    tenia esa informacion enterrada en el volcado del CaseState; aqui se le
    entrega al frente, porque es literalmente el punto de partida de su trabajo:
    la explicacion legitima mas fuerte suele estar ya escrita ahi.
    """

    results: list[dict[str, Any]] = []

    for observation in sorted(
        case_state.observations, key=lambda o: o.observation_id
    ):
        if not (
            observation.legitimate_alternatives
            or observation.limitations
            or observation.recommended_checks
        ):
            continue

        results.append(
            {
                "observation_id": observation.observation_id,
                "signal_type": observation.signal_type,
                "statement": observation.statement,
                "legitimate_alternatives_declared_by_the_detector": list(
                    observation.legitimate_alternatives
                ),
                "limitations_declared_by_the_detector": list(observation.limitations),
                "checks_the_detector_recommended": list(observation.recommended_checks),
            }
        )

    return results


# ==========================================================================
# BRIEFING
# ==========================================================================

def build_case_briefing(case_state: CaseState) -> dict[str, Any]:
    """Ensambla el pre-analisis completo para cualquiera de los dos criticos."""

    return {
        "_what_this_is": (
            "Deterministic pre-analysis computed in Python from the CaseState only. "
            "It contains no verdict, no probability and no accusation. Treat every "
            "number here as established; treat every interpretation of it as yours "
            "to make."
        ),
        "evidence_independence": analyze_evidence_independence(case_state),
        "absence_claims": analyze_absence_claims(case_state),
        "cycle_continuity": analyze_cycle_continuity(case_state),
        "shared_identifier_claims": analyze_shared_identifier_claims(case_state),
        "scores": analyze_scores(case_state),
    }
