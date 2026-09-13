from src.output.formatters import normalize_entity

def test_normalize_entity():
    assert normalize_entity("123", is_employee=False) == "RFC:123"
    assert normalize_entity("RFC:123", is_employee=False) == "RFC:123"
    assert normalize_entity("0001", is_employee=True) == "EMP:0001"
    assert normalize_entity("EMP:0001", is_employee=True) == "EMP:0001"
    assert normalize_entity("") == ""
