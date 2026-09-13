from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from src.core.estate import EstateRepository
from src.core.models import CaseState
from src.investigation.estate_pipeline import run_estate_preverification
from src.investigation.postverification_pipeline import run_postverification_pipeline
from src.llm.replay import ReplayLLMClient
from src.llm.runtime import GeminiLLMClient, RecordingLLMClient
from src.output.finding_builder import build_finding
from src.output.models import LeadNotPursued, RunMetadata
from src.output.submission_builder import build_submission, write_submission_json
from src.rules.rule_registry import RuleDefinition, RuleRegistry


PHANTOM_RULE_ID = "CFF-69B-DEFINITIVO"


class CountingLLMClient:
    """Minimal transparent wrapper: count actual complete() calls."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return self.inner.complete(system_prompt, user_prompt)


def build_rule_registry() -> RuleRegistry:
    registry = RuleRegistry()

    registry.register(
        RuleDefinition(
            rule_id=PHANTOM_RULE_ID,
            title="Definitive EFOS relationship with cited invoice issuer",
            source="Código Fiscal de la Federación",
            source_reference="Artículo 69-B",
            applies_to=["phantom_vendor"],
            supports=[
                "Exact RFC correspondence between a cited invoice issuer "
                "and an EFOS record with definitivo status."
            ],
            does_not_prove=[
                "Criminal intent.",
                "That every transaction involving the entity is simulated.",
                "Responsibility of a particular natural person.",
            ],
            requirements=[
                "Resolved invoice evidence.",
                "Resolved EFOS evidence.",
                "EFOS status definitivo.",
                "Exact RFC correspondence.",
                "Deterministic peso reconciliation.",
            ],
            exceptions=[],
        )
    )

    return registry


def find_hypothesis(case_state: CaseState, hypothesis_id: str):
    for hypothesis in case_state.hypotheses:
        if hypothesis.hypothesis_id == hypothesis_id:
            return hypothesis

    raise RuntimeError(
        f"Hypothesis {hypothesis_id!r} not found in "
        f"case {case_state.case_id!r}."
    )


def supporting_refs(case_state: CaseState, hypothesis_id: str):
    hypothesis = find_hypothesis(case_state, hypothesis_id)

    supporting_ids = set(hypothesis.supporting_evidence_ids)
    refs = []

    for evidence in case_state.evidence:
        if evidence.evidence_id in supporting_ids:
            refs.extend(evidence.source_refs)

    # Stable de-duplication.
    unique = {}
    for ref in refs:
        key = (ref.source_table, str(ref.record_id))
        unique.setdefault(key, ref)

    return list(unique.values())


def derive_phantom_claim(
    case_state: CaseState,
    hypothesis_id: str,
    estate: EstateRepository,
) -> float | None:
    """
    Establish the monetary claim BEFORE verification.

    Policy:
    Sum the `total` values of unique invoice records already cited by
    supporting CaseEvidence.

    The verifier receives this independently-established claim and tests it.
    It does not search for a convenient amount after reconciliation.
    """

    refs = supporting_refs(case_state, hypothesis_id)

    total = 0.0
    invoice_count = 0

    for ref in refs:
        if ref.source_table != "invoices":
            continue

        record = estate.get_record("invoices", str(ref.record_id))
        if not record:
            continue

        amount = record.get("total")

        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            continue

        if amount <= 0:
            continue

        total += float(amount)
        invoice_count += 1

    if invoice_count == 0:
        return None

    return round(total, 2)


def has_definitive_efos_match(
    case_state: CaseState,
    hypothesis_id: str,
    estate: EstateRepository,
) -> tuple[bool, str]:
    """
    Conservative promotion policy.

    Presence on efos_list alone is insufficient for a probable finding.
    At least one supporting invoice issuer RFC must match a supporting
    EFOS record whose status is exactly 'definitivo' (case-insensitive).
    """

    refs = supporting_refs(case_state, hypothesis_id)

    invoice_rfcs = set()
    definitive_efos_rfcs = set()
    seen_efos_statuses = []

    for ref in refs:
        record = estate.get_record(ref.source_table, str(ref.record_id))

        if not record:
            continue

        if ref.source_table == "invoices":
            rfc = record.get("issuer_rfc")
            if isinstance(rfc, str) and rfc.strip():
                invoice_rfcs.add(rfc.strip())

        elif ref.source_table == "efos_list":
            rfc = record.get("rfc")
            status = record.get("status")

            if isinstance(status, str):
                normalized_status = status.strip().casefold()
            else:
                normalized_status = ""

            if isinstance(rfc, str) and rfc.strip():
                seen_efos_statuses.append(
                    f"{rfc.strip()}:{normalized_status or 'missing'}"
                )

                if normalized_status == "definitivo":
                    definitive_efos_rfcs.add(rfc.strip())

    intersection = invoice_rfcs.intersection(definitive_efos_rfcs)

    if intersection:
        return (
            True,
            "Definitive EFOS RFC match: "
            + ", ".join(sorted(intersection)),
        )

    if seen_efos_statuses:
        return (
            False,
            "No supporting invoice issuer matched an EFOS record with "
            "definitivo status. Seen: "
            + ", ".join(seen_efos_statuses),
        )

    return False, "No supporting definitive EFOS evidence was available."


def lookup_lead(run_result, lead_id: str):
    for lead in run_result.leads:
        if lead.lead_id == lead_id:
            return lead
    return None


def tool_calls_from_case(case_state: CaseState | None) -> list[str] | None:
    if case_state is None:
        return None

    names = []

    for action in case_state.actions_taken:
        name = getattr(action, "action_name", None)
        if isinstance(name, str) and name:
            names.append(name)

    return names or None


def make_not_pursued(
    *,
    entity: str,
    signal: str,
    reason: str,
    closed_by: str,
    case_state: CaseState | None = None,
) -> LeadNotPursued:
    return LeadNotPursued(
        entity=entity,
        signal=signal,
        reason=reason,
        tool_calls_made=tool_calls_from_case(case_state),
        closed_by=closed_by,
    )


def blocked_case_to_lead(run_result, outcome) -> LeadNotPursued:
    lead = lookup_lead(run_result, outcome.lead_id)

    pre = outcome.preverification
    case_state = pre.case_state if pre is not None else None

    if lead and lead.subject_entities:
        entity = ", ".join(lead.subject_entities)
    elif case_state and case_state.subject_entities:
        entity = ", ".join(case_state.subject_entities)
    else:
        entity = outcome.case_id

    signal = (
        lead.reason_opened
        if lead is not None
        else f"Case {outcome.case_id} did not reach verification."
    )

    stop_reason = outcome.stop_reason or "review_blocked"

    if stop_reason.startswith("investigator_"):
        closed_by = "investigator"
    else:
        # Official contract has no Method Critic actor.
        # Adversarial-review closures are represented as challenger.
        closed_by = "challenger"

    return make_not_pursued(
        entity=entity,
        signal=signal,
        reason=f"Not promoted to a finding: {stop_reason}.",
        closed_by=closed_by,
        case_state=case_state,
    )


def build_llm(args):
    if args.replay is not None:
        replay = ReplayLLMClient(
            recording_path=args.replay,
            provider="gemini",
            model=args.model,
        )

        return CountingLLMClient(replay), True, 0.0

    api_key = os.getenv("GEMINI_API_KEY")
    model = args.model or os.getenv("GEMINI_MODEL")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. "
            "Set it or run using --replay <recording.jsonl>."
        )

    if not model:
        raise RuntimeError(
            "GEMINI_MODEL is not configured. "
            "Set it or pass --model."
        )

    if args.mxn_cost is None:
        raise RuntimeError(
            "Live mode requires --mxn-cost. "
            "Do not silently encode an unknown LLM cost as zero. "
            "For the final offline replay run, cost is truthfully 0.0."
        )

    raw_client = GeminiLLMClient(
        api_key=api_key,
        model=model,
    )

    recording_client = RecordingLLMClient(
        inner_client=raw_client,
        recording_path=args.recording,
        provider="gemini",
        model=model,
        save_prompts=False,
    )

    return (
        CountingLLMClient(recording_client),
        False,
        float(args.mxn_cost),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run the LegLens forensic audit pipeline."
    )

    parser.add_argument(
        "--estate",
        required=True,
        help="Path to official-schema estate.db",
    )

    parser.add_argument(
        "--output",
        required=True,
        help="Path for submission.json",
    )

    parser.add_argument(
        "--max-cases",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--enable-gnn",
        action="store_true",
        help="Enable optional GNN discovery lane.",
    )

    parser.add_argument(
        "--model",
        default=None,
        help="Gemini model. Otherwise GEMINI_MODEL is used.",
    )

    parser.add_argument(
        "--recording",
        default="demo/llm_recording.jsonl",
        help="JSONL recording path for live Gemini calls.",
    )

    parser.add_argument(
        "--replay",
        default=None,
        help="Replay a prior JSONL recording with no network calls.",
    )

    parser.add_argument(
        "--mxn-cost",
        type=float,
        default=None,
        help=(
            "Actual/externally established MXN cost for this LIVE run. "
            "Replay runs truthfully use 0.0."
        ),
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    args = parser.parse_args()

    estate_path = Path(args.estate)
    output_path = Path(args.output)

    if not estate_path.exists():
        raise FileNotFoundError(estate_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    Path(args.recording).parent.mkdir(parents=True, exist_ok=True)

    llm_client, deterministic, mxn_cost = build_llm(args)
    registry = build_rule_registry()

    started = time.perf_counter()

    findings = []
    leads_not_pursued = []
    runtime_errors = []

    with EstateRepository(estate_path) as estate:
        pre_run = run_estate_preverification(
            estate=estate,
            llm_client=llm_client,
            max_cases=args.max_cases,
            enable_gnn=args.enable_gnn,
            rule_context=[
                registry.get_rule(PHANTOM_RULE_ID).model_dump()
            ],
        )

        for outcome in pre_run.case_outcomes:
            if outcome.status == "error":
                runtime_errors.append(
                    f"{outcome.case_id}: "
                    f"{outcome.error_type}: "
                    f"{outcome.error_message}"
                )
                continue

            if outcome.status == "blocked":
                leads_not_pursued.append(
                    blocked_case_to_lead(pre_run, outcome)
                )
                continue

            if outcome.status != "ready_for_verification":
                runtime_errors.append(
                    f"{outcome.case_id}: unexpected status "
                    f"{outcome.status!r}"
                )
                continue

            pre = outcome.preverification

            if pre is None:
                runtime_errors.append(
                    f"{outcome.case_id}: ready outcome has no "
                    "PreVerificationResult."
                )
                continue

            target_id = pre.target_hypothesis_id

            if target_id is None:
                runtime_errors.append(
                    f"{outcome.case_id}: ready outcome has no target."
                )
                continue

            hypothesis = find_hypothesis(
                pre.case_state,
                target_id,
            )

            entity = (
                ", ".join(pre.case_state.subject_entities)
                if pre.case_state.subject_entities
                else outcome.case_id
            )

            if hypothesis.scheme_type != "phantom_vendor":
                leads_not_pursued.append(
                    make_not_pursued(
                        entity=entity,
                        signal=hypothesis.statement,
                        reason=(
                            "Investigation produced a reviewable hypothesis, "
                            f"but no scheme-specific deterministic verifier "
                            f"is implemented for "
                            f"{hypothesis.scheme_type!r}. "
                            "LegLens refuses to promote the hypothesis to an "
                            "official finding."
                        ),
                        closed_by="validator",
                        case_state=pre.case_state,
                    )
                )
                continue

            definitive_ok, definitive_reason = (
                has_definitive_efos_match(
                    pre.case_state,
                    target_id,
                    estate,
                )
            )

            if not definitive_ok:
                leads_not_pursued.append(
                    make_not_pursued(
                        entity=entity,
                        signal=hypothesis.statement,
                        reason=(
                            "Phantom-vendor promotion refused: "
                            + definitive_reason
                        ),
                        closed_by="validator",
                        case_state=pre.case_state,
                    )
                )
                continue

            claimed_amount = derive_phantom_claim(
                pre.case_state,
                target_id,
                estate,
            )

            post = run_postverification_pipeline(
                pre,
                estate=estate,
                rule_registry=registry,
                claimed_amount=claimed_amount,
                requested_rule_id=PHANTOM_RULE_ID,
                useful_actions_remain=False,
            )

            if post.gate_decision is None:
                runtime_errors.append(
                    f"{outcome.case_id}: no EvidenceGate decision."
                )
                continue

            if post.gate_decision.outcome != "authorize_probable":
                reason = post.gate_decision.reason

                if post.gate_decision.failed_requirements:
                    reason += " Failed requirements: " + "; ".join(
                        post.gate_decision.failed_requirements
                    )

                leads_not_pursued.append(
                    make_not_pursued(
                        entity=entity,
                        signal=hypothesis.statement,
                        reason=reason,
                        closed_by="validator",
                        case_state=pre.case_state,
                    )
                )
                continue

            if post.verification_report is None:
                runtime_errors.append(
                    f"{outcome.case_id}: authorized without "
                    "VerificationReport."
                )
                continue

            finding = build_finding(
                case_state=pre.case_state,
                target_hypothesis_id=target_id,
                gate_decision=post.gate_decision,
                verification_report=post.verification_report,
                rule_registry=registry,
                requested_rule_id=PHANTOM_RULE_ID,
                claimed_amount=claimed_amount,
                estate=estate,
            )

            findings.append(finding)

    if runtime_errors:
        joined = "\n  - ".join(runtime_errors)

        raise RuntimeError(
            "Structural/runtime errors occurred. "
            "They are not being disguised as forensic uncertainty:\n  - "
            + joined
        )

    wall_clock_seconds = time.perf_counter() - started

    metadata = RunMetadata(
        llm_calls=llm_client.calls,
        mxn_cost=mxn_cost,
        wall_clock_seconds=wall_clock_seconds,
        cost_by_role=None,
        deterministic=deterministic,
    )

    submission = build_submission(
        findings=findings,
        leads_not_pursued=leads_not_pursued,
        run_metadata=metadata,
        seed=args.seed,
    )

    write_submission_json(
        submission,
        output_path,
    )

    print("\n==========================================")
    print(" LEGLENS AUDIT COMPLETE")
    print("==========================================")
    print("observations:        ", pre_run.metadata.observation_count)
    print("leads:               ", pre_run.metadata.lead_count)
    print("cases processed:     ", pre_run.metadata.processed_case_count)
    print("ready cases:         ", pre_run.metadata.ready_for_verification_count)
    print("blocked cases:       ", pre_run.metadata.blocked_case_count)
    print("findings:            ", len(findings))
    print("leads not pursued:   ", len(leads_not_pursued))
    print("LLM calls:           ", llm_client.calls)
    print("deterministic replay:", deterministic)
    print("wall clock seconds:  ", round(wall_clock_seconds, 3))
    print("output:              ", output_path)


if __name__ == "__main__":
    main()
