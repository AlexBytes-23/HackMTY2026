"""El Verifier sólo mira supporting_evidence_ids, y el LLM no puede mantener esa
lista por sí solo: los evidence_id de las acciones los acuña Python DESPUÉS de que
el modelo respondió, y una hipótesis nueva llega siempre con la lista vacía.

Estas pruebas fijan el contrato de contabilidad que evita que la evidencia del caso
quede huérfana, y también sus límites: hacer algo citable no es valorarlo.
"""

from __future__ import annotations

from src.core.models import (
    CaseEvidence,
    CaseState,
    EvidenceRef,
    Hypothesis,
    InvestigatorDecision,
    ProposedAction,
)
from src.investigation.runner import apply_investigator_decision


class OneInvoiceEstate:
    def get_vendor_invoices(self, vendor_rfc: str):
        return [{"uuid": "INV-001", "issuer_rfc": vendor_rfc, "total": 1000.0}]


def _seed_case(hypotheses=None):
    return CaseState(
        case_id="CASE-1",
        lead_id="LEAD-1",
        hypotheses=hypotheses or [],
        evidence=[
            CaseEvidence(
                evidence_id="EV-OBS-001",
                statement="El RFC aparece en la tabla EFOS suministrada.",
                direction="neutral",
                produced_by="deterministic_tabular",
                source_refs=[
                    EvidenceRef(source_table="efos_list", record_id="PHA010101AA1")
                ],
            )
        ],
    )


def _decision(hypotheses, *, action=True):
    return InvestigatorDecision(
        decision="investigate" if action else "request_review",
        current_assessment="Falta consultar las facturas.",
        hypotheses=hypotheses,
        next_action=(
            ProposedAction(
                action_name="get_vendor_invoices",
                arguments={"vendor_rfc": "PHA010101AA1"},
                reason="Establecer qué facturas emitió el RFC.",
                question_resolved="¿Qué facturas tiene este RFC?",
            )
            if action
            else None
        ),
        new_unknowns=[],
        resolved_unknowns=[],
        reason="La consulta aporta registros objetivos.",
    )


def _hypothesis(**overrides):
    base = dict(
        hypothesis_id="H-001",
        statement="Posible proveedor fantasma.",
        scheme_type="phantom_vendor",
        status="supported",
        supporting_evidence_ids=[],
        counter_evidence_ids=[],
    )
    base.update(overrides)
    return Hypothesis(**base)


# ==========================================================================
# HACER CITABLE LA EVIDENCIA SEMILLA
# ==========================================================================

def test_seed_evidence_becomes_citable_for_a_brand_new_hypothesis():
    """El LLM crea la hipótesis con la lista vacía; la evidencia del Lead no se pierde."""

    updated = apply_investigator_decision(
        case_state=_seed_case(),
        decision=_decision([_hypothesis()], action=False),
        estate=OneInvoiceEstate(),
    )

    hypothesis = updated.hypotheses[0]
    assert hypothesis.supporting_evidence_ids == ["EV-OBS-001"]


def test_action_evidence_becomes_citable_in_the_same_step():
    """El evidence_id se acuña después de la decisión, así que Python debe registrarlo."""

    updated = apply_investigator_decision(
        case_state=_seed_case(),
        decision=_decision([_hypothesis()]),
        estate=OneInvoiceEstate(),
    )

    hypothesis = updated.hypotheses[0]
    assert hypothesis.supporting_evidence_ids == ["EV-OBS-001", "EV-ACTION-0001"]

    # Y queda por escrito en el rastro auditable.
    assert "H-001" in updated.actions_taken[0].result_summary


# ==========================================================================
# LÍMITES
# ==========================================================================

def test_explicit_counter_evidence_is_never_moved_into_support():
    """Si el LLM la declaró contraria, la contabilidad automática no la reinterpreta."""

    hypothesis = _hypothesis(counter_evidence_ids=["EV-OBS-001"])

    updated = apply_investigator_decision(
        case_state=_seed_case(),
        decision=_decision([hypothesis], action=False),
        estate=OneInvoiceEstate(),
    )

    result = updated.hypotheses[0]
    assert "EV-OBS-001" not in result.supporting_evidence_ids
    assert result.counter_evidence_ids == ["EV-OBS-001"]


def test_closed_hypotheses_do_not_accumulate_new_citations():
    """Una hipótesis rechazada o inconclusa no se reanima con evidencia nueva."""

    case = _seed_case(
        hypotheses=[
            _hypothesis(hypothesis_id="H-REJ", status="rejected"),
            _hypothesis(hypothesis_id="H-INC", status="inconclusive"),
        ]
    )

    updated = apply_investigator_decision(
        case_state=case,
        decision=_decision([], action=True),
        estate=OneInvoiceEstate(),
    )

    by_id = {h.hypothesis_id: h for h in updated.hypotheses}
    assert by_id["H-REJ"].supporting_evidence_ids == []
    assert by_id["H-INC"].supporting_evidence_ids == []


def test_evidence_is_only_made_citable_never_treated_as_support():
    """Hacer citable no es valorar: la dirección registrada sigue siendo neutral."""

    updated = apply_investigator_decision(
        case_state=_seed_case(),
        decision=_decision([_hypothesis()]),
        estate=OneInvoiceEstate(),
    )

    action_evidence = next(
        e for e in updated.evidence if e.evidence_id == "EV-ACTION-0001"
    )
    assert action_evidence.direction == "neutral"


# ==========================================================================
# LA UNIÓN NO BORRA HISTORIA
# ==========================================================================

def test_llm_echoing_an_empty_list_cannot_erase_recorded_citations():
    """El prompt del Investigator muestra supporting_evidence_ids: [] como ejemplo.

    Si el modelo lo copia, la evidencia acumulada no debe desaparecer.
    """

    case = _seed_case(
        hypotheses=[
            _hypothesis(
                supporting_evidence_ids=["EV-OBS-001", "EV-ACTION-0001"],
            )
        ]
    )

    updated = apply_investigator_decision(
        case_state=case,
        # El LLM devuelve la misma hipótesis con la lista vacía.
        decision=_decision([_hypothesis(supporting_evidence_ids=[])], action=False),
        estate=OneInvoiceEstate(),
    )

    hypothesis = updated.hypotheses[0]
    assert hypothesis.supporting_evidence_ids == ["EV-OBS-001", "EV-ACTION-0001"]


def test_the_llm_can_still_reformulate_a_hypothesis():
    """La unión protege las citas, no congela el contenido de la hipótesis."""

    case = _seed_case(
        hypotheses=[_hypothesis(supporting_evidence_ids=["EV-OBS-001"])]
    )

    updated = apply_investigator_decision(
        case_state=case,
        decision=_decision(
            [
                _hypothesis(
                    statement="Reformulada tras nueva evidencia.",
                    scheme_type="kickback",
                    status="inconclusive",
                    supporting_evidence_ids=[],
                )
            ],
            action=False,
        ),
        estate=OneInvoiceEstate(),
    )

    hypothesis = updated.hypotheses[0]
    assert hypothesis.statement == "Reformulada tras nueva evidencia."
    assert hypothesis.scheme_type == "kickback"
    assert hypothesis.status == "inconclusive"
    assert hypothesis.supporting_evidence_ids == ["EV-OBS-001"]


def test_a_new_alternative_hypothesis_does_not_overwrite_the_original():
    case = _seed_case(
        hypotheses=[_hypothesis(supporting_evidence_ids=["EV-OBS-001"])]
    )

    updated = apply_investigator_decision(
        case_state=case,
        decision=_decision(
            [_hypothesis(hypothesis_id="H-002", scheme_type="round_tripping")],
            action=False,
        ),
        estate=OneInvoiceEstate(),
    )

    by_id = {h.hypothesis_id: h for h in updated.hypotheses}
    assert set(by_id) == {"H-001", "H-002"}
    assert by_id["H-001"].scheme_type == "phantom_vendor"
    assert by_id["H-002"].scheme_type == "round_tripping"
