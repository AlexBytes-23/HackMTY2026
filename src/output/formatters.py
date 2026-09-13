def normalize_entity(entity_id: str, is_employee: bool = False) -> str:
    """
    Format entity identifier for official submission.
    e.g. 'RFC:AAAA010101AA1' or 'EMP:0001'
    """
    if not entity_id:
        raise ValueError("Entity ID cannot be empty.")

    entity_id = entity_id.strip()
    if not entity_id:
        raise ValueError("Entity ID cannot be whitespace only.")

    if is_employee:
        if entity_id.startswith("RFC:"):
            raise ValueError(f"Contradictory prefix for employee: {entity_id}")
        if entity_id == "EMP:":
            raise ValueError("Entity ID has empty payload.")
        if entity_id.startswith("EMP:"):
            return entity_id
        return f"EMP:{entity_id}"
    else:
        if entity_id.startswith("EMP:"):
            raise ValueError(f"Contradictory prefix for vendor: {entity_id}")
        if entity_id == "RFC:":
            raise ValueError("Entity ID has empty payload.")
        if entity_id.startswith("RFC:"):
            return entity_id
        return f"RFC:{entity_id}"
