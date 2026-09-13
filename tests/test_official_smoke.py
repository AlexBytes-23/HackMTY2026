import json
import pytest
import sqlite3
import tempfile
from pathlib import Path
from src.output.models import Submission, SubmissionFinding, SubmissionExhibit, RunMetadata, MoneyTrailStep, serialize_official_submission

OFFICIAL_DIR = Path("official_materials/student-materials/forensic-auditor")
HAS_OFFICIAL = OFFICIAL_DIR.exists()

if HAS_OFFICIAL:
    import sys
    sys.path.insert(0, str(OFFICIAL_DIR))
    import validate_format

@pytest.mark.skipif(not HAS_OFFICIAL, reason="Official materials not present")
def test_positive_official_validator_smoke():
    sub = Submission(
        seed=42,
        findings=[
            SubmissionFinding(
                scheme_type="phantom_vendor",
                entities=["RFC:AAAA010101AA1"],
                narrative="Valid finding narrative.",
                rule_broken="SAT 69-B",
                peso_amount=1000.0,
                exhibits=[
                    SubmissionExhibit(exhibit_id="e1", source_table="invoices", record_id="inv_1", note="invoice"),
                    SubmissionExhibit(exhibit_id="e2", source_table="bank_txns", record_id="txn_1", note="payment"),
                    SubmissionExhibit(exhibit_id="e3", source_table="vendors", record_id="AAAA010101AA1", note="vendor")
                ],
                money_trail=[
                    MoneyTrailStep(**{"from": "RFC:A", "to": "RFC:B", "amount": 1000.0, "date": "2026", "exhibit_id": "e2"})
                ],
                confidence="probable"
            )
        ],
        leads_not_pursued=[],
        run_metadata=RunMetadata(llm_calls=1, mxn_cost=0.5, wall_clock_seconds=1.0)
    )

    sub_dict = serialize_official_submission(sub)
    errs = validate_format.validate_structure(sub_dict)
    assert not errs, f"Validation failed: {errs}"

@pytest.mark.skipif(not HAS_OFFICIAL, reason="Official materials not present")
def test_negative_official_validator_smoke():
    sub_dict = {
        "seed": "not-an-int",
        "findings": [
            {
                "scheme_type": "fake_scheme",
                "entities": [],
                "narrative": "too short",
                "peso_amount": -100
            }
        ]
    }
    errs = validate_format.validate_structure(sub_dict)
    assert len(errs) > 0
    assert any("findings[0].scheme_type must be one of" in e for e in errs)

@pytest.mark.skipif(not HAS_OFFICIAL, reason="Official materials not present")
def test_official_validator_with_real_estate():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE invoices (uuid TEXT PRIMARY KEY, total REAL)")
        conn.execute("CREATE TABLE bank_txns (txn_id TEXT PRIMARY KEY, amount REAL)")
        conn.execute("CREATE TABLE vendors (rfc TEXT PRIMARY KEY)")

        conn.execute("INSERT INTO invoices VALUES ('inv_1', 1000.0)")
        conn.execute("INSERT INTO bank_txns VALUES ('txn_1', 1000.0)")
        conn.execute("INSERT INTO vendors VALUES ('AAAA010101AA1')")
        conn.commit()
        conn.close()

        sub = Submission(
            seed=42,
            findings=[
                SubmissionFinding(
                    scheme_type="phantom_vendor",
                    entities=["RFC:AAAA010101AA1"],
                    narrative="Valid finding narrative.",
                    rule_broken="SAT 69-B",
                    peso_amount=1000.0,
                    exhibits=[
                        SubmissionExhibit(exhibit_id="e1", source_table="invoices", record_id="inv_1", note="invoice"),
                        SubmissionExhibit(exhibit_id="e2", source_table="bank_txns", record_id="txn_1", note="payment"),
                        SubmissionExhibit(exhibit_id="e3", source_table="vendors", record_id="AAAA010101AA1", note="vendor")
                    ],
                    money_trail=[
                        MoneyTrailStep(**{"from": "RFC:A", "to": "RFC:B", "amount": 1000.0, "date": "2026", "exhibit_id": "e2"})
                    ],
                    confidence="probable"
                )
            ],
            leads_not_pursued=[],
            run_metadata=RunMetadata(llm_calls=1, mxn_cost=0.5, wall_clock_seconds=1.0)
        )
        sub_dict = serialize_official_submission(sub)
        errs = validate_format.validate_against_estate(sub_dict, db_path)
        assert not errs, f"Estate validation failed: {errs}"
    finally:
        Path(db_path).unlink()

def test_ground_truth_leakage_scan():
    import os
    src_dir = Path("src")
    forbidden = ["ground_truth", "answer_key", "dev_answer_key"]
    for root, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith(".py"):
                content = (Path(root) / f).read_text(encoding="utf-8").lower()
                for fb in forbidden:
                    assert fb not in content, f"Ground truth leakage found in {f}"
