from __future__ import annotations

from src.core.models import InvestigationLoopResult, Observation
from src.gnn.runtime import GNNRuntimeResult
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


def _gnn_observation() -> Observation:
    return Observation(
        observation_id="OBS-GNN-RUNTIME-1",
        detector_name="gnn_discovery",
        signal_type="gnn_relational_anomaly",
        entities=["RFC:AAA010101AAA"],
        statement="The model ranked this vendor as relatively unusual among peers.",
        score=0.93,
        score_semantics="Estate-relative unsupervised discovery score; not a fraud probability.",
    )


def test_estate_pipeline_adds_optional_gnn_observations(monkeypatch):
    monkeypatch.setattr(
        estate_pipeline,
        "run_deterministic_tabular_detectors",
        lambda estate: [],
    )
    monkeypatch.setattr(
        estate_pipeline,
        "run_deterministic_relational_detectors",
        lambda estate: [],
    )
    monkeypatch.setattr(
        estate_pipeline,
        "run_optional_gnn_discovery",
        lambda estate, **kwargs: GNNRuntimeResult(
            enabled=True,
            status="success",
            observations=(_gnn_observation(),),
            seeds=(11, 17, 23),
        ),
    )
    monkeypatch.setattr(
        estate_pipeline,
        "run_preverification_pipeline",
        _blocked,
    )

    result = estate_pipeline.run_estate_preverification(
        estate=object(),
        llm_client=NeverCalledLLM(),
        enable_gnn=True,
    )

    assert result.metadata.gnn.enabled is True
    assert result.metadata.gnn.status == "success"
    assert result.metadata.gnn.observation_count == 1
    assert result.metadata.gnn.training_seeds == [11, 17, 23]
    assert result.metadata.observation_count == 1
    assert result.observations[0].signal_type == "gnn_relational_anomaly"
    assert result.metadata.lead_count == 1


def test_estate_pipeline_gnn_error_does_not_erase_deterministic_discovery(monkeypatch):
    deterministic = Observation(
        observation_id="OBS-DET-1",
        detector_name="deterministic_relational",
        signal_type="directed_transfer_cycle",
        entities=["CLABE:A1", "CLABE:A2"],
        statement="A directed transfer cycle exists.",
    )
    monkeypatch.setattr(
        estate_pipeline,
        "run_deterministic_tabular_detectors",
        lambda estate: [deterministic],
    )
    monkeypatch.setattr(
        estate_pipeline,
        "run_deterministic_relational_detectors",
        lambda estate: [],
    )
    monkeypatch.setattr(
        estate_pipeline,
        "run_optional_gnn_discovery",
        lambda estate, **kwargs: GNNRuntimeResult(
            enabled=True,
            status="error",
            seeds=(11, 17, 23),
            error_type="RuntimeError",
            error_message="training failed",
        ),
    )
    monkeypatch.setattr(
        estate_pipeline,
        "run_preverification_pipeline",
        _blocked,
    )

    result = estate_pipeline.run_estate_preverification(
        estate=object(),
        llm_client=NeverCalledLLM(),
        enable_gnn=True,
    )

    assert result.metadata.gnn.status == "error"
    assert result.metadata.gnn.error_type == "RuntimeError"
    assert result.metadata.observation_count == 1
    assert result.observations == [deterministic]
    assert result.metadata.error_count == 0


def test_estate_pipeline_gnn_defaults_to_disabled(monkeypatch):
    monkeypatch.setattr(
        estate_pipeline,
        "run_deterministic_tabular_detectors",
        lambda estate: [],
    )
    monkeypatch.setattr(
        estate_pipeline,
        "run_deterministic_relational_detectors",
        lambda estate: [],
    )

    calls = []

    def disabled(estate, **kwargs):
        calls.append(kwargs)
        return GNNRuntimeResult(enabled=False, status="disabled")

    monkeypatch.setattr(estate_pipeline, "run_optional_gnn_discovery", disabled)

    result = estate_pipeline.run_estate_preverification(
        estate=object(),
        llm_client=NeverCalledLLM(),
    )

    assert calls[0]["enabled"] is False
    assert result.metadata.gnn.status == "disabled"
    assert result.metadata.gnn.observation_count == 0
