from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.core.models import Lead, Observation
from src.output.formatters import with_entity_prefix

# Sólo estos prefijos pueden salir del sistema. `CLABE:` es interno: identifica
# una cuenta, no a una persona ni a una empresa, y nunca debe aparecer en una
# submission ni en un case file.
OFFICIAL_ENTITY_PREFIXES = ("RFC:", "EMP:")


def _clabe_owners(
    clabe: str,
    estate: Any,
) -> list[str]:
    """
    Devuelve las entidades oficiales que el estate registra como dueñas
    de esa cuenta.

    Resolver una CLABE establece la titularidad de ESA cuenta y nada más.
    Si el estate no registra dueño, no se inventa ninguno: la lista sale
    vacía y la CLABE simplemente no se reporta.
    """

    owners: list[str] = []

    if not clabe or estate is None:
        return owners

    find_vendor = getattr(estate, "find_vendor_by_clabe", None)
    if callable(find_vendor):
        vendor = find_vendor(clabe)
        if vendor and str(vendor.get("rfc", "")).strip():
            owners.append(with_entity_prefix("RFC:", str(vendor["rfc"])))

    find_employee = getattr(estate, "find_employee_by_clabe", None)
    if callable(find_employee):
        employee = find_employee(clabe)
        if employee and str(employee.get("emp_id", "")).strip():
            owners.append(with_entity_prefix("EMP:", str(employee["emp_id"])))

    return owners


def official_subject_entities(
    observations: list[Observation],
    estate: Any = None,
) -> list[str]:
    """
    Entidades oficiales (`RFC:` / `EMP:`) de un grupo de observaciones,
    en orden de aparición y sin repetir.

    Una CLABE se sustituye por su titular según el estate suministrado.
    Si no hay estate, o la cuenta no tiene dueño registrado, la CLABE se
    descarta: se usa la siguiente entidad oficial de la observación, y si
    no hay ninguna la lista queda vacía. Nunca se emite la cuenta.
    """

    entities: list[str] = []

    for observation in observations:
        for entity in observation.entities:

            candidates: list[str] = []

            if entity.startswith(OFFICIAL_ENTITY_PREFIXES):
                if len(entity) > len("RFC:"):
                    candidates.append(entity)

            elif entity.startswith("CLABE:"):
                candidates.extend(
                    _clabe_owners(
                        entity[len("CLABE:"):].strip(),
                        estate,
                    )
                )

            for candidate in candidates:
                if candidate not in entities:
                    entities.append(candidate)

    return entities


def build_leads(
    observations: list[Observation],
    estate: Any = None,
) -> list[Lead]:
    """
    Convierte observaciones en Leads investigables.

    Una Observation es una señal.
    Un Lead significa que ya existe suficiente razón
    para abrir una investigación.

    Esta función NO determina fraude y NO asigna
    automáticamente un scheme_type.

    `estate` es opcional y sólo se usa para resolver una CLABE a su titular,
    de modo que `subject_entities` sólo contenga entidades oficiales.
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

        entities = official_subject_entities(group, estate)

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

        # `subject` es la clave de agrupación y puede ser una CLABE, así que
        # nunca se escribe aquí: el texto usa la entidad oficial resuelta.
        described = (
            entities[0]
            if entities
            else (
                "una cuenta sin titular registrado en el estate suministrado"
            )
        )

        reason = (
            f"Se observaron {len(group)} señal(es) "
            f"relacionadas con {described}: "
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