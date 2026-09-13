from __future__ import annotations

from src.core.models import EvidenceRef, InvestigationLoopResult, Observation
from src.investigation.preverification_pipeline import PreVerificationResult
import src.investigation.estate_pipeline as estate_pipeline


class NeverCalledLLM:
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("LLM should not be called in this test.")


def _blocked(case_state, **kwargs):
    initial = InvestigationLoopResult(
        case_state=case_state,
        decisions=[],
        iterations=0,
        stop_reason="close_inconclusive",
    )
    return PreVerificationResult(
        case_state=case_state,
        initial_investigation=initial,
        ready_for_verification=False,
        stop_reason="investigator_closed_inconclusive",
    )


def test_estate_pipeline_includes_relational_discovery(monkeypatch):
    relational_observation = Observation(
        observation_id="OBS-REL-1",
        detector_name="deterministic_relational",
        signal_type="vendor_to_employee_bank_transfer",
        entities=["RFC:AAA010101AAA", "EMP:EMP001"],
        statement="A vendor-to-employee transfer exists in the supplied estate.",
        evidence=[
            EvidenceRef(source_table="bank_txns", record_id="TX-1"),
        ],
    )

    monkeypatch.setattr(
        estate_pipeline,
        "run_deterministic_tabular_detectors",
        lambda estate: [],
    )
    monkeypatch.setattr(
        estate_pipeline,
        "run_deterministic_relational_detectors",
        lambda estate: [relational_observation],
    )
    monkeypatch.setattr(
        estate_pipeline,
        "run_preverification_pipeline",
        _blocked,
    )

    result = estate_pipeline.run_estate_preverification(
        estate=object(),
        llm_client=NeverCalledLLM(),
    )

    assert result.metadata.observation_count == 1
    assert result.metadata.lead_count == 1
    assert result.observations[0].signal_type == "vendor_to_employee_bank_transfer"
    assert result.leads[0].subject_entities == ["RFC:AAA010101AAA", "EMP:EMP001"]
