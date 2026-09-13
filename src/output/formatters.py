def with_entity_prefix(prefix: str, identifier: str) -> str:
    """Attach ``prefix`` to an estate identifier exactly once.

    MUST STAY IDEMPOTENT -- do not "simplify" this back to an f-string.  The
    official ``employees`` schema documents ``emp_id`` as ``EMP:0001``, prefix
    included, so a judge's estate may hand us the identifier with the prefix
    already attached.  Prefixing again yields ``EMP:EMP:0001``, which matches no
    entity in the answer key: the same correct finding then scores as a missed
    scheme AND as an unattributed false accusation at the same time.
    """

    value = (identifier or "").strip()

    if value.startswith(prefix):
        return value

    return prefix + value


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
        return with_entity_prefix("EMP:", entity_id)
    else:
        if entity_id.startswith("EMP:"):
            raise ValueError(f"Contradictory prefix for vendor: {entity_id}")
        if entity_id == "RFC:":
            raise ValueError("Entity ID has empty payload.")
        return with_entity_prefix("RFC:", entity_id)
