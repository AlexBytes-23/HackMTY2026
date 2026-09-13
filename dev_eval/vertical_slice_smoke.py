from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from src.agents.challenger import ChallengerReview
from src.agents.method_critic import MethodCriticReview
from src.core.estate import EstateRepository
from src.core.models import (
    CaseEvidence,
    CaseState,
    EvidenceRef,
    Hypothesis,
    InvestigationLoopResult,
)
from src.investigation.postverification_pipeline import run_postverification_pipeline
from src.investigation.preverification_pipeline import PreVerificationResult
from src.investigation.review_orchestrator import (
    ReviewOrchestrationResult,
    ReviewRound,
)
from src.output.finding_builder import build_finding
from src.output.models import RunMetadata
from src.output.submission_builder import (
    build_submission,
    write_submission_json,
)
from src.rules.rule_registry import RuleDefinition, RuleRegistry


ROOT = Path(__file__).resolve().parents[1]

OFFICIAL_CANDIDATES = [
    ROOT / "official_materials" / "student-materials" / "forensic-auditor",
    ROOT / "official_materials" / "forensic-auditor-official",
]

OFFICIAL_DIR = next(
    (
        path
        for path in OFFICIAL_CANDIDATES
        if (path / "estate_schema.sql").exists()
    ),
    None,
)

if OFFICIAL_DIR is None:
    raise RuntimeError(
        "Could not find the official HackMTY materials / estate_schema.sql."
    )

DEMO_DIR = ROOT / "demo"
DEMO_DIR.mkdir(exist_ok=True)

ESTATE_PATH = DEMO_DIR / "vertical_smoke_estate.db"
SUBMISSION_PATH = DEMO_DIR / "submission.json"

if ESTATE_PATH.exists():
    ESTATE_PATH.unlink()


# ============================================================
# 1. CREATE REAL SQLITE ESTATE USING THE OFFICIAL SCHEMA
# ============================================================

schema_sql = (OFFICIAL_DIR / "estate_schema.sql").read_text(
    encoding="utf-8"
)

vendor_rfc = "DEM240101AB1"
receiver_rfc = "AUD240101XY1"

invoice_1 = "11111111-1111-4111-8111-111111111111"
invoice_2 = "22222222-2222-4222-8222-222222222222"

conn = sqlite3.connect(ESTATE_PATH)

try:
    conn.executescript(schema_sql)

    conn.execute(
        """
        INSERT INTO vendors (
            rfc,
            legal_name,
            registered_date,
            address,
            bank_clabe,
            category,
            contact_email
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            vendor_rfc,
            "Servicios Demostracion Integral SA de CV",
            "2023-08-15",
            "Av. Ejemplo 123, Monterrey, Nuevo Leon",
            "012180001234567890",
            "Servicios profesionales",
            "contacto@demostracion.invalid",
        ),
    )

    conn.executemany(
        """
        INSERT INTO invoices (
            uuid,
            issuer_rfc,
            receiver_rfc,
            issue_date,
            subtotal,
            iva,
            total,
            concepto_text,
            uso_cfdi,
            forma_pago,
            metodo_pago,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                invoice_1,
                vendor_rfc,
                receiver_rfc,
                "2025-03-10",
                500.00,
                80.00,
                580.00,
                "Servicios especializados marzo",
                "G03",
                "03",
                "PUE",
                "Vigente",
            ),
            (
                invoice_2,
                vendor_rfc,
                receiver_rfc,
                "2025-04-10",
                500.00,
                80.00,
                580.00,
                "Servicios especializados abril",
                "G03",
                "03",
                "PUE",
                "Vigente",
            ),
        ],
    )

    conn.execute(
        """
        INSERT INTO efos_list (
            rfc,
            legal_name,
            status,
            publication_date
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            vendor_rfc,
            "Servicios Demostracion Integral SA de CV",
            "Definitivo",
            "2024-11-20",
        ),
    )

    conn.commit()

finally:
    conn.close()


# ============================================================
# 2. BUILD A CASE AT THE PREVERIFICATION BOUNDARY
#
# IMPORTANT:
# This deliberately does NOT fake the Verifier, Gate,
# FindingBuilder or SubmissionBuilder.
#
# We are isolating the previously unproven final vertical.
# ============================================================

evidence_refs = [
    EvidenceRef(
        source_table="efos_list",
        record_id=vendor_rfc,
        note="Synthetic EFOS record for vertical integration test.",
    ),
    EvidenceRef(
        source_table="invoices",
        record_id=invoice_1,
        note="First invoice issued by the matching RFC.",
    ),
    EvidenceRef(
        source_table="invoices",
        record_id=invoice_2,
        note="Second invoice issued by the matching RFC.",
    ),
]

case_evidence = CaseEvidence(
    evidence_id="EV-PV-001",
    statement=(
        "Invoice records cite an issuer RFC that also appears in "
        "the estate EFOS table."
    ),
    direction="for",
    source_refs=evidence_refs,
    produced_by="vertical_slice_smoke",
    observation_id=None,
)

hypothesis = Hypothesis(
    hypothesis_id="H-PV-001",
    statement=(
        "The cited invoice activity is consistent with the "
        "phantom_vendor scheme and warrants deterministic verification."
    ),
    scheme_type="phantom_vendor",
    status="supported",
    supporting_evidence_ids=["EV-PV-001"],
    counter_evidence_ids=[],
    unresolved_questions=[],
)

case_state = CaseState(
    case_id="CASE-VERTICAL-001",
    lead_id="LEAD-VERTICAL-001",
    lead_reason=(
        "Exact RFC relationship between invoice issuer and EFOS record."
    ),
    subject_entities=[vendor_rfc],
    observations=[],
    hypotheses=[hypothesis],
    evidence=[case_evidence],
    unknowns=[],
    actions_taken=[],
    status="ready_for_verification",
)


# ============================================================
# 3. REPRESENT AN ALREADY-COMPLETED ADVERSARIAL REVIEW
#
# model_construct is intentional here:
# Challenger / Method Critic have independent tests.
#
# This test is NOT validating LLM parsing.
# ============================================================

challenger_review = ChallengerReview.model_construct(
    outcome="survives",
)

method_critic_review = MethodCriticReview.model_construct(
    outcome="clear",
)

review_round = ReviewRound.model_construct(
    round_number=1,
    target_hypothesis_id="H-PV-001",
    challenger_review=challenger_review,
    method_critic_review=method_critic_review,
    investigator_result=None,
)

review_result = ReviewOrchestrationResult.model_construct(
    case_state=case_state,
    target_hypothesis_id="H-PV-001",
    rounds=[review_round],
    review_rounds=1,
    ready_for_verification=True,
    stop_reason="ready_for_verification",
)

initial_investigation = InvestigationLoopResult.model_construct(
    case_state=case_state,
    decisions=[],
    iterations=0,
    stop_reason="request_review",
)

preverification = PreVerificationResult.model_construct(
    case_state=case_state,
    initial_investigation=initial_investigation,
    review_result=review_result,
    target_hypothesis_id="H-PV-001",
    ready_for_verification=True,
    stop_reason="ready_for_verification",
    detail=None,
)


# ============================================================
# 4. EXPLICIT RULE REGISTRY
# ============================================================

registry = RuleRegistry()

registry.register(
    RuleDefinition(
        rule_id="PV_RULE",
        title="EFOS and invoice issuer relationship",
        source="CFF",
        source_reference="69-B",
        applies_to=["phantom_vendor"],
        supports=[
            "An exact RFC match between an invoice issuer and an EFOS record."
        ],
        does_not_prove=[
            "Criminal intent.",
            "That every transaction involving the entity is simulated.",
        ],
        requirements=[
            "Exact RFC match.",
            "Resolved invoice evidence.",
            "Resolved EFOS evidence.",
            "Deterministic peso reconciliation.",
        ],
        exceptions=[],
    )
)


# ============================================================
# 5. EXPLICIT MONETARY CLAIM
#
# invoice 1 = 580
# invoice 2 = 580
# total claim = 1,160 MXN
#
# The Verifier must test this claim.
# It must NOT manufacture it.
# ============================================================

claimed_amount = 1160.00


# ============================================================
# 6. REAL POSTVERIFICATION
# ============================================================

started = time.perf_counter()

with EstateRepository(ESTATE_PATH) as estate:

    result = run_postverification_pipeline(
        preverification,
        estate=estate,
        rule_registry=registry,
        claimed_amount=claimed_amount,
        requested_rule_id="PV_RULE",
        useful_actions_remain=False,
    )

    print("\n=== POSTVERIFICATION ===")
    print("status:", result.status)

    if result.verification_report is None:
        raise RuntimeError("No VerificationReport produced.")

    report = result.verification_report

    print("\nVerification checks:")
    for check in report.checks:
        print(
            f"  {check.check_id:<50} "
            f"{check.status}"
        )

    print("\nResolved exhibits:")
    for ref in report.resolved_exhibits:
        print(f"  {ref.source_table}.{ref.record_id}")

    print("\nReconciliation:")
    print(report.reconciliation)

    if result.gate_decision is None:
        raise RuntimeError("No EvidenceGate decision produced.")

    print("\nGate:")
    print("  outcome:", result.gate_decision.outcome)
    print(
        "  confidence:",
        result.gate_decision.authorized_confidence,
    )

    if result.gate_decision.failed_requirements:
        print("  failed requirements:")
        for item in result.gate_decision.failed_requirements:
            print("   -", item)

    if result.gate_decision.outcome != "authorize_probable":
        raise RuntimeError(
            "Vertical slice did not reach authorize_probable. "
            f"Outcome: {result.gate_decision.outcome}"
        )

    finding = build_finding(
        case_state=preverification.case_state,
        target_hypothesis_id="H-PV-001",
        gate_decision=result.gate_decision,
        verification_report=report,
        rule_registry=registry,
        requested_rule_id="PV_RULE",
        claimed_amount=claimed_amount,
        estate=estate,
    )


# ============================================================
# 7. REAL SUBMISSION
# ============================================================

elapsed = time.perf_counter() - started

metadata = RunMetadata(
    llm_calls=0,
    mxn_cost=0.0,
    wall_clock_seconds=elapsed,
    cost_by_role=None,
    deterministic=True,
)

submission = build_submission(
    findings=[finding],
    leads_not_pursued=[],
    run_metadata=metadata,
    seed=42,
)

write_submission_json(
    submission,
    SUBMISSION_PATH,
)


# ============================================================
# 8. HUMAN-READABLE RESULT
# ============================================================

print("\n=== FINDING ===")
print("scheme:", finding.scheme_type)
print("entities:", finding.entities)
print("peso_amount:", finding.peso_amount)
print("confidence:", finding.confidence)
print("exhibits:", len(finding.exhibits))
print("money trail steps:", len(finding.money_trail))

print("\n=== OUTPUT ===")
print("estate:")
print(" ", ESTATE_PATH)
print("submission:")
print(" ", SUBMISSION_PATH)

print("\nVERTICAL SLICE COMPLETE")
