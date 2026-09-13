from __future__ import annotations

from time import perf_counter
from typing import Literal

from pydantic import BaseModel, Field

from src.core.estate import EstateRepository
from src.core.models import Lead, Observation
from src.detectors.deterministic_tabular import run_deterministic_tabular_detectors
from src.detectors.deterministic_relational import run_deterministic_relational_detectors
from src.investigation.case_builder import build_case_state
from src.investigation.investigator import LLMClient
from src.investigation.lead_builder import build_leads
from src.investigation.preverification_pipeline import (
    PreVerificationResult,
    run_preverification_pipeline,
)
from src.gnn.runtime import GNNRuntimeResult, run_optional_gnn_discovery


CasePipelineStatus = Literal[
    "ready_for_verification",
    "blocked",
    "error",
]


class CasePipelineOutcome(BaseModel):
    """Auditable outcome for one Lead processed up to the Verifier boundary."""

    lead_id: str
    case_id: str
    status: CasePipelineStatus
    stop_reason: str | None = None
    preverification: PreVerificationResult | None = None
    error_type: str | None = None
    error_message: str | None = None


class GNNDiscoveryMetadata(BaseModel):
    """Operational metadata for optional neural discovery."""

    enabled: bool
    status: Literal["disabled", "skipped", "success", "error"]
    observation_count: int = 0
    training_seeds: list[int] = Field(default_factory=list)
    detail: str | None = None
    error_type: str | None = None
    error_message: str | None = None


class EstatePreverificationMetadata(BaseModel):
    """Run counters. This is not official submission metadata."""

    observation_count: int
    lead_count: int
    processed_case_count: int
    ready_for_verification_count: int
    blocked_case_count: int
    error_count: int
    max_cases: int | None = None
    wall_clock_seconds: float = Field(ge=0)
    gnn: GNNDiscoveryMetadata


class EstatePreverificationRunResult(BaseModel):
    """
    Result of the production discovery/investigation pipeline up to verification.

    This layer does not verify facts, authorize findings, or serialize submission.json.
    """

    observations: list[Observation] = Field(default_factory=list)
    leads: list[Lead] = Field(default_factory=list)
    case_outcomes: list[CasePipelineOutcome] = Field(default_factory=list)
    metadata: EstatePreverificationMetadata


def run_estate_preverification(
    estate: EstateRepository,
    llm_client: LLMClient,
    *,
    max_cases: int | None = None,
    max_initial_investigation_steps: int = 6,
    max_review_rounds: int = 3,
    max_followup_investigation_steps: int = 3,
    rule_context: list | None = None,
    enable_gnn: bool = False,
    gnn_seeds: tuple[int, ...] = (11, 17, 23),
    gnn_training_config: object | None = None,
    gnn_policy: object | None = None,
) -> EstatePreverificationRunResult:
    """
    Run the real pipeline from an official EstateRepository to the Verifier boundary.

    Flow:
        EstateRepository
        -> deterministic observations
        -> optional GNN discovery observations
        -> Leads
        -> CaseState
        -> Investigator
        -> Challenger
        -> Method Critic
        -> ready_for_verification OR explicit blocked/error outcome

    Per-case failures are isolated so one malformed LLM response or case-specific
    failure does not erase the results of other Leads. Every failure remains visible
    in ``case_outcomes``.
    """

    if max_cases is not None and max_cases < 1:
        raise ValueError("max_cases must be at least 1 when provided.")

    started = perf_counter()

    # Discovery failures are systemic and should fail loudly rather than silently
    # returning an apparently clean audit.
    observations = run_deterministic_tabular_detectors(estate)
    observations.extend(run_deterministic_relational_detectors(estate))

    # Neural discovery is optional by design. A failure must be visible, but it
    # must not erase deterministic observations or block the rest of the audit.
    gnn_result = run_optional_gnn_discovery(
        estate,
        enabled=enable_gnn,
        seeds=gnn_seeds,
        training_config=gnn_training_config,
        policy=gnn_policy,
    )
    observations.extend(gnn_result.observations)

    leads = build_leads(observations)

    selected_leads = leads if max_cases is None else leads[:max_cases]
    outcomes: list[CasePipelineOutcome] = []

    for lead in selected_leads:
        case_id = f"CASE-{lead.lead_id}"

        try:
            case_state = build_case_state(
                lead=lead,
                observations=observations,
                case_id=case_id,
            )

            result = run_preverification_pipeline(
                case_state=case_state,
                estate=estate,
                llm_client=llm_client,
                max_initial_investigation_steps=max_initial_investigation_steps,
                max_review_rounds=max_review_rounds,
                max_followup_investigation_steps=max_followup_investigation_steps,
                rule_context=rule_context,
            )

            outcomes.append(
                CasePipelineOutcome(
                    lead_id=lead.lead_id,
                    case_id=result.case_state.case_id,
                    status=(
                        "ready_for_verification"
                        if result.ready_for_verification
                        else "blocked"
                    ),
                    stop_reason=result.stop_reason,
                    preverification=result,
                )
            )
        except Exception as error:
            # Case-local errors stay explicit. We intentionally do not convert them
            # into "inconclusive" because an execution failure is not an evidentiary
            # conclusion about the case.
            outcomes.append(
                CasePipelineOutcome(
                    lead_id=lead.lead_id,
                    case_id=case_id,
                    status="error",
                    stop_reason="case_processing_error",
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
            )

    ready_count = sum(
        outcome.status == "ready_for_verification" for outcome in outcomes
    )
    blocked_count = sum(outcome.status == "blocked" for outcome in outcomes)
    error_count = sum(outcome.status == "error" for outcome in outcomes)

    metadata = EstatePreverificationMetadata(
        observation_count=len(observations),
        lead_count=len(leads),
        processed_case_count=len(outcomes),
        ready_for_verification_count=ready_count,
        blocked_case_count=blocked_count,
        error_count=error_count,
        max_cases=max_cases,
        wall_clock_seconds=perf_counter() - started,
        gnn=GNNDiscoveryMetadata(
            enabled=gnn_result.enabled,
            status=gnn_result.status,
            observation_count=len(gnn_result.observations),
            training_seeds=list(gnn_result.seeds),
            detail=gnn_result.detail,
            error_type=gnn_result.error_type,
            error_message=gnn_result.error_message,
        ),
    )

    return EstatePreverificationRunResult(
        observations=observations,
        leads=leads,
        case_outcomes=outcomes,
        metadata=metadata,
    )
