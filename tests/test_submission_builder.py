import json
import math
import os
import pytest
from pathlib import Path
from pydantic import ValidationError

from src.output.models import (
    Submission,
    SubmissionFinding,
    SubmissionExhibit,
    LeadNotPursued,
    RunMetadata,
    serialize_official_submission
)
from src.output.submission_builder import build_submission, write_submission_json

@pytest.fixture
def base_metadata():
    return RunMetadata(
        llm_calls=10,
        mxn_cost=2.5,
        wall_clock_seconds=30.0,
        cost_by_role={"investigator": 1.5},
        deterministic=True
    )

@pytest.fixture
def base_finding():
    return SubmissionFinding(
        scheme_type="phantom_vendor",
        entities=["RFC:123"],
        narrative="Test narrative",
        rule_broken="Art 69-B",
        peso_amount=100.0,
        money_trail=[],
        exhibits=[
            SubmissionExhibit(exhibit_id="EX-001", source_table="invoices", record_id="1", note="n1"),
            SubmissionExhibit(exhibit_id="EX-002", source_table="invoices", record_id="2", note="n2"),
            SubmissionExhibit(exhibit_id="EX-003", source_table="invoices", record_id="3", note="n3")
        ],
        confidence="probable"
    )

@pytest.fixture
def base_lead():
    return LeadNotPursued(
        entity="RFC:ABC",
        signal="anomaly",
        reason="Not enough evidence",
        closed_by="investigator"
    )

def test_submission_with_zero_findings(base_metadata):
    sub = build_submission([], [], base_metadata, 42)
    assert sub.seed == 42
    assert sub.findings == []
    assert sub.leads_not_pursued == []

def test_submission_with_one_finding(base_finding, base_metadata):
    sub = build_submission([base_finding], [], base_metadata, 42)
    assert len(sub.findings) == 1
    assert sub.findings[0].entities == ["RFC:123"]

def test_submission_with_multiple_findings(base_finding, base_metadata):
    f2 = base_finding.model_copy(deep=True)
    f2.entities = ["RFC:456"]
    sub = build_submission([base_finding, f2], [], base_metadata, 42)
    assert len(sub.findings) == 2
    assert sub.findings[0].entities == ["RFC:123"]
    assert sub.findings[1].entities == ["RFC:456"]

def test_findings_preserve_input_order(base_finding, base_metadata):
    f2 = base_finding.model_copy(deep=True)
    f2.entities = ["RFC:456"]
    sub = build_submission([f2, base_finding], [], base_metadata, 42)
    assert sub.findings[0].entities == ["RFC:456"]
    assert sub.findings[1].entities == ["RFC:123"]

def test_leads_not_pursued_preserved(base_lead, base_metadata):
    sub = build_submission([], [base_lead], base_metadata, 42)
    assert len(sub.leads_not_pursued) == 1
    assert sub.leads_not_pursued[0].entity == "RFC:ABC"

def test_leads_preserve_input_order(base_lead, base_metadata):
    l2 = base_lead.model_copy(deep=True)
    l2.entity = "RFC:DEF"
    sub = build_submission([], [l2, base_lead], base_metadata, 42)
    assert sub.leads_not_pursued[0].entity == "RFC:DEF"
    assert sub.leads_not_pursued[1].entity == "RFC:ABC"

def test_run_metadata_preserved_exactly(base_metadata):
    sub = build_submission([], [], base_metadata, 42)
    assert sub.run_metadata.mxn_cost == 2.5
    assert sub.run_metadata.llm_calls == 10

def test_seed_preserved_exactly(base_metadata):
    sub = build_submission([], [], base_metadata, 999)
    assert sub.seed == 999

def test_same_input_equal_model_dump(base_finding, base_lead, base_metadata):
    sub1 = build_submission([base_finding], [base_lead], base_metadata, 42)
    sub2 = build_submission([base_finding], [base_lead], base_metadata, 42)
    assert sub1.model_dump() == sub2.model_dump()

def test_no_unexpected_top_level_fields(base_metadata):
    sub = build_submission([], [], base_metadata, 42)
    d = serialize_official_submission(sub)
    expected_keys = {"seed", "findings", "leads_not_pursued", "run_metadata"}
    assert set(d.keys()) == expected_keys

def test_invalid_official_closed_by_refuses():
    with pytest.raises(ValidationError):
        LeadNotPursued(
            entity="RFC:ABC",
            signal="anomaly",
            reason="Not enough evidence",
            closed_by="invalid_closer"
        )

def test_invalid_metadata_mxn_cost_refuses():
    with pytest.raises(ValidationError):
        RunMetadata(llm_calls=1, mxn_cost="unknown", wall_clock_seconds=1.0)

    with pytest.raises(ValueError, match="finite float"):
        meta = RunMetadata(llm_calls=1, mxn_cost=float("nan"), wall_clock_seconds=1.0)
        build_submission([], [], meta, 42)

    with pytest.raises(ValueError, match="finite float"):
        meta = RunMetadata(llm_calls=1, mxn_cost=2.0, wall_clock_seconds=float("inf"))
        build_submission([], [], meta, 42)

def test_builder_does_not_mutate_inputs(base_finding, base_lead, base_metadata):
    original_f_len = len(base_finding.exhibits)
    sub = build_submission([base_finding], [base_lead], base_metadata, 42)
    assert len(base_finding.exhibits) == original_f_len

    flist = [base_finding]
    sub = build_submission(flist, [], base_metadata, 42)
    sub.findings.clear()
    assert len(flist) == 1

def test_writer_produces_parseable_json_and_matches_serialization(tmp_path, base_finding, base_lead, base_metadata):
    sub = build_submission([base_finding], [base_lead], base_metadata, 42)
    out_path = tmp_path / "submission.json"
    write_submission_json(sub, out_path)

    with open(out_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data == serialize_official_submission(sub)

def test_same_input_byte_identical_json(tmp_path, base_finding, base_lead, base_metadata):
    sub1 = build_submission([base_finding], [base_lead], base_metadata, 42)
    sub2 = build_submission([base_finding], [base_lead], base_metadata, 42)

    out1 = tmp_path / "1.json"
    out2 = tmp_path / "2.json"

    write_submission_json(sub1, out1)
    write_submission_json(sub2, out2)

    with open(out1, "rb") as f1, open(out2, "rb") as f2:
        assert f1.read() == f2.read()

def test_writer_uses_utf8(tmp_path, base_finding, base_metadata):
    base_finding.narrative = "Test narrative with áéíóú ñ"
    sub = build_submission([base_finding], [], base_metadata, 42)
    out_path = tmp_path / "utf8.json"
    write_submission_json(sub, out_path)

    with open(out_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "áéíóú ñ" in content

def test_writer_replaces_existing_valid_target_only_after_successful_serialization(tmp_path, base_metadata):
    out_path = tmp_path / "submission.json"
    with open(out_path, "w") as f:
        f.write("existing content")

    sub = build_submission([], [], base_metadata, 42)
    write_submission_json(sub, out_path)

    with open(out_path, "r") as f:
        assert f.read() != "existing content"

def test_serialization_failure_does_not_leave_partial_target_and_cleans_tmp(tmp_path, base_metadata):
    out_path = tmp_path / "submission.json"
    with open(out_path, "w") as f:
        f.write("existing content")

    sub = build_submission([], [], base_metadata, 42)
    sub.run_metadata.mxn_cost = float('nan')

    with pytest.raises(ValueError, match="JSON serialization failed"):
        write_submission_json(sub, out_path)

    with open(out_path, "r") as f:
        assert f.read() == "existing content"

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name == "submission.json"

def test_serialization_os_replace_failure_cleans_tmp(tmp_path, base_metadata, monkeypatch):
    out_path = tmp_path / "submission.json"
    with open(out_path, "w") as f:
        f.write("existing content")

    sub = build_submission([], [], base_metadata, 42)

    def mock_replace(src, dst):
        raise OSError("Mock disk failure")

    monkeypatch.setattr(os, "replace", mock_replace)

    with pytest.raises(OSError, match="Mock disk failure"):
        write_submission_json(sub, out_path)

    with open(out_path, "r") as f:
        assert f.read() == "existing content"

    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name == "submission.json"

def test_zero_cost_semantics_accepted(base_metadata):
    base_metadata.mxn_cost = 0.0
    sub = build_submission([], [], base_metadata, 42)
    assert sub.run_metadata.mxn_cost == 0.0
