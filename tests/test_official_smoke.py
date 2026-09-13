import json
import pytest
from pathlib import Path
from src.output.models import Submission, SubmissionFinding, SubmissionExhibit, RunMetadata

import sys
sys.path.insert(0, str(Path("official_materials/student-materials/forensic-auditor")))
import validate_format

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
                    SubmissionExhibit(exhibit_id="e3", source_table="vendors", record_id="RFC:1", note="vendor")
                ],
                confidence="probable",
                money_trail=[
                    {"from": "RFC:1", "to": "EMP:1", "amount": 1000.0, "date": "2026-01-01", "exhibit_id": "e2"}
                ]
            )
        ],
        leads_not_pursued=[],
        run_metadata=RunMetadata(llm_calls=1, mxn_cost=0.5, wall_clock_seconds=1.0)
    )
    
    sub_dict = sub.model_dump(by_alias=True)
    
    errs = validate_format.validate_structure(sub_dict)
    assert not errs, f"Validation failed: {errs}"

def test_negative_official_validator_smoke():
    sub_dict = {
        "seed": "not-an-int",
        "findings": [
            {
                "scheme_type": "fake_scheme", # invalid enum
                "entities": [], # empty entities
                "narrative": "too short",
                "peso_amount": -100 # negative
            }
        ]
    }
    
    errs = validate_format.validate_structure(sub_dict)
    assert len(errs) > 0
    assert any("findings[0].scheme_type must be one of" in e for e in errs)

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

