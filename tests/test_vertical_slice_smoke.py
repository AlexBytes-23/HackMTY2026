import pytest
import sqlite3
import json
from pathlib import Path

from src.core.estate import EstateRepository, OFFICIAL_COLUMNS
from src.core.models import (
    CaseState,
    Hypothesis,
    CaseEvidence,
    EvidenceRef,
    InvestigationLoopResult
)
from src.investigation.preverification_pipeline import PreVerificationResult
from src.investigation.review_orchestrator import ReviewOrchestrationResult, ReviewRound
from src.agents.challenger import ChallengerReview
from src.agents.method_critic import MethodCriticReview
from src.investigation.postverification_pipeline import run_postverification_pipeline
from src.output.finding_builder import build_finding
from src.output.submission_builder import build_submission, write_submission_json
from src.output.models import RunMetadata
from src.rules.rule_registry import RuleRegistry, RuleDefinition

@pytest.fixture
def temp_estate(tmp_path):
    db_path = tmp_path / "estate.db"
    conn = sqlite3.connect(db_path)
    
    # We dynamically synthesize CREATE TABLE from OFFICIAL_COLUMNS 
    # matching the canonical pattern established in tests/test_estate_contract.py.
    # Note: There is no authoritative raw SQL DDL file exposed natively by the project.
    for table, cols in OFFICIAL_COLUMNS.items():
        if table == "ledger":
            conn.execute(f"CREATE TABLE {table} (entry_id INTEGER PRIMARY KEY, {','.join(c for c in cols if c != 'entry_id')})")
        elif table == "vendors":
            conn.execute(f"CREATE TABLE {table} (rfc TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'rfc')})")
        elif table == "employees":
            conn.execute(f"CREATE TABLE {table} (emp_id TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'emp_id')})")
        elif table == "invoices":
            conn.execute(f"CREATE TABLE {table} (uuid TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'uuid')})")
        elif table == "bank_txns":
            conn.execute(f"CREATE TABLE {table} (txn_id TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'txn_id')})")
        elif table == "purchase_orders":
            conn.execute(f"CREATE TABLE {table} (po_id TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'po_id')})")
        elif table == "contracts":
            conn.execute(f"CREATE TABLE {table} (contract_id TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'contract_id')})")
        elif table == "efos_list":
            conn.execute(f"CREATE TABLE {table} (rfc TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'rfc')})")
            
    # The EFOS row needs a definitive status and a publication_date, and the
    # invoices need an issue_date on or after it: the verifier will not treat a
    # bare RFC intersection as an established phantom-vendor link.
    conn.execute(
        "INSERT INTO efos_list (rfc, status, publication_date) "
        "VALUES ('EFOS010101XYZ', 'definitivo', '2025-03-01')"
    )
    conn.execute("INSERT INTO invoices (uuid, issuer_rfc, total, issue_date) VALUES ('INV-001', 'EFOS010101XYZ', 400.0, '2025-04-01')")
    conn.execute("INSERT INTO invoices (uuid, issuer_rfc, total, issue_date) VALUES ('INV-002', 'EFOS010101XYZ', 600.0, '2025-04-18')")
    
    conn.commit()
    conn.close()
    
    repo = EstateRepository(db_path)
    yield repo
    repo.close()

def test_deterministic_vertical_slice_phantom_vendor(temp_estate, tmp_path):
    rule_registry = RuleRegistry()
    rule_registry.register(RuleDefinition(
        rule_id="RULE-PV",
        title="Phantom Vendor Rule",
        source="Law",
        source_reference="Art 69-B",
        applies_to=["phantom_vendor"]
    ))
    
    target_id = "hyp-target"
    
    evidence_1 = CaseEvidence(
        evidence_id="ev-1",
        statement="EFOS record exists",
        direction="for",
        produced_by="test",
        source_refs=[EvidenceRef(source_table="efos_list", record_id="EFOS010101XYZ")]
    )
    evidence_2 = CaseEvidence(
        evidence_id="ev-2",
        statement="Invoice 1 exists",
        direction="for",
        produced_by="test",
        source_refs=[EvidenceRef(source_table="invoices", record_id="INV-001")]
    )
    evidence_3 = CaseEvidence(
        evidence_id="ev-3",
        statement="Invoice 2 exists",
        direction="for",
        produced_by="test",
        source_refs=[EvidenceRef(source_table="invoices", record_id="INV-002")]
    )
    
    case_state = CaseState(
        case_id="case-1",
        lead_id="lead-1",
        status="ready_for_verification",
        hypotheses=[
            Hypothesis(
                hypothesis_id=target_id,
                statement="The vendor is a phantom vendor.",
                scheme_type="phantom_vendor",
                status="supported",
                supporting_evidence_ids=["ev-1", "ev-2", "ev-3"]
            )
        ],
        evidence=[evidence_1, evidence_2, evidence_3]
    )
    
    challenger_review = ChallengerReview(
        target_hypothesis_id=target_id,
        outcome="survives",
        reasoning_summary="No alternatives found.",
        survives_challenge=True
    )
    method_critic_review = MethodCriticReview(
        target_hypothesis_id=target_id,
        outcome="clear",
        reasoning_summary="Methodology is sound."
    )
    review_round = ReviewRound(
        round_number=1,
        target_hypothesis_id=target_id,
        challenger_review=challenger_review,
        method_critic_review=method_critic_review
    )
    review_orchestration_result = ReviewOrchestrationResult(
        case_state=case_state,
        target_hypothesis_id=target_id,
        rounds=[review_round],
        review_rounds=1,
        ready_for_verification=True,
        stop_reason="ready_for_verification"
    )
    
    inv_loop_result = InvestigationLoopResult(
        case_state=case_state,
        decisions=[],
        iterations=0,
        stop_reason="ready_for_verification"
    )
    
    pre_result = PreVerificationResult(
        case_state=case_state,
        target_hypothesis_id=target_id,
        ready_for_verification=True,
        stop_reason="ready_for_verification",
        initial_investigation=inv_loop_result,
        review_result=review_orchestration_result
    )
    
    claimed_amount = 1000.0 # 400 + 600
    requested_rule_id = "RULE-PV"
    
    post_result = run_postverification_pipeline(
        preverification_result=pre_result,
        estate=temp_estate,
        rule_registry=rule_registry,
        claimed_amount=claimed_amount,
        requested_rule_id=requested_rule_id
    )
    
    assert post_result.status == "evaluated"
    assert post_result.target_hypothesis_id == target_id
    
    report = post_result.verification_report
    assert report.target_hypothesis_id == target_id
    assert not report.internal_errors
    
    checks_by_id = {c.check_id: c for c in report.checks}
    assert checks_by_id["PHANTOM-EFOS-INVOICE-LINK"].critical is True
    assert checks_by_id["PHANTOM-EFOS-INVOICE-LINK"].status == "verified"
    
    assert checks_by_id["PESO-RECONCILIATION"].critical is True
    assert checks_by_id["PESO-RECONCILIATION"].status == "verified"
    
    assert post_result.gate_decision.outcome == "authorize_probable"
    assert post_result.gate_decision.authorized_confidence == "probable"
    
    finding = build_finding(
        case_state=post_result.preverification_result.case_state,
        target_hypothesis_id=post_result.target_hypothesis_id,
        gate_decision=post_result.gate_decision,
        verification_report=post_result.verification_report,
        rule_registry=rule_registry,
        requested_rule_id=post_result.requested_rule_id,
        claimed_amount=post_result.claimed_amount,
        estate=temp_estate
    )
    
    assert finding.scheme_type == "phantom_vendor"
    assert finding.confidence == "probable"
    assert finding.peso_amount == 1000.0
    assert "RFC:EFOS010101XYZ" in finding.entities
    assert len(finding.entities) == 1
    assert len(finding.exhibits) == 3
    assert finding.money_trail == []
    assert len(finding.narrative.split()) <= 150
    
    # 2. Strict narrative safety assertions
    narrative_lower = finding.narrative.lower()
    for forbidden_claim in [
        "fraud proven",
        "proves fraud",
        "criminal intent proven",
        "proves criminal intent",
        "fake invoice proven",
        "proves fake invoice",
        "simulated transaction proven",
        "proves simulated transaction"
    ]:
        assert forbidden_claim not in narrative_lower, f"Narrative wrongly affirmatively claims: {forbidden_claim}"
    
    metadata = RunMetadata(
        llm_calls=0,
        mxn_cost=0.0,
        wall_clock_seconds=1.0,
        deterministic=True
    )
    seed = 8675309
    
    submission = build_submission(
        findings=[finding],
        leads_not_pursued=[],
        run_metadata=metadata,
        seed=seed
    )
    
    assert submission.seed == seed
    assert len(submission.findings) == 1
    assert submission.leads_not_pursued == []
    assert submission.run_metadata.mxn_cost == 0.0
    
    out_path = tmp_path / "submission.json"
    write_submission_json(submission, out_path)
    
    assert out_path.exists()
    
    with open(out_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    assert data["seed"] == seed
    assert len(data["findings"]) == 1
    assert data["leads_not_pursued"] == []
    assert data["run_metadata"]["mxn_cost"] == 0.0
    
    # 3. Recursive leak assertion
    def assert_no_leaks(obj, forbidden_keys):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert k not in forbidden_keys, f"Internal key '{k}' leaked into JSON output."
                assert_no_leaks(v, forbidden_keys)
        elif isinstance(obj, list):
            for item in obj:
                assert_no_leaks(item, forbidden_keys)

    forbidden_internal = {
        "verification_report",
        "gate_decision",
        "selected_review_round",
        "preverification_result",
        "case_state",
        "internal_errors",
        "critical_failures"
    }
    
    assert_no_leaks(data, forbidden_internal)

