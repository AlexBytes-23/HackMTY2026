def normalize_entity(entity_id: str, is_employee: bool = False) -> str:
    """
    Format entity identifier for official submission.
    e.g. 'RFC:AAAA010101AA1' or 'EMP:0001'
    """
    if not entity_id:
        return entity_id
    
    entity_id = entity_id.strip()
    
    if is_employee:
        if entity_id.startswith("EMP:"):
            return entity_id
        return f"EMP:{entity_id}"
    else:
        if entity_id.startswith("RFC:"):
            return entity_id
        return f"RFC:{entity_id}"
