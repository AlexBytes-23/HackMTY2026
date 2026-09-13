from __future__ import annotations

from collections import defaultdict

from src.core.models import Lead, Observation


def build_leads(
    observations: list[Observation],
) -> list[Lead]:
    """
    Convierte observaciones en Leads investigables.

    Una Observation es una señal.
    Un Lead significa que ya existe suficiente razón
    para abrir una investigación.

    Esta función NO determina fraude y NO asigna
    automáticamente un scheme_type.
    """

    if not observations:
        return []

    grouped: dict[str, list[Observation]] = defaultdict(list)

    # Primera agrupación sencilla:
    # observaciones cuyo sujeto principal coincide.
    #
    # Es una decisión provisional. Después podremos
    # separar varios leads de una misma entidad.
    for observation in observations:

        if observation.entities:
            subject = observation.entities[0]
        else:
            subject = "UNKNOWN_SUBJECT"

        grouped[subject].append(observation)

    leads: list[Lead] = []

    for index, (subject, group) in enumerate(
        sorted(grouped.items()),
        start=1,
    ):

        observation_ids = [
            obs.observation_id
            for obs in group
        ]

        entities: list[str] = []

        for obs in group:
            for entity in obs.entities:
                if entity not in entities:
                    entities.append(entity)

        questions: list[str] = []

        # Las observaciones pueden sugerir comprobaciones
        # concretas.
        for obs in group:
            for check in obs.recommended_checks:

                if check not in questions:
                    questions.append(check)

        # Y añadimos tres preguntas que nunca queremos
        # olvidar al abrir un caso.
        generic_questions = [
            (
                "¿Existe una explicación legítima que "
                "produzca las mismas observaciones?"
            ),
            (
                "¿Qué evidencia permitiría distinguir "
                "entre una explicación legítima y una irregular?"
            ),
            (
                "¿Existe evidencia que contradiga o debilite "
                "la interpretación sospechosa?"
            ),
        ]

        for question in generic_questions:
            if question not in questions:
                questions.append(question)

        signal_types = sorted({
            obs.signal_type
            for obs in group
        })

        reason = (
            f"Se observaron {len(group)} señal(es) "
            f"relacionadas con {subject}: "
            + ", ".join(signal_types)
            + ". Las señales justifican investigación, "
              "pero no constituyen una acusación."
        )

        leads.append(
            Lead(
                lead_id=f"LEAD-{index:04d}",
                subject_entities=entities,
                observation_ids=observation_ids,
                reason_opened=reason,
                open_questions=questions,
            )
        )

    return leads