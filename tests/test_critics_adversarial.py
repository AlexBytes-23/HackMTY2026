"""Casos adversariales de juicio para el Challenger y el Method Critic.

Que prueban y que NO prueban
----------------------------

No se puede afirmar en una prueba determinista que un LLM emitira el outcome
correcto. Lo que SI se puede probar, y es lo que hace este archivo:

1. que el contexto que recibe cada critico contiene los hechos necesarios para
   decidir bien -- si el dato no llega, el critico no puede acertar salvo por suerte;
2. que el prompt instruye explicitamente sobre cada trampa;
3. que cuando un critico emite un outcome, los validadores deterministas exigen
   que venga acompañado de lo que ese outcome requiere;
4. que un outcome no puede colarse sin su respaldo (por ejemplo `clear` con
   objeciones materiales, o `needs_more_evidence` sin accion ejecutable).

Cada caso esta nombrado por la trampa que representa.
"""

from __future__ import annotations

import json

import pytest

from src.agents.challenger import (
    CHALLENGER_SYSTEM_PROMPT,
    ChallengerReview,
    build_challenger_prompt,
    challenge_case,
    validate_challenger_review,
)
from src.agents.method_critic import (
    METHOD_CRITIC_PROMPT,
    MethodCriticReview,
    build_method_critic_prompt,
    criticize_method,
)
from src.core.models import (
    ActionRecord,
    CaseEvidence,
    CaseState,
    EvidenceRef,
    Hypothesis,
    Observation,
    ProposedAction,
)
from src.investigation.action_bank import list_available_actions


TARGET = "H-001"


class _Capture:
    """Cliente LLM que captura el prompt y devuelve una respuesta fija."""

    provider = "test"
    model = "test-1"

    def __init__(self, response: dict):
        self.response = response
        self.system_prompt: str | None = None
        self.user_prompt: str | None = None

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return json.dumps(self.response)


def _case(
    *,
    observations=None,
    evidence=None,
    actions=None,
    scheme_type="phantom_vendor",
) -> CaseState:
    return CaseState(
        case_id="CASE-1",
        lead_id="LEAD-1",
        hypotheses=[
            Hypothesis(
                hypothesis_id=TARGET,
                statement="hipotesis bajo revision",
                scheme_type=scheme_type,
                status="supported",
                supporting_evidence_ids=[e.evidence_id for e in (evidence or [])],
            )
        ],
        observations=observations or [],
        evidence=evidence or [],
        actions_taken=actions or [],
    )


def _survives(**overrides) -> dict:
    base = {
        "target_hypothesis_id": TARGET,
        "outcome": "survives",
        "reasoning_summary": "sin objecion material pendiente",
    }
    base.update(overrides)
    return base


def _clear(**overrides) -> dict:
    base = {
        "target_hypothesis_id": TARGET,
        "outcome": "clear",
        "reasoning_summary": "metodo defendible",
    }
    base.update(overrides)
    return base


def _challenger_payload(case_state: CaseState) -> dict:
    _, user_prompt = build_challenger_prompt(
        case_state=case_state,
        target_hypothesis_id=TARGET,
        available_actions=list_available_actions(),
    )
    return json.loads(user_prompt)


def _method_payload(case_state: CaseState) -> dict:
    prompt = build_method_critic_prompt(
        case_state, TARGET, list_available_actions(), None
    )
    return json.loads(prompt.split("=== CONTEXT ===", 1)[1])


# ==========================================================================
# CASO 1 — 3 detectores derivados de una sola transaccion
# ==========================================================================

def test_three_detectors_one_transaction_reaches_both_critics_as_one_fact():
    ref = EvidenceRef(source_table="bank_txns", record_id="BNK-1")
    case = _case(
        observations=[
            Observation(
                observation_id=f"OBS-{i}",
                detector_name=f"detector_{i}",
                signal_type=sig,
                statement="s",
                evidence=[ref],
            )
            for i, sig in enumerate(
                (
                    "vendor_to_employee_bank_transfer",
                    "directed_bank_transfer_cycle",
                    "shared_vendor_clabe",
                ),
                start=1,
            )
        ],
        evidence=[
            CaseEvidence(
                evidence_id=f"EV-{i}",
                statement="s",
                direction="for",
                produced_by=f"detector_{i}",
                source_refs=[ref],
            )
            for i in (1, 2, 3)
        ],
    )

    for payload in (_method_payload(case), _challenger_payload(case)):
        independence = payload["deterministic_pre_analysis"]["evidence_independence"]
        assert independence["evidence_item_count"] == 3
        assert independence["distinct_underlying_records"] == 1
        assert independence["records_supporting_more_than_one_signal"]

    assert "NOT corroboration" in METHOD_CRITIC_PROMPT
    assert "same record counted twice" in METHOD_CRITIC_PROMPT


# ==========================================================================
# CASO 2 — vendor y employee comparten CLABE pero no hay transferencia
# ==========================================================================

def test_shared_clabe_without_transfer_is_flagged_to_both_critics():
    case = _case(
        observations=[
            Observation(
                observation_id="OBS-CLABE",
                detector_name="deterministic_tabular",
                signal_type="vendor_employee_shared_clabe",
                statement="mismo CLABE",
                entities=["RFC:AAA010101AA1", "EMP:0001"],
                facts={"rfc": "AAA010101AA1", "emp_id": "0001"},
                legitimate_alternatives=[
                    "El proveedor puede ser la misma persona fisica que el empleado."
                ],
            )
        ]
    )

    method = _method_payload(case)["deterministic_pre_analysis"]
    [claim] = method["shared_identifier_claims"]
    assert claim["a_transfer_between_these_parties_is_also_observed"] is False
    assert "does not establish common ownership" in claim["what_this_does_not_establish"]

    # El Challenger recibe la alternativa legitima que el detector ya declaro.
    challenger = _challenger_payload(case)
    declared = challenger["legitimate_alternatives_declared_by_detectors"]
    assert declared
    assert "persona fisica" in declared[0][
        "legitimate_alternatives_declared_by_the_detector"
    ][0]


# ==========================================================================
# CASO 3 — ciclo A->B->C->A con montos diferentes
# ==========================================================================

def test_cycle_with_different_amounts_arrives_quantified_without_a_threshold():
    case = _case(
        observations=[
            Observation(
                observation_id="OBS-CYCLE",
                detector_name="deterministic_relational",
                signal_type="directed_bank_transfer_cycle",
                statement="ciclo dirigido",
                facts={
                    "account_cycle": ["A", "B", "C", "A"],
                    "transaction_amounts": [500_000.0, 31_250.0, 480_000.0],
                    "transaction_dates": ["2025-01-05", "2025-05-20", "2025-10-02"],
                },
            )
        ]
    )

    [cycle] = _method_payload(case)["deterministic_pre_analysis"]["cycle_continuity"]
    assert cycle["largest_to_smallest_leg_ratio"] == 16.0
    assert cycle["date_span_days"] > 250
    assert "same funds" in cycle["what_this_does_not_establish"]

    assert "same-money" in METHOD_CRITIC_PROMPT
    assert "must not invent one" in METHOD_CRITIC_PROMPT


# ==========================================================================
# CASO 4 — facturas repetidas explicadas por contrato mensual
# ==========================================================================

def test_monthly_contract_alternative_is_handed_to_the_challenger_first():
    case = _case(
        observations=[
            Observation(
                observation_id="OBS-CLUSTER",
                detector_name="deterministic_relational",
                signal_type="short_window_similar_invoice_cluster",
                statement="facturas similares en ventana corta",
                legitimate_alternatives=[
                    "Un contrato marco con cuota mensual fija produce este patron."
                ],
                recommended_checks=["Consultar contratos del proveedor."],
            )
        ]
    )

    payload = _challenger_payload(case)
    keys = list(payload)
    # Debe llegar ANTES del volcado del case_state, no enterrado dentro.
    assert keys.index("legitimate_alternatives_declared_by_detectors") < keys.index(
        "case_state"
    )
    declared = payload["legitimate_alternatives_declared_by_detectors"][0]
    assert "contrato marco" in declared[
        "legitimate_alternatives_declared_by_the_detector"
    ][0]
    assert declared["checks_the_detector_recommended"]


# ==========================================================================
# CASO 5 — ausencia de contrato sin garantia de completitud
# ==========================================================================

def test_absent_contract_is_reported_as_estate_absence_to_both_critics():
    case = _case(
        actions=[
            ActionRecord(
                step=1,
                action_name="get_vendor_contracts",
                arguments={"vendor_rfc": "AAA010101AA1"},
                reason="buscar cobertura",
                result_summary="0 contratos",
                success=True,
                produced_evidence_ids=[],
            )
        ]
    )

    for payload in (_method_payload(case), _challenger_payload(case)):
        absence = payload["deterministic_pre_analysis"]["absence_claims"]
        [empty] = absence["successful_queries_returning_no_record"]
        assert empty["action_name"] == "get_vendor_contracts"
        assert "SUPPLIED ESTATE" in empty["what_may_be_concluded"]
        assert "estate is complete" in empty["what_may_be_concluded"]

    assert "absence in estate != absence in reality" in METHOD_CRITIC_PROMPT


# ==========================================================================
# CASO 6 — vendor sospechoso cuyo RFC no aparece en EFOS
# ==========================================================================

def test_no_efos_record_yields_no_efos_evidence_and_no_invented_rule():
    """Sin registro EFOS no hay evidencia EFOS, y sin rule_context no hay regla."""

    case = _case(
        actions=[
            ActionRecord(
                step=1,
                action_name="check_efos",
                arguments={"vendor_rfc": "BBB020202BB2"},
                reason="verificar listado",
                result_summary="No se encontro registro EFOS para BBB020202BB2.",
                success=True,
                produced_evidence_ids=[],
            )
        ]
    )

    payload = _method_payload(case)

    [empty] = payload["deterministic_pre_analysis"]["absence_claims"][
        "successful_queries_returning_no_record"
    ]
    assert empty["action_name"] == "check_efos"

    # Sin rule_context, el prompt prohibe validar cualquier claim normativo.
    assert "No explicit rule context supplied" in payload["rule_context"]
    assert "NO INVENTED LAW" in METHOD_CRITIC_PROMPT


# ==========================================================================
# CASO 7 — hipotesis fuerte con evidencia independiente: DEBE poder sobrevivir
# ==========================================================================

def test_independent_evidence_is_not_flagged_and_survives_is_accepted():
    """Contrapeso imprescindible: los criticos no deben bloquear lo defendible."""

    case = _case(
        evidence=[
            CaseEvidence(
                evidence_id="EV-EFOS",
                statement="RFC en efos_list",
                direction="for",
                produced_by="deterministic_tabular",
                source_refs=[
                    EvidenceRef(source_table="efos_list", record_id="AAA010101AA1")
                ],
            ),
            CaseEvidence(
                evidence_id="EV-INV",
                statement="factura emitida",
                direction="for",
                produced_by="action:get_vendor_invoices",
                source_refs=[
                    EvidenceRef(source_table="invoices", record_id="INV-1")
                ],
            ),
            CaseEvidence(
                evidence_id="EV-TXN",
                statement="pago realizado",
                direction="for",
                produced_by="action:get_bank_transactions_for_clabe",
                source_refs=[
                    EvidenceRef(source_table="bank_txns", record_id="BNK-1")
                ],
            ),
        ]
    )

    independence = _method_payload(case)["deterministic_pre_analysis"][
        "evidence_independence"
    ]
    assert independence["distinct_underlying_records"] == 3
    assert independence["records_supporting_more_than_one_signal"] == []

    client = _Capture(_survives(strongest_legitimate_alternative="ninguna sostenida"))
    review = challenge_case(
        case_state=case,
        target_hypothesis_id=TARGET,
        available_actions=list_available_actions(),
        llm_client=client,
    )
    assert review.outcome == "survives"

    method = criticize_method(
        llm_client=_Capture(_clear()),
        case_state=case,
        target_hypothesis_id=TARGET,
        available_actions=list_available_actions(),
    )
    assert method.review.outcome == "clear"

    # El prompt prohibe explicitamente bloquear por una posibilidad remota.
    assert "remote possibility" in CHALLENGER_SYSTEM_PROMPT
    assert "not here to make accusations impossible" in CHALLENGER_SYSTEM_PROMPT


# ==========================================================================
# CASO 8 — alternativa legitima viva: debe volver a investigacion
# ==========================================================================

def test_a_live_legitimate_alternative_requires_a_concrete_explanation():
    case = _case()

    review = challenge_case(
        case_state=case,
        target_hypothesis_id=TARGET,
        available_actions=list_available_actions(),
        llm_client=_Capture(
            {
                "target_hypothesis_id": TARGET,
                "outcome": "legitimate_alternative",
                "strongest_legitimate_alternative": (
                    "Un contrato marco vigente explica la cadencia de las facturas."
                ),
                "reasoning_summary": "la alternativa sigue viva",
            }
        ),
    )
    assert review.outcome == "legitimate_alternative"
    assert review.strongest_legitimate_alternative

    # Sin explicacion concreta el contrato lo rechaza: no se puede declinar a ciegas.
    with pytest.raises(ValueError, match="concrete"):
        ChallengerReview(
            target_hypothesis_id=TARGET,
            outcome="legitimate_alternative",
            reasoning_summary="vaga",
        )


def test_needs_more_evidence_must_carry_a_question_and_an_executable_action():
    """"Investigar mas" sin accion ejecutable no puede pasar."""

    with pytest.raises(ValueError, match="missing_counterevidence"):
        ChallengerReview(
            target_hypothesis_id=TARGET,
            outcome="needs_more_evidence",
            reasoning_summary="falta algo",
        )

    with pytest.raises(ValueError, match="proposed executable action"):
        ChallengerReview(
            target_hypothesis_id=TARGET,
            outcome="needs_more_evidence",
            reasoning_summary="falta algo",
            missing_counterevidence=[
                {"question": "¿Existe contrato?", "why_it_matters": "cambiaria todo"}
            ],
        )


def test_a_proposed_action_outside_the_action_bank_is_rejected():
    case = _case()
    review = ChallengerReview(
        target_hypothesis_id=TARGET,
        outcome="needs_more_evidence",
        reasoning_summary="falta",
        missing_counterevidence=[{"question": "q", "why_it_matters": "w"}],
        proposed_actions=[
            ProposedAction(
                action_name="subpoena_bank_records",
                arguments={},
                reason="r",
                question_resolved="q",
            )
        ],
    )

    with pytest.raises(ValueError, match="unavailable action"):
        validate_challenger_review(
            review,
            case_state=case,
            target_hypothesis_id=TARGET,
            available_actions=list_available_actions(),
        )


# ==========================================================================
# INTEGRIDAD DE LOS OUTCOMES (contratos que no cambiaron)
# ==========================================================================

def test_clear_cannot_carry_material_objections():
    with pytest.raises(ValueError, match="cannot contain material objections"):
        MethodCriticReview(
            target_hypothesis_id=TARGET,
            outcome="clear",
            reasoning_summary="ok",
            evidence_dependency_risks=["tres detectores, un registro"],
        )


def test_needs_more_work_requires_an_action_and_cannot_support_requires_a_blocker():
    with pytest.raises(ValueError, match="at least one proposed_actions"):
        MethodCriticReview(
            target_hypothesis_id=TARGET,
            outcome="needs_more_work",
            reasoning_summary="hay defecto",
            problematic_assumptions=["x"],
        )

    with pytest.raises(ValueError, match="material_blockers"):
        MethodCriticReview(
            target_hypothesis_id=TARGET,
            outcome="cannot_support",
            reasoning_summary="no se puede",
        )


def test_the_outcome_vocabularies_are_unchanged():
    """El orquestador y el gate ramifican sobre estos literales exactos."""

    from src.agents.challenger import ChallengerOutcome
    from src.agents.method_critic import MethodCriticOutcome

    assert set(ChallengerOutcome.__args__) == {
        "survives",
        "needs_more_evidence",
        "legitimate_alternative",
        "alternative_hypothesis",
    }
    assert set(MethodCriticOutcome.__args__) == {
        "clear",
        "needs_more_work",
        "cannot_support",
    }


# ==========================================================================
# COBERTURA DE INSTRUCCIONES EN LOS PROMPTS
# ==========================================================================

@pytest.mark.parametrize(
    "instruction",
    [
        "DISCRIMINATING",
        "TUNNEL VISION",
        "ANOMALY",
        "SUSPICION",
        "VERIFIED FACT",
        "legitimate_alternatives_declared_by_detectors",
        "remote possibility",
    ],
)
def test_challenger_prompt_teaches_each_judgment_skill(instruction: str):
    assert instruction in CHALLENGER_SYSTEM_PROMPT


@pytest.mark.parametrize(
    "instruction",
    [
        "UNIVERSAL",
        "EXISTENTIAL",
        "distinct_underlying_records",
        "records_supporting_more_than_one_signal",
        "semantics_declared",
        "cycle_continuity",
        "absence_claims",
        "FALSIFY",
    ],
)
def test_method_critic_prompt_teaches_each_judgment_skill(instruction: str):
    assert instruction in METHOD_CRITIC_PROMPT


def test_neither_critic_is_told_to_decide_guilt():
    for prompt in (CHALLENGER_SYSTEM_PROMPT, METHOD_CRITIC_PROMPT):
        lowered = prompt.lower()
        assert "not to decide guilt" in lowered or "never decide guilt" in lowered
