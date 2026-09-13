from src.core.models import (
    CaseState,
    EvidenceRef,
    Observation,
)

from src.investigation.case_builder import (
    build_case_state,
)

from src.investigation.lead_builder import (
    build_leads,
)

from src.investigation.investigator import (
    build_investigator_prompt,
    investigate_case,
)

from src.investigation.runner import (
    apply_investigator_decision,
    run_investigation_loop,
)



class FakeLLM:
    """
    Modelo falso para probar nuestra arquitectura
    sin gastar API ni depender de Internet.
    """

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:

        return """
        {
          "decision": "investigate",

          "current_assessment":
            "Existe una señal pero falta revisar soporte contractual.",

          "hypotheses": [
            {
              "hypothesis_id": "H-001",
              "statement":
                "La operación podría carecer de soporte económico.",
              "scheme_type": null,
              "status": "open",
              "supporting_evidence_ids": [],
              "counter_evidence_ids": [],
              "unresolved_questions": [
                "¿Existe contrato válido?"
              ]
            }
          ],

          "next_action": {
            "action_name": "get_vendor_contracts",
            "arguments": {
              "vendor_rfc": "TEST010101AAA"
            },
            "reason":
              "Un contrato podría explicar legítimamente la operación.",
            "question_resolved":
              "¿Existe soporte contractual?"
          },

          "new_unknowns": [
            "¿El contrato cubre específicamente la operación?"
          ],

          "reason":
            "La evidencia actual no permite distinguir entre irregularidad y operación legítima."
        }
        """


def test_investigator_requests_valid_action():

    case = CaseState(
        case_id="CASE-001",
        lead_id="LEAD-001",
        subject_entities=[
            "TEST010101AAA"
        ],
    )

    decision = investigate_case(
        case_state=case,
        llm_client=FakeLLM(),
    )

    assert (
        decision.decision
        == "investigate"
    )

    assert (
        decision.next_action
        is not None
    )

    assert (
        decision.next_action.action_name
        == "get_vendor_contracts"
    )

class FakeEstate:
    def get_vendor_contracts(
        self,
        vendor_rfc: str,
    ):
        return [
            {
                "contract_id": "CTR-001",
                "vendor_rfc": vendor_rfc,
                "start_date": "2026-01-01",
                "value": 120000.0,
                "scope_text": "Servicios de consultoría",
            }
        ]


def test_action_result_enters_case_state():

    case = CaseState(
        case_id="CASE-002",
        lead_id="LEAD-002",
        subject_entities=[
            "TEST010101AAA"
        ],
    )

    decision = investigate_case(
        case_state=case,
        llm_client=FakeLLM(),
    )

    updated = apply_investigator_decision(
        case_state=case,
        decision=decision,
        estate=FakeEstate(),
    )

    assert len(updated.actions_taken) == 1

    assert (
        updated.actions_taken[0].action_name
        == "get_vendor_contracts"
    )

    assert (
        updated.actions_taken[0]
        .result_data[0]["contract_id"]
        == "CTR-001"
    )

    assert len(updated.evidence) == 1

    assert (
        updated.evidence[0]
        .source_refs[0]
        .source_table
        == "contracts"
    )

    assert (
        updated.evidence[0]
        .source_refs[0]
        .record_id
        == "CTR-001"
    )

    assert (
        updated.actions_taken[0]
        .question_resolved
        == "¿Existe soporte contractual?"
    )

    assert updated.status == "investigating"

class TwoStepLLM:
    """
    Simula dos vueltas del Investigator.

    Vuelta 1:
        pide revisar contratos.

    Vuelta 2:
        ve que ya existe soporte contractual
        y pide revisión adversarial.
    """

    def __init__(self):
        self.calls = 0

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:

        self.calls += 1

        if self.calls == 1:
            return """
            {
              "decision": "investigate",

              "current_assessment":
                "Falta revisar soporte contractual.",

              "hypotheses": [
                {
                  "hypothesis_id": "H-001",
                  "statement":
                    "La operación podría carecer de soporte económico.",
                  "scheme_type": null,
                  "status": "open",
                  "supporting_evidence_ids": [],
                  "counter_evidence_ids": [],
                  "unresolved_questions": [
                    "¿Existe contrato?"
                  ]
                }
              ],

              "next_action": {
                "action_name": "get_vendor_contracts",
                "arguments": {
                  "vendor_rfc": "TEST010101AAA"
                },
                "reason":
                  "Necesito comprobar soporte contractual.",
                "question_resolved":
                  "¿Existe contrato?"
              },

              "new_unknowns": [],
              "resolved_unknowns": [],

              "reason":
                "El contrato podría cambiar la interpretación."
            }
            """

        return """
        {
          "decision": "request_review",

          "current_assessment":
            "Existe soporte contractual y el caso necesita revisión adversarial.",

          "hypotheses": [
            {
              "hypothesis_id": "H-001",
              "statement":
                "La operación podría carecer de soporte económico.",
              "scheme_type": null,
              "status": "inconclusive",
              "supporting_evidence_ids": [],
              "counter_evidence_ids": [
                "EV-ACTION-0001"
              ],
              "unresolved_questions": []
            }
          ],

          "next_action": null,

          "new_unknowns": [],

          "resolved_unknowns": [
            "¿Existe contrato?"
          ],

          "reason":
            "La nueva evidencia cambia la interpretación y debe revisarse adversarialmente."
        }
        """


def test_investigation_runs_multiple_steps():

    case = CaseState(
        case_id="CASE-003",
        lead_id="LEAD-003",
        subject_entities=[
            "TEST010101AAA"
        ],
        unknowns=[
            "¿Existe contrato?"
        ],
    )

    result = run_investigation_loop(
        case_state=case,
        estate=FakeEstate(),
        llm_client=TwoStepLLM(),
        max_steps=4,
    )

    assert result.iterations == 2

    assert (
        result.stop_reason
        == "request_review"
    )

    assert (
        result.case_state.status
        == "ready_for_review"
    )

    assert (
        len(result.case_state.actions_taken)
        == 1
    )

    assert (
        "¿Existe contrato?"
        not in result.case_state.unknowns
    )

def test_investigation_blocks_repeated_action():
    case = CaseState(
        case_id="CASE-004",
        lead_id="LEAD-004",
        subject_entities=[
            "TEST010101AAA"
        ],
    )

    # FakeLLM siempre pide exactamente
    # la misma acción con el mismo RFC.
    result = run_investigation_loop(
        case_state=case,
        estate=FakeEstate(),
        llm_client=FakeLLM(),
        max_steps=6,
    )

    assert (
        result.stop_reason
        == "repeated_action"
    )

    # La primera acción sí se ejecutó.
    # La segunda fue bloqueada antes de ejecutarse.
    assert (
        len(result.case_state.actions_taken)
        == 1
    )

    assert result.iterations == 2

def test_observations_become_case_state():

    observations = [
        Observation(
            observation_id="OBS-001",
            detector_name="tabular_detector",
            signal_type="po_pattern",
            entities=[
                "RFC:TEST010101AAA"
            ],
            statement=(
                "Se detectó un patrón repetitivo "
                "de órdenes de compra."
            ),
            evidence=[
                EvidenceRef(
                    source_table="purchase_orders",
                    record_id="PO-001",
                )
            ],
            facts={
                "po_count": 6,
            },
            limitations=[
                (
                    "La repetición no demuestra "
                    "threshold splitting."
                )
            ],
            legitimate_alternatives=[
                "Compras periódicas legítimas."
            ],
            recommended_checks=[
                "Revisar contratos del proveedor."
            ],
        ),

        Observation(
            observation_id="OBS-002",
            detector_name="bank_detector",
            signal_type="bank_pattern",
            entities=[
                "RFC:TEST010101AAA"
            ],
            statement=(
                "El proveedor presenta movimientos "
                "bancarios que requieren revisión."
            ),
            evidence=[
                EvidenceRef(
                    source_table="bank_txns",
                    record_id="TXN-001",
                )
            ],
            facts={
                "txn_count": 3,
            },
            legitimate_alternatives=[
                "Flujo comercial legítimo."
            ],
        ),
    ]

    leads = build_leads(
        observations
    )

    assert len(leads) == 1

    case = build_case_state(
        lead=leads[0],
        observations=observations,
        case_id="CASE-REAL-001",
    )

    assert (
        case.case_id
        == "CASE-REAL-001"
    )

    assert len(case.observations) == 2

    assert len(case.evidence) == 2

    assert (
        case.observations[0]
        .facts["po_count"]
        == 6
    )

    assert (
        case.evidence[0]
        .source_refs[0]
        .record_id
        == "PO-001"
    )

    assert (
        "Revisar contratos del proveedor."
        in case.unknowns
    )

def test_investigator_prompt_receives_observation_context():

    observation = Observation(
        observation_id="OBS-PROMPT-001",
        detector_name="tabular_detector",
        signal_type="repeated_payments",
        entities=[
            "RFC:TEST010101AAA"
        ],
        statement=(
            "Se detectaron pagos repetitivos."
        ),
        evidence=[
            EvidenceRef(
                source_table="bank_txns",
                record_id="TXN-999",
            )
        ],
        facts={
            "payment_count": 8,
        },
        limitations=[
            (
                "Puede corresponder a una "
                "tarifa periódica."
            )
        ],
        legitimate_alternatives=[
            "Contrato mensual legítimo."
        ],
    )

    leads = build_leads([
        observation
    ])

    case = build_case_state(
        lead=leads[0],
        observations=[
            observation
        ],
    )

    prompt = build_investigator_prompt(
        case
    )

    assert "OBS-PROMPT-001" in prompt
    assert "payment_count" in prompt
    assert "Contrato mensual legítimo" in prompt
    assert "TXN-999" in prompt