"""End-to-end entrypoint: estate.db -> submission.json.

    python -m src.run_audit --estate path/to/estate.db --seed 42 \
        --out runs/42/submission.json --replay recordings/session.jsonl

This module is wiring, not judgement.  Every forensic decision is made by the
components it calls:

    EstateRepository
      -> deterministic detectors / exact bank graph / optional GNN discovery
      -> Leads -> CaseState
      -> Investigator (Action Bank)
      -> Challenger -> follow-up -> Method Critic -> follow-up
      -> claim_builder (scope-defined amount)
      -> OfficialVerifier -> EvidenceGate
      -> FindingBuilder -> SubmissionBuilder -> submission.json

Three rules this file must never break:

1. A case that is not authorized by the Evidence Gate becomes a *lead not pursued*,
   never a finding.
2. An execution failure is reported as an execution failure.  It is never dressed
   up as inconclusive evidence.
3. ``run_metadata`` is measured, not estimated.  An unknown cost aborts the run
   rather than being serialized as 0.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from src.core.estate import EstateRepository
from src.investigation.claim_builder import build_phantom_vendor_claim
from src.investigation.estate_pipeline import (
    CasePipelineOutcome,
    EstatePreverificationRunResult,
    run_estate_preverification,
)
from src.investigation.postverification_pipeline import (
    PostVerificationBoundaryError,
    run_postverification_pipeline,
)
from src.output.finding_builder import build_finding
from src.output.models import LeadNotPursued, RunMetadata, SubmissionFinding
from src.output.submission_builder import build_submission, write_submission_json
from src.rules.default_rules import (
    build_default_registry,
    default_rule_id_for_scheme,
    rule_context_for_scheme,
)


# Only these prefixes may appear in official output. CLABE identifiers are internal
# and must never leave the system inside a submission.
OFFICIAL_ENTITY_PREFIXES = ("RFC:", "EMP:")


# ==========================================================================
# ENTITY NORMALIZATION
# ==========================================================================

def _official_entity(raw_entity: str, estate: EstateRepository) -> str | None:
    """Map an internal entity label to an official one, or refuse.

    ``CLABE:`` is internal. We try to resolve it to the vendor or employee that owns
    the account in the supplied estate; if the estate does not say who owns it, we
    return ``None`` rather than leaking the account number into the submission.
    """

    entity = raw_entity.strip()

    if entity.startswith(OFFICIAL_ENTITY_PREFIXES):
        return entity if len(entity) > 4 else None

    if entity.startswith("CLABE:"):
        clabe = entity[len("CLABE:"):].strip()
        if not clabe:
            return None

        vendor = estate.find_vendor_by_clabe(clabe)
        if vendor and str(vendor.get("rfc", "")).strip():
            return f"RFC:{str(vendor['rfc']).strip()}"

        employee = estate.find_employee_by_clabe(clabe)
        if employee and str(employee.get("emp_id", "")).strip():
            return f"EMP:{str(employee['emp_id']).strip()}"

        return None

    return None


def _lead_entity(
    outcome: CasePipelineOutcome,
    run_result: EstatePreverificationRunResult,
    estate: EstateRepository,
) -> str | None:
    """Pick one official entity for a lead, preserving deterministic order."""

    subjects: list[str] = []

    if outcome.preverification is not None:
        subjects.extend(outcome.preverification.case_state.subject_entities)

    for lead in run_result.leads:
        if lead.lead_id == outcome.lead_id:
            for entity in lead.subject_entities:
                if entity not in subjects:
                    subjects.append(entity)

    for subject in subjects:
        official = _official_entity(subject, estate)
        if official:
            return official

    return None


# ==========================================================================
# LEADS NOT PURSUED
# ==========================================================================

def _lead_signal(
    outcome: CasePipelineOutcome,
    run_result: EstatePreverificationRunResult,
) -> str:
    """Name the detector signals that opened this lead."""

    signal_types: list[str] = []

    for lead in run_result.leads:
        if lead.lead_id != outcome.lead_id:
            continue
        for observation in run_result.observations:
            if (
                observation.observation_id in lead.observation_ids
                and observation.signal_type not in signal_types
            ):
                signal_types.append(observation.signal_type)

    if not signal_types:
        return "no detector signal recorded for this lead"

    return ", ".join(sorted(signal_types))


def _tool_calls_made(outcome: CasePipelineOutcome) -> list[str]:
    """Report the Action Bank calls actually executed for this case."""

    if outcome.preverification is None:
        return []

    return [
        record.action_name
        for record in outcome.preverification.case_state.actions_taken
    ]


def _reviewed_evidence_summary(outcome: CasePipelineOutcome) -> str:
    """Describe the evidence actually inspected, so the reason is not generic."""

    if outcome.preverification is None:
        return "no case state was produced"

    case_state = outcome.preverification.case_state

    cited: list[str] = []
    for evidence in case_state.evidence:
        for ref in evidence.source_refs:
            label = f"{ref.source_table}.{ref.record_id}"
            if label not in cited:
                cited.append(label)

    if not cited:
        return (
            f"{len(case_state.actions_taken)} Action Bank call(s) returned no "
            "estate record for this subject"
        )

    shown = cited[:6]
    suffix = f" (+{len(cited) - len(shown)} more)" if len(cited) > len(shown) else ""
    return "reviewed " + ", ".join(shown) + suffix


def _closed_by(outcome: CasePipelineOutcome, gate_declined: bool) -> str | None:
    """Attribute the closure. ``None`` for an execution failure: nobody closed it."""

    if outcome.status == "error":
        return None

    if gate_declined:
        return "validator"

    stop_reason = outcome.stop_reason or ""

    if stop_reason.startswith("investigator_"):
        return "investigator"

    if stop_reason in {"review_blocked", "no_reviewable_hypothesis"}:
        return "challenger"

    return "investigator"


def _make_lead_not_pursued(
    outcome: CasePipelineOutcome,
    run_result: EstatePreverificationRunResult,
    estate: EstateRepository,
    *,
    reason: str,
    gate_declined: bool = False,
) -> LeadNotPursued | None:
    entity = _lead_entity(outcome, run_result, estate)
    if entity is None:
        # Without an official entity we cannot report this lead in the submission
        # without leaking an internal identifier. Dropping it is the safe choice.
        return None

    return LeadNotPursued(
        entity=entity,
        signal=_lead_signal(outcome, run_result),
        reason=reason,
        tool_calls_made=_tool_calls_made(outcome) or None,
        closed_by=_closed_by(outcome, gate_declined),
    )


# ==========================================================================
# PER-CASE AUTHORIZATION
# ==========================================================================

class CaseReport:
    """What happened to one case, for the operator's console."""

    def __init__(self, case_id: str, status: str, detail: str):
        self.case_id = case_id
        self.status = status
        self.detail = detail


def _authorize_case(
    outcome: CasePipelineOutcome,
    estate: EstateRepository,
    rule_registry: Any,
) -> tuple[SubmissionFinding | None, str, bool]:
    """Verify and authorize one reviewed case.

    Returns ``(finding_or_None, reason, gate_declined)``.  ``reason`` is always a
    specific, quotable explanation, never a generic phrase.
    """

    preverification = outcome.preverification
    if preverification is None:
        return (
            None,
            "Internal contract error: a ready case carried no preverification result.",
            False,
        )

    target_hypothesis_id = preverification.target_hypothesis_id
    if target_hypothesis_id is None:
        return (
            None,
            "Internal contract error: the case was ready for verification with no "
            "target hypothesis.",
            False,
        )

    hypothesis = next(
        (
            h
            for h in preverification.case_state.hypotheses
            if h.hypothesis_id == target_hypothesis_id
        ),
        None,
    )
    scheme_type = hypothesis.scheme_type if hypothesis else None

    if scheme_type != "phantom_vendor":
        return (
            None,
            (
                f"{_reviewed_evidence_summary(outcome)}; the surviving hypothesis is "
                f"{scheme_type!r}, for which no deterministic substantive verifier "
                "exists yet, so it remains a lead rather than a finding."
            ),
            False,
        )

    # The amount is produced by a fixed scope rule, before verification, and is
    # never adjusted afterwards to make reconciliation succeed.
    claim = build_phantom_vendor_claim(
        preverification.case_state,
        target_hypothesis_id,
        estate,
    )

    if claim.claimed_amount is None:
        return (
            None,
            (
                f"{_reviewed_evidence_summary(outcome)}; no monetary claim could be "
                "stated under the phantom_vendor scope rule: "
                + " ".join(claim.errors)
            ),
            False,
        )

    requested_rule_id = default_rule_id_for_scheme(scheme_type)
    if requested_rule_id is None:
        return (
            None,
            (
                f"{_reviewed_evidence_summary(outcome)}; no single registered rule "
                f"applies to {scheme_type}, and a rule may not be improvised."
            ),
            False,
        )

    try:
        post = run_postverification_pipeline(
            preverification_result=preverification,
            estate=estate,
            rule_registry=rule_registry,
            claimed_amount=claim.claimed_amount,
            requested_rule_id=requested_rule_id,
        )
    except PostVerificationBoundaryError as error:
        # A violated internal contract is a program defect, not a finding about
        # the case, and not evidentiary uncertainty either.
        return (
            None,
            f"Internal contract error during verification: {error}",
            False,
        )

    gate_decision = post.gate_decision

    if gate_decision is None or gate_decision.outcome != "authorize_probable":
        outcome_name = gate_decision.outcome if gate_decision else "not evaluated"
        failed = (
            "; ".join(gate_decision.failed_requirements)
            if gate_decision and gate_decision.failed_requirements
            else "no specific requirement recorded"
        )
        return (
            None,
            (
                f"{_reviewed_evidence_summary(outcome)}; claimed "
                f"MXN {claim.claimed_amount:,.2f} via {claim.formula}; the Evidence "
                f"Gate returned {outcome_name} because: {failed}."
            ),
            True,
        )

    try:
        finding = build_finding(
            case_state=post.preverification_result.case_state,
            target_hypothesis_id=target_hypothesis_id,
            gate_decision=gate_decision,
            verification_report=post.verification_report,
            rule_registry=rule_registry,
            requested_rule_id=requested_rule_id,
            claimed_amount=claim.claimed_amount,
            estate=estate,
        )
    except ValueError as error:
        # FindingBuilder is the last defensive layer. If it refuses an authorized
        # target, that is a real inconsistency and must be visible as such.
        return (
            None,
            f"Authorized by the Evidence Gate but refused by the FindingBuilder: {error}",
            True,
        )

    return finding, "authorized", False


# ==========================================================================
# RUN METADATA
# ==========================================================================

def _resolve_mxn_cost(
    llm_client: Any,
    *,
    replay_mode: bool,
    override: float | None,
) -> float:
    """Return a truthful cost, or abort.

    Never returns 0.0 as a stand-in for "unknown".
    """

    if override is not None:
        return override

    if replay_mode:
        # A replayed session issues no provider request, so nothing was billed.
        # This 0.0 is a measured fact, not a placeholder for an unknown number.
        return 0.0

    total = getattr(llm_client, "total_configured_cost", None)
    if total is None:
        raise SystemExit(
            "Refusing to write submission.json: the real MXN cost of this run is "
            "unknown (no token usage or no pricing was available for at least one "
            "call). Pass --mxn-cost with the real figure, or supply "
            "--mxn-per-1k-input and --mxn-per-1k-output so it can be computed. "
            "run_metadata.mxn_cost must never be a fabricated 0."
        )

    return float(total)


# ==========================================================================
# LLM CLIENT WIRING
# ==========================================================================

def _build_llm_client(args: argparse.Namespace) -> tuple[Any, bool]:
    """Construct the instrumented client. Returns ``(client, replay_mode)``."""

    from src.llm.runtime import InstrumentedLLMClient

    if args.replay:
        from src.llm.replay import ReplayLLMClient

        replay_client: Any = ReplayLLMClient(
            recording_path=args.replay,
            model=args.model,
        )
        return InstrumentedLLMClient(replay_client), True

    from src.llm.runtime import GeminiLLMClient, RecordingLLMClient

    pricing_fn = None
    if args.mxn_per_1k_input is not None and args.mxn_per_1k_output is not None:
        rate_in = args.mxn_per_1k_input
        rate_out = args.mxn_per_1k_output

        def pricing_fn(input_tokens: int, output_tokens: int) -> float:
            return (input_tokens / 1000.0) * rate_in + (
                output_tokens / 1000.0
            ) * rate_out

    instrumented = InstrumentedLLMClient(
        GeminiLLMClient(model=args.model),
        pricing_fn=pricing_fn,
    )

    if args.record:
        # Record the raw provider exchange, but keep the cost/usage accounting on
        # the instrumented client the caller reads metadata from.
        return (
            InstrumentedLLMClient(
                RecordingLLMClient(instrumented, recording_path=args.record),
                pricing_fn=pricing_fn,
            ),
            False,
        )

    return instrumented, False


# ==========================================================================
# MAIN
# ==========================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_audit",
        description="Audit an accounting estate and emit an official submission.json.",
    )
    parser.add_argument(
        "--estate",
        required=True,
        help="Path to the estate .db to audit. Supplied at runtime; never hardcoded.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Path to write submission.json.",
    )
    parser.add_argument(
        "--seed",
        required=True,
        type=int,
        help="The seed of the estate being audited, as reported by its generator.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "LLM model id. Falls back to GEMINI_MODEL, or to the recording's model "
            "in replay mode."
        ),
    )
    parser.add_argument(
        "--replay",
        default=None,
        help="Replay a recorded JSONL session offline. Makes no network call.",
    )
    parser.add_argument(
        "--record",
        default=None,
        help="Record this live session to a JSONL file for later replay.",
    )
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--max-initial-steps", type=int, default=6)
    parser.add_argument("--max-review-rounds", type=int, default=3)
    parser.add_argument("--max-followup-steps", type=int, default=3)
    parser.add_argument(
        "--enable-gnn",
        action="store_true",
        help="Enable optional GNN discovery. Discovery only; never proof.",
    )
    parser.add_argument(
        "--mxn-cost",
        type=float,
        default=None,
        help="The real MXN cost of this run, if you are measuring it externally.",
    )
    parser.add_argument("--mxn-per-1k-input", type=float, default=None)
    parser.add_argument("--mxn-per-1k-output", type=float, default=None)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    llm_client: Any = None,
    replay_mode: bool | None = None,
) -> int:
    """Run one audit.

    ``llm_client`` is a test seam only.  When it is supplied, no provider client is
    constructed; it must already be instrumented if truthful ``run_metadata`` is
    expected.
    """

    args = build_parser().parse_args(argv)

    # Total run duration, measured around everything: detectors, graph, optional
    # GNN, every LLM call and all deterministic verification. Not LLM latency.
    started = perf_counter()

    if llm_client is None:
        llm_client, replay_mode = _build_llm_client(args)
    elif replay_mode is None:
        replay_mode = args.replay is not None
    rule_registry = build_default_registry()

    findings: list[SubmissionFinding] = []
    leads_not_pursued: list[LeadNotPursued] = []
    reports: list[CaseReport] = []
    dropped_leads = 0

    with EstateRepository(args.estate) as estate:
        run_result = run_estate_preverification(
            estate=estate,
            llm_client=llm_client,
            max_cases=args.max_cases,
            max_initial_investigation_steps=args.max_initial_steps,
            max_review_rounds=args.max_review_rounds,
            max_followup_investigation_steps=args.max_followup_steps,
            rule_context=rule_context_for_scheme("phantom_vendor"),
            enable_gnn=args.enable_gnn,
        )

        for outcome in run_result.case_outcomes:
            gate_declined = False

            if outcome.status == "error":
                reason = (
                    "Execution failure while processing this case "
                    f"({outcome.error_type}: {outcome.error_message}). This is a "
                    "program failure, not a conclusion about the evidence."
                )
                reports.append(CaseReport(outcome.case_id, "error", reason))

            elif outcome.status == "blocked":
                detail = ""
                if outcome.preverification and outcome.preverification.detail:
                    detail = f" ({outcome.preverification.detail})"
                reason = (
                    f"{_reviewed_evidence_summary(outcome)}; the adversarial review "
                    f"stopped at {outcome.stop_reason}{detail}."
                )
                reports.append(CaseReport(outcome.case_id, "blocked", reason))

            else:
                finding, reason, gate_declined = _authorize_case(
                    outcome, estate, rule_registry
                )
                if finding is not None:
                    findings.append(finding)
                    reports.append(
                        CaseReport(
                            outcome.case_id,
                            "finding",
                            f"{finding.scheme_type} MXN {finding.peso_amount:,.2f}",
                        )
                    )
                    continue
                reports.append(CaseReport(outcome.case_id, "not_authorized", reason))

            lead = _make_lead_not_pursued(
                outcome,
                run_result,
                estate,
                reason=reason,
                gate_declined=gate_declined,
            )
            if lead is None:
                dropped_leads += 1
            else:
                leads_not_pursued.append(lead)

        mxn_cost = _resolve_mxn_cost(
            llm_client, replay_mode=replay_mode, override=args.mxn_cost
        )

        run_metadata = RunMetadata(
            llm_calls=int(getattr(llm_client, "llm_calls", 0)),
            mxn_cost=mxn_cost,
            wall_clock_seconds=perf_counter() - started,
            deterministic=replay_mode,
        )

        submission = build_submission(
            findings=findings,
            leads_not_pursued=leads_not_pursued,
            run_metadata=run_metadata,
            seed=args.seed,
        )
        write_submission_json(submission, args.out)

    # ------------------------------------------------------------------
    # Operator console. Deliberately explicit about what was NOT concluded.
    # ------------------------------------------------------------------
    print(f"estate            {args.estate}")
    print(f"observations      {run_result.metadata.observation_count}")
    print(f"leads             {run_result.metadata.lead_count}")
    print(f"cases processed   {run_result.metadata.processed_case_count}")
    print(f"gnn               {run_result.metadata.gnn.status}")
    print(f"llm calls         {run_metadata.llm_calls}")
    print(f"mxn cost          {run_metadata.mxn_cost}")
    print(f"wall clock (s)    {run_metadata.wall_clock_seconds:.2f}")
    print("-" * 70)
    for report in reports:
        print(f"  [{report.status}] {report.case_id}: {report.detail}")
    if dropped_leads:
        print(
            f"  ({dropped_leads} lead(s) omitted from leads_not_pursued: no official "
            "RFC/EMP entity could be resolved without leaking a CLABE)"
        )
    print("-" * 70)
    print(f"findings          {len(findings)}")
    print(f"leads_not_pursued {len(leads_not_pursued)}")
    print(f"submission        {Path(args.out)}")

    if run_result.metadata.error_count:
        print(
            f"\nWARNING: {run_result.metadata.error_count} case(s) failed to execute. "
            "See the [error] lines above. These are program failures, not findings."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
