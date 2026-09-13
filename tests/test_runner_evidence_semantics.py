from src.core.models import CaseState, InvestigatorDecision, ProposedAction
from src.investigation.runner import apply_investigator_decision


def _investigate(action_name: str, arguments: dict) -> InvestigatorDecision:
    return InvestigatorDecision(
        decision="investigate",
        current_assessment="Hace falta consultar el estate.",
        hypotheses=[],
        next_action=ProposedAction(
            action_name=action_name,
            arguments=arguments,
            reason="Resolver una pregunta concreta con datos del estate.",
            question_resolved="¿Qué contiene el estate sobre esta pregunta?",
        ),
        new_unknowns=[],
        resolved_unknowns=[],
        reason="La consulta puede aportar información factual.",
    )


class EmptyContractsEstate:
    def get_vendor_contracts(self, vendor_rfc: str):
        return []


class MissingEfosEstate:
    def get_efos_record(self, vendor_rfc: str):
        return None


class OneContractEstate:
    def get_vendor_contracts(self, vendor_rfc: str):
        return [
            {
                "contract_id": "CTR-001",
                "vendor_rfc": vendor_rfc,
                "start_date": "2026-01-01",
                "value": 100000.0,
                "scope_text": "Servicios",
            }
        ]


def test_successful_empty_query_is_recorded_but_not_case_evidence():
    case = CaseState(case_id="CASE-EMPTY", lead_id="LEAD-EMPTY")

    updated = apply_investigator_decision(
        case_state=case,
        decision=_investigate(
            "get_vendor_contracts",
            {"vendor_rfc": "TEST010101AAA"},
        ),
        estate=EmptyContractsEstate(),
    )

    assert len(updated.actions_taken) == 1
    record = updated.actions_taken[0]
    assert record.success is True
    assert record.result_data == []
    assert record.produced_evidence_ids == []
    assert updated.evidence == []


def test_successful_absence_check_without_source_record_is_not_case_evidence():
    case = CaseState(case_id="CASE-EFOS", lead_id="LEAD-EFOS")

    updated = apply_investigator_decision(
        case_state=case,
        decision=_investigate(
            "check_efos",
            {"vendor_rfc": "TEST010101AAA"},
        ),
        estate=MissingEfosEstate(),
    )

    assert len(updated.actions_taken) == 1
    record = updated.actions_taken[0]
    assert record.success is True
    assert record.result_data is None
    assert record.produced_evidence_ids == []
    assert updated.evidence == []


def test_successful_query_with_source_refs_still_creates_case_evidence():
    case = CaseState(case_id="CASE-POS", lead_id="LEAD-POS")

    updated = apply_investigator_decision(
        case_state=case,
        decision=_investigate(
            "get_vendor_contracts",
            {"vendor_rfc": "TEST010101AAA"},
        ),
        estate=OneContractEstate(),
    )

    assert len(updated.actions_taken) == 1
    assert updated.actions_taken[0].produced_evidence_ids == ["EV-ACTION-0001"]
    assert len(updated.evidence) == 1
    assert updated.evidence[0].source_refs[0].source_table == "contracts"
    assert updated.evidence[0].source_refs[0].record_id == "CTR-001"
