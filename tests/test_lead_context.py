"""A declined lead has to answer a judge's question off the page.

The judge picks an entity from ``leads_not_pursued`` and asks why it was not
flagged.  The reason must name the records that were read, quote the document
that explains the relationship, and quote the deterministic check that did not
clear -- in under ten seconds, with nothing re-run.
"""

from __future__ import annotations

from src.investigation.lead_context import (
    MAX_CALCULATION_CHARS,
    entity_coverage,
    unmet_critical_check_detail,
)
from src.verifier.official_verifier import VerificationCheck, VerificationReport


class FakeEstate:
    """The five reads ``entity_coverage`` performs, and nothing else."""

    def __init__(self, vendors=None, employees=None, contracts=None,
                 purchase_orders=None, invoices=None):
        self._vendors = vendors or {}
        self._employees = employees or {}
        self._contracts = contracts or {}
        self._purchase_orders = purchase_orders or {}
        self._invoices = invoices or {}

    def get_vendor(self, rfc):
        return self._vendors.get(rfc)

    def get_employee(self, emp_id):
        return self._employees.get(emp_id)

    def get_vendor_contracts(self, rfc):
        return self._contracts.get(rfc, [])

    def get_vendor_purchase_orders(self, rfc):
        return self._purchase_orders.get(rfc, [])

    def get_vendor_invoices(self, rfc):
        return self._invoices.get(rfc, [])


RFC = "GAL160126E3F"


def _estate_with_a_contract():
    return FakeEstate(
        vendors={RFC: {"rfc": RFC, "legal_name": "Manufacturas Galzeta, S.A. de C.V."}},
        contracts={
            RFC: [
                {
                    "contract_id": "CTR-2023-0001",
                    "scope_text": (
                        "Manufactura de subensambles metalmecanicos. Contrato por "
                        "proyecto pagado contra entregables."
                    ),
                }
            ]
        },
        purchase_orders={RFC: [{"po_id": "PO-%d" % i} for i in range(5)]},
        invoices={RFC: [{"uuid": "INV-%d" % i} for i in range(5)]},
    )


# ==========================================================================
# COVERAGE
# ==========================================================================

def test_vendor_coverage_names_what_was_read_and_quotes_the_contract():
    sentence, calls = entity_coverage(_estate_with_a_contract(), "RFC:" + RFC)

    assert sentence.startswith(
        "Read 5 invoice(s), 5 purchase order(s), 1 contract(s) for "
        "%s (Manufacturas Galzeta, S.A. de C.V.)." % RFC
    )
    assert "Contract CTR-2023-0001 states: Manufactura de subensambles" in sentence
    assert calls == [
        "get_vendor",
        "get_vendor_contracts",
        "get_vendor_purchase_orders",
        "get_vendor_invoices",
    ]


def test_quoting_one_contract_says_it_is_one_of_several():
    """A check on one record must never be written up as a check on all of them."""

    estate = _estate_with_a_contract()
    estate._contracts[RFC].append(
        {"contract_id": "CTR-2023-0002", "scope_text": "Servicios adicionales."}
    )

    sentence, _ = entity_coverage(estate, "RFC:" + RFC)

    assert "Contract CTR-2023-0001 (1 of 2 read) states:" in sentence
    # It never claims the quoted contract covers the invoices.
    assert "all invoice" not in sentence.lower()


def test_a_missing_contract_is_reported_as_a_missing_record():
    estate = FakeEstate(
        vendors={RFC: {"rfc": RFC, "legal_name": "Sin Contrato SA"}},
        invoices={RFC: [{"uuid": "INV-1"}]},
    )

    sentence, _ = entity_coverage(estate, "RFC:" + RFC)

    assert "No contract row was found in the supplied estate" in sentence
    assert "not evidence that no contract exists" in sentence


def test_an_absent_vendor_is_absent_from_the_estate_not_from_reality():
    sentence, calls = entity_coverage(FakeEstate(), "RFC:" + RFC)

    assert sentence == (
        "No vendor record for %s was found in the supplied estate." % RFC
    )
    assert calls == ["get_vendor"]


def test_employee_coverage_reports_the_recorded_role():
    estate = FakeEstate(employees={"0003": {"emp_id": "0003", "role": "Gerente de Compras"}})

    sentence, calls = entity_coverage(estate, "EMP:0003")

    assert sentence == (
        "Resolved to employee EMP:0003 (Gerente de Compras) in the supplied estate."
    )
    assert calls == ["get_employee"]


def test_employee_coverage_finds_an_id_stored_with_its_prefix():
    estate = FakeEstate(employees={"EMP:0003": {"emp_id": "EMP:0003", "role": "Analista"}})

    sentence, _ = entity_coverage(estate, "EMP:0003")

    assert "(Analista)" in sentence


def test_a_long_scope_clause_is_clipped_rather_than_dumped():
    estate = _estate_with_a_contract()
    estate._contracts[RFC][0]["scope_text"] = "x" * 400

    sentence, _ = entity_coverage(estate, "RFC:" + RFC)

    assert sentence.endswith("...")
    assert len(sentence) < 400


# ==========================================================================
# UNMET CRITICAL CHECKS
# ==========================================================================

def _report(checks):
    return VerificationReport(target_hypothesis_id="H-001", checks=checks)


def test_an_unmet_critical_check_is_named_with_its_calculation():
    report = _report(
        [
            VerificationCheck(
                check_id="PHANTOM-EFOS-INVOICE-LINK",
                statement="The listing reaches the cited invoices.",
                status="unresolved",
                calculation=(
                    "Match found for RFC(s): GAL160126E3F. Status 'definitivo' "
                    "published GAL160126E3F=2024-11-02, but no cited invoice was "
                    "issued on or after that RFC's publication_date."
                ),
                critical=True,
            )
        ]
    )

    detail = unmet_critical_check_detail(report)

    assert detail.startswith(
        "Unmet critical check(s): PHANTOM-EFOS-INVOICE-LINK (unresolved): "
    )
    # The documentary specifics survive verbatim.
    assert "definitivo" in detail
    assert "2024-11-02" in detail


def test_verified_and_non_critical_checks_are_not_reported_as_unmet():
    report = _report(
        [
            VerificationCheck(check_id="EXHIBITS", statement="ok", status="verified",
                              critical=True),
            VerificationCheck(check_id="ADVISORY", statement="soft", status="failed",
                              calculation="not decisive", critical=False),
        ]
    )

    assert unmet_critical_check_detail(report) == ""


def test_every_unmet_critical_check_is_listed_in_report_order():
    report = _report(
        [
            VerificationCheck(check_id="A", statement="a", status="failed",
                              calculation="ca", critical=True),
            VerificationCheck(check_id="B", statement="b", status="unresolved",
                              calculation="cb", critical=True),
        ]
    )

    assert unmet_critical_check_detail(report) == (
        "Unmet critical check(s): A (failed): ca | B (unresolved): cb"
    )


def test_a_very_long_calculation_is_clipped():
    report = _report(
        [
            VerificationCheck(check_id="A", statement="a", status="failed",
                              calculation="y" * (MAX_CALCULATION_CHARS + 200),
                              critical=True)
        ]
    )

    detail = unmet_critical_check_detail(report)

    assert detail.endswith("...")
    assert len(detail) < MAX_CALCULATION_CHARS + 60


def test_no_report_yields_no_detail():
    assert unmet_critical_check_detail(None) == ""
