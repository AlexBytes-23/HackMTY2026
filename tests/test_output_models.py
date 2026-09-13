import pytest
from src.output.models import SubmissionFinding, SubmissionExhibit, MoneyTrailStep

def create_valid_finding() -> dict:
    return {
        "scheme_type": "phantom_vendor",
        "entities": ["RFC:1"],
        "narrative": "valid",
        "rule_broken": "SAT 69-B",
        "peso_amount": 10.0,
        "money_trail": [
            {"from": "RFC:1", "to": "EMP:1", "amount": 10.0, "date": "2026", "exhibit_id": "e1"},
            {"from": "EMP:1", "to": "RFC:2", "amount": 10.0, "date": "2026", "exhibit_id": "e2"}
        ],
        "exhibits": [
            {"exhibit_id": "e1", "source_table": "invoices", "record_id": "r1", "note": "n1"},
            {"exhibit_id": "e2", "source_table": "invoices", "record_id": "r2", "note": "n2"},
            {"exhibit_id": "e3", "source_table": "invoices", "record_id": "r3", "note": "n3"}
        ],
        "confidence": "probable"
    }

def test_valid_finding():
    SubmissionFinding(**create_valid_finding())

def test_entities_must_have_prefix():
    data = create_valid_finding()
    data["entities"] = ["NO_PREFIX"]
    with pytest.raises(ValueError, match="must start with RFC: or EMP:"):
        SubmissionFinding(**data)

def test_narrative_length_limit():
    data = create_valid_finding()
    data["narrative"] = "word " * 151
    with pytest.raises(ValueError, match="exceeds 150 words"):
        SubmissionFinding(**data)

def test_exhibit_ids_must_be_unique():
    data = create_valid_finding()
    data["exhibits"][1]["exhibit_id"] = "e1"
    with pytest.raises(ValueError, match="unique"):
        SubmissionFinding(**data)

def test_money_trail_exhibit_must_exist():
    data = create_valid_finding()
    data["money_trail"][0]["exhibit_id"] = "e99"
    with pytest.raises(ValueError, match="must belong to finding exhibits"):
        SubmissionFinding(**data)

def test_money_trail_continuity():
    data = create_valid_finding()
    data["money_trail"][1]["from"] = "SOMETHING_ELSE"
    with pytest.raises(ValueError, match="continuity broken"):
        SubmissionFinding(**data)

def test_whitespace_rejections():
    # Narrative
    data = create_valid_finding()
    data["narrative"] = "   "
    with pytest.raises(ValueError, match="Narrative cannot be empty or whitespace only"):
        SubmissionFinding(**data)

    # Rule broken
    data = create_valid_finding()
    data["rule_broken"] = "   "
    with pytest.raises(ValueError, match="rule_broken cannot be empty or whitespace only"):
        SubmissionFinding(**data)

    # Empty entity payloads
    data = create_valid_finding()
    data["entities"] = ["RFC:"]
    with pytest.raises(ValueError, match="empty payload"):
        SubmissionFinding(**data)

    data = create_valid_finding()
    data["entities"] = ["EMP:"]
    with pytest.raises(ValueError, match="empty payload"):
        SubmissionFinding(**data)

    # Exhibit note
    with pytest.raises(ValueError, match="note cannot be empty or whitespace only"):
        SubmissionExhibit(exhibit_id="e1", source_table="invoices", record_id="r1", note="   ")
