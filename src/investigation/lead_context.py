"""Documentary context for a lead the audit decided NOT to pursue.

The official brief says a judge will pick an entity out of ``leads_not_pursued``
and ask why it was not flagged.  The answer has to be readable off the page in
under ten seconds, without re-running anything.  "Insufficient evidence" fails
that test; naming the records that were read, quoting the document that explains
the relationship, and quoting the deterministic check that did not clear passes
it.

Two rules this module exists to keep:

* **It states only what was established.**  A check that settles something about
  one record is never written up as settling it for all of them.  The counts are
  counts of what was read; a quoted contract is quoted by its own
  ``contract_id`` and is never presented as covering invoices nobody compared
  against it.
* **Absence in the estate is not absence in reality.**  A missing contract row is
  reported as a missing row, never as "there is no contract".

``entity_coverage`` was lifted from ``ui/audit_bridge._entity_coverage``, which
another session wrote and tested first; ``src`` must not import ``ui``, so the
lookups live here.  The ``CLABE:`` branch of the original is deliberately absent:
callers here resolve a lead to an official ``RFC:``/``EMP:`` entity before asking
for coverage, so an account number never reaches this code.
"""

from __future__ import annotations

from typing import Any

# A scope clause is prose written by the estate's author. Quote enough of it to
# be convincing, not so much that the reason stops being skimmable.
MAX_SCOPE_CHARS = 180

# A verifier's calculation is already terse and every clause in it is load-bearing
# (statuses, publication dates, invoice date ranges), so the cap is generous.
MAX_CALCULATION_CHARS = 400


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def entity_coverage(estate: Any, entity: str) -> tuple[str, list[str]]:
    """Read the documents that would explain an ordinary business relationship.

    Returns ``(sentence, action_bank_calls)``.  ``entity`` must already be an
    official ``RFC:``/``EMP:`` identifier.  The call names are Action Bank names,
    so they can be merged with the calls the Investigator itself made.
    """

    prefix, _, value = entity.partition(":")
    calls: list[str] = []

    if prefix == "EMP":
        calls.append("get_employee")
        # The estate may store emp_id with or without its prefix; try both.
        employee = estate.get_employee(entity) or estate.get_employee(value)
        if employee:
            role = str(employee.get("role") or "").strip() or "role not recorded"
            return (
                "Resolved to employee %s (%s) in the supplied estate." % (entity, role),
                calls,
            )
        return (
            "No employee record for %s was found in the supplied estate." % entity,
            calls,
        )

    calls.append("get_vendor")
    vendor = estate.get_vendor(value)
    if not vendor:
        return (
            "No vendor record for %s was found in the supplied estate." % value,
            calls,
        )

    contracts = estate.get_vendor_contracts(value) or []
    purchase_orders = estate.get_vendor_purchase_orders(value) or []
    invoices = estate.get_vendor_invoices(value) or []
    calls.extend(
        [
            "get_vendor_contracts",
            "get_vendor_purchase_orders",
            "get_vendor_invoices",
        ]
    )

    legal_name = str(vendor.get("legal_name") or "").strip() or "name not recorded"
    sentence = "Read %d invoice(s), %d purchase order(s), %d contract(s) for %s (%s)." % (
        len(invoices),
        len(purchase_orders),
        len(contracts),
        value,
        legal_name,
    )

    if contracts:
        first = contracts[0]
        scope = str(first.get("scope_text") or "").strip()
        if scope:
            # Named by its own contract_id, and labelled when it is one of
            # several: quoting one contract establishes what THAT contract says,
            # never what every contract or every invoice covers.
            label = "Contract %s" % first.get("contract_id")
            if len(contracts) > 1:
                label += " (1 of %d read)" % len(contracts)
            sentence += " %s states: %s" % (label, _clip(scope, MAX_SCOPE_CHARS))
    else:
        # Absence in the estate is not absence in reality.
        sentence += (
            " No contract row was found in the supplied estate; that is an absence "
            "of a record, not evidence that no contract exists."
        )

    return sentence, calls


def unmet_critical_check_detail(verification_report: Any) -> str:
    """Name every critical check that did not clear, and quote its calculation.

    The calculation is where the documentary specifics live -- the EFOS status,
    the publication date, the invoice issue dates it was compared against -- so
    the reason quotes it verbatim instead of paraphrasing it.
    """

    if verification_report is None:
        return ""

    parts: list[str] = []

    for check in verification_report.checks:
        if not check.critical or check.status == "verified":
            continue

        detail = "%s (%s)" % (check.check_id, check.status)
        calculation = str(check.calculation or "").strip()
        if calculation:
            detail += ": " + _clip(calculation, MAX_CALCULATION_CHARS)
        parts.append(detail)

    if not parts:
        return ""

    return "Unmet critical check(s): " + " | ".join(parts)
