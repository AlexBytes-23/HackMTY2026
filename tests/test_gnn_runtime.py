from __future__ import annotations

from dataclasses import dataclass

from src.core.models import Observation
import src.gnn.runtime as gnn_runtime


def test_optional_gnn_is_disabled_without_loading_neural_stack(monkeypatch):
    def fail_if_loaded():
        raise AssertionError("neural components should not load when disabled")

    monkeypatch.setattr(gnn_runtime, "_load_gnn_components", fail_if_loaded)
    result = gnn_runtime.run_optional_gnn_discovery(object(), enabled=False)

    assert result.status == "disabled"
    assert result.enabled is False
    assert result.observations == ()


@dataclass
class _Policy:
    min_peer_count: int = 5

    def validate(self):
        return None


@dataclass
class _Bundle:
    node_ids: dict


def test_optional_gnn_skips_when_no_peer_group_is_large_enough(monkeypatch):
    calls = {"train": 0}

    def build(_estate):
        return _Bundle(
            node_ids={
                "vendor": ("V1", "V2"),
                "employee": ("E1",),
                "account": ("A1", "A2", "A3"),
            }
        )

    def train(*args, **kwargs):
        calls["train"] += 1
        raise AssertionError("training should not run for an ineligible estate")

    monkeypatch.setattr(
        gnn_runtime,
        "_load_gnn_components",
        lambda: (build, train, lambda *args, **kwargs: [], _Policy),
    )

    result = gnn_runtime.run_optional_gnn_discovery(
        object(),
        enabled=True,
        seeds=(11, 17, 23),
    )

    assert result.status == "skipped"
    assert result.enabled is True
    assert calls["train"] == 0
    assert "min_peer_count=5" in result.detail


def test_optional_gnn_success_keeps_observations_as_discovery_only(monkeypatch):
    observation = Observation(
        observation_id="OBS-GNN-1",
        detector_name="gnn_discovery",
        signal_type="gnn_relational_anomaly",
        entities=["RFC:V1"],
        statement="Relative graph anomaly.",
    )

    bundle = _Bundle(
        node_ids={
            "vendor": ("V1", "V2", "V3", "V4", "V5"),
            "employee": (),
            "account": (),
        }
    )

    monkeypatch.setattr(
        gnn_runtime,
        "_load_gnn_components",
        lambda: (
            lambda estate: bundle,
            lambda bundle, seeds, training_config=None: object(),
            lambda estate, bundle, ensemble, policy=None: [observation],
            _Policy,
        ),
    )

    result = gnn_runtime.run_optional_gnn_discovery(
        object(),
        enabled=True,
        seeds=(3, 5),
    )

    assert result.status == "success"
    assert result.seeds == (3, 5)
    assert result.observations == (observation,)
    assert result.error_type is None


def test_optional_gnn_failure_is_explicit_and_does_not_raise(monkeypatch):
    def explode():
        raise RuntimeError("synthetic dependency failure")

    monkeypatch.setattr(gnn_runtime, "_load_gnn_components", explode)

    result = gnn_runtime.run_optional_gnn_discovery(
        object(),
        enabled=True,
        seeds=(7,),
    )

    assert result.status == "error"
    assert result.observations == ()
    assert result.error_type == "RuntimeError"
    assert "synthetic dependency failure" in result.error_message
