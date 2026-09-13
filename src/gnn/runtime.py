"""Optional runtime wrapper for GNN discovery.

The neural subsystem is auxiliary discovery.  Failure here must remain visible
without converting the whole deterministic audit into an execution failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.core.estate import EstateRepository
from src.core.models import Observation


GNNRuntimeStatus = Literal["disabled", "skipped", "success", "error"]


@dataclass(frozen=True)
class GNNRuntimeResult:
    enabled: bool
    status: GNNRuntimeStatus
    observations: tuple[Observation, ...] = ()
    seeds: tuple[int, ...] = ()
    detail: str | None = None
    error_type: str | None = None
    error_message: str | None = None


def _load_gnn_components():
    """Late import so deterministic runtime does not require neural dependencies."""

    from src.gnn.discovery import run_multi_seed_discovery
    from src.gnn.graph_builder import build_gnn_graph
    from src.gnn.observation_adapter import (
        GNNObservationPolicy,
        build_gnn_observations,
    )

    return (
        build_gnn_graph,
        run_multi_seed_discovery,
        build_gnn_observations,
        GNNObservationPolicy,
    )


def run_optional_gnn_discovery(
    estate: EstateRepository,
    *,
    enabled: bool = False,
    seeds: tuple[int, ...] = (11, 17, 23),
    training_config: Any = None,
    policy: Any = None,
) -> GNNRuntimeResult:
    """Run GNN discovery without making it a critical dependency.

    ``success`` means the neural subsystem executed, not that any candidate is
    suspicious or fraudulent.  ``skipped`` is reserved for an estate that cannot
    produce any peer group large enough for the configured observation policy.
    Other failures are returned explicitly as ``error`` and never silently
    swallowed.
    """

    if not enabled:
        return GNNRuntimeResult(enabled=False, status="disabled")

    if not seeds:
        return GNNRuntimeResult(
            enabled=True,
            status="error",
            seeds=(),
            error_type="ValueError",
            error_message="At least one GNN training seed is required.",
        )

    try:
        (
            build_gnn_graph,
            run_multi_seed_discovery,
            build_gnn_observations,
            policy_cls,
        ) = _load_gnn_components()

        active_policy = policy if policy is not None else policy_cls()
        active_policy.validate()

        bundle = build_gnn_graph(estate)
        eligible_peer_types = tuple(
            node_type
            for node_type, ids in bundle.node_ids.items()
            if len(ids) >= active_policy.min_peer_count
        )
        if not eligible_peer_types:
            return GNNRuntimeResult(
                enabled=True,
                status="skipped",
                seeds=tuple(int(seed) for seed in seeds),
                detail=(
                    "No GNN node type has enough peers for the configured "
                    f"min_peer_count={active_policy.min_peer_count}."
                ),
            )

        ensemble = run_multi_seed_discovery(
            bundle,
            seeds=tuple(int(seed) for seed in seeds),
            training_config=training_config,
        )
        observations = build_gnn_observations(
            estate,
            bundle,
            ensemble,
            policy=active_policy,
        )
        return GNNRuntimeResult(
            enabled=True,
            status="success",
            observations=tuple(observations),
            seeds=tuple(int(seed) for seed in seeds),
            detail=(
                "GNN discovery executed successfully. Neural scores remain "
                "estate-relative discovery signals, not findings or proof."
            ),
        )
    except Exception as error:
        return GNNRuntimeResult(
            enabled=True,
            status="error",
            observations=(),
            seeds=tuple(int(seed) for seed in seeds),
            detail="GNN discovery failed; deterministic discovery remains usable.",
            error_type=type(error).__name__,
            error_message=str(error),
        )
