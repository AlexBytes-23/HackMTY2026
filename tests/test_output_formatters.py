import pytest
from src.output.formatters import normalize_entity, with_entity_prefix

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


# ==========================================================================
# IDEMPOTENT PREFIXING
# ==========================================================================

def test_with_entity_prefix_adds_the_prefix_once():
    assert with_entity_prefix("EMP:", "0001") == "EMP:0001"
    assert with_entity_prefix("RFC:", "AAA010101AA1") == "RFC:AAA010101AA1"


def test_with_entity_prefix_is_idempotent():
    """The official employees schema stores emp_id as 'EMP:0001', prefix included.

    Doubling it produces an entity that matches nothing in the answer key.
    """

    assert with_entity_prefix("EMP:", "EMP:0001") == "EMP:0001"
    assert with_entity_prefix("RFC:", "RFC:AAA010101AA1") == "RFC:AAA010101AA1"

    # Applying it repeatedly must never grow the identifier.
    once = with_entity_prefix("EMP:", "0001")
    assert with_entity_prefix("EMP:", once) == once


def test_with_entity_prefix_trims_surrounding_whitespace():
    assert with_entity_prefix("EMP:", "  EMP:0001  ") == "EMP:0001"
    assert with_entity_prefix("EMP:", "  0001  ") == "EMP:0001"
