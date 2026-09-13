from __future__ import annotations

from src.core.models import (
    CaseEvidence,
    CaseState,
    Lead,
    Observation,
)


def build_case_state(
    lead: Lead,
    observations: list[Observation],
    case_id: str | None = None,
) -> CaseState:
    """
    Convierte un Lead en el estado inicial
    de una investigación.

    Observation
        ↓
       Lead
        ↓
    CaseState

    No crea acusaciones ni decide scheme_type.
    """

    # ========================================================
    # 1. INDEXAR OBSERVATIONS
    # ========================================================

    observation_by_id: dict[str, Observation] = {}

    for observation in observations:

        if observation.observation_id in observation_by_id:
            raise ValueError(
                "Observation ID duplicado: "
                f"{observation.observation_id}"
            )

        observation_by_id[
            observation.observation_id
        ] = observation

    # ========================================================
    # 2. RECUPERAR SÓLO LAS OBSERVATIONS DEL LEAD
    # ========================================================

    selected_observations: list[Observation] = []
    missing_ids: list[str] = []

    for observation_id in lead.observation_ids:

        observation = observation_by_id.get(
            observation_id
        )

        if observation is None:
            missing_ids.append(
                observation_id
            )
            continue

        selected_observations.append(
            observation
        )

    # Si un Lead dice que depende de OBS-005,
    # pero OBS-005 desapareció, NO construimos
    # silenciosamente un caso incompleto.
    if missing_ids:
        raise ValueError(
            "El Lead referencia Observations "
            "que no fueron proporcionadas: "
            f"{missing_ids}"
        )

    # ========================================================
    # 3. CREAR EVIDENCIA INICIAL
    # ========================================================

    initial_evidence: list[CaseEvidence] = []

    for observation in selected_observations:

        evidence_id = (
            f"EV-{observation.observation_id}"
        )

        initial_evidence.append(
            CaseEvidence(
                evidence_id=evidence_id,
                statement=observation.statement,

                # Importante:
                # todavía no existe una hipótesis concreta
                # que esta evidencia apoye o contradiga.
                direction="neutral",

                source_refs=observation.evidence,

                produced_by=(
                    observation.detector_name
                ),

                observation_id=(
                    observation.observation_id
                ),
            )
        )

    # ========================================================
    # 4. CREAR CASE ID
    # ========================================================

    if case_id is None:
        case_id = (
            f"CASE-{lead.lead_id}"
        )

    # ========================================================
    # 5. CONSTRUIR EL CASE STATE
    # ========================================================

    return CaseState(
        case_id=case_id,
        lead_id=lead.lead_id,

        lead_reason=lead.reason_opened,

        subject_entities=(
            lead.subject_entities.copy()
        ),

        observations=[
            observation.model_copy(
                deep=True
            )
            for observation
            in selected_observations
        ],

        evidence=initial_evidence,

        unknowns=(
            lead.open_questions.copy()
        ),

        status="open",
    )