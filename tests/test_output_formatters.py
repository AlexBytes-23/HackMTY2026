import pytest
from src.output.formatters import normalize_entity

def test_normalize_entity():
    assert normalize_entity("123", is_employee=False) == "RFC:123"
    assert normalize_entity("RFC:123", is_employee=False) == "RFC:123"
    assert normalize_entity("0001", is_employee=True) == "EMP:0001"
    assert normalize_entity("EMP:0001", is_employee=True) == "EMP:0001"

def test_normalize_entity_rejections():
    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_entity("")

    with pytest.raises(ValueError, match="cannot be whitespace"):
        normalize_entity("   ")

    with pytest.raises(ValueError, match="Contradictory prefix"):
        normalize_entity("RFC:123", is_employee=True)

    with pytest.raises(ValueError, match="Contradictory prefix"):
        normalize_entity("EMP:0001", is_employee=False)

    with pytest.raises(ValueError, match="empty payload"):
        normalize_entity("RFC:", is_employee=False)

    with pytest.raises(ValueError, match="empty payload"):
        normalize_entity("EMP:", is_employee=True)
