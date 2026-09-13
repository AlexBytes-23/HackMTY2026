from __future__ import annotations

import sqlite3

import pytest

from src.core.estate import EstateRepository, ID_COLUMN, OFFICIAL_COLUMNS
from src.core.models import CaseEvidence, CaseState, EvidenceRef, Hypothesis
from src.investigation.claim_builder import (
    PHANTOM_VENDOR_SCOPE_RULE,
    build_phantom_vendor_claim,
)


def _make_estate(tmp_path):
    db_path = tmp_path / "estate.db"
    conn = sqlite3.connect(db_path)
    for table, columns in OFFICIAL_COLUMNS.items():
        id_column = ID_COLUMN[table]
        rest = ",".join(
            f'"{column}"' for column in sorted(columns) if column != id_column
        )
        conn.execute(
            f'CREATE TABLE {table} ("{id_column}" TEXT PRIMARY KEY'
            + (f",{rest}" if rest else "")
            + ")"
        )

    conn.execute(
        "INSERT INTO efos_list (rfc, status, publication_date) "
        "VALUES ('PHA010101AA1', 'Definitivo', '2025-03-01')"
    )
    conn.execute(
        "INSERT INTO vendors (rfc, legal_name) VALUES ('PHA010101AA1', 'Phantom SA')"
    )
    conn.execute(
        "INSERT INTO vendors (rfc, legal_name) VALUES ('REA010101BB2', 'Real SA')"
    )
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, total, issue_date) "
        "VALUES ('INV-001', 'PHA010101AA1', '400.50', '2025-04-01')"
    )
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, total, issue_date) "
        "VALUES ('INV-002', 'PHA010101AA1', '600.25', '2025-04-05')"
    )
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, total, issue_date) "
        "VALUES ('INV-900', 'REA010101BB2', '999.00', '2025-04-07')"
    )
    conn.commit()
    conn.close()

    repository = EstateRepository(db_path)
    yield repository
    repository.close()


@pytest.fixture
def estate(tmp_path):
    yield from _make_estate(tmp_path)


def _case(cited_invoice_ids, *, scheme_type="phantom_vendor", cite_efos=True):
    refs = []
    if cite_efos:
        refs.append(
            EvidenceRef(source_table="efos_list", record_id="PHA010101AA1")
        )
    refs.append(EvidenceRef(source_table="vendors", record_id="PHA010101AA1"))
    for invoice_id in cited_invoice_ids:
        refs.append(EvidenceRef(source_table="invoices", record_id=invoice_id))

    evidence = CaseEvidence(
        evidence_id="EV-1",
        statement="cited records",
        direction="neutral",
        produced_by="test",
        source_refs=refs,
    )
    return CaseState(
        case_id="C-1",
        lead_id="L-1",
        hypotheses=[
            Hypothesis(
                hypothesis_id="H-001",
                statement="phantom vendor",
                scheme_type=scheme_type,
                status="supported",
                supporting_evidence_ids=["EV-1"],
            )
        ],
        evidence=[evidence],
    )


def test_claim_sums_only_invoices_issued_by_cited_efos_rfc(estate):
    claim = build_phantom_vendor_claim(_case(["INV-001", "INV-002"]), "H-001", estate)

    assert claim.errors == []
    assert claim.claimed_amount == pytest.approx(1000.75)
    assert claim.contributing_record_ids == ["INV-001", "INV-002"]
    assert claim.matched_entities == ["RFC:PHA010101AA1"]
    assert claim.source_table == "invoices"
    assert claim.scope_rule == PHANTOM_VENDOR_SCOPE_RULE
    assert "INV-001=400.50" in claim.formula
    assert "1000.75" in claim.formula


def test_claim_refuses_when_exhibits_mix_other_issuers(estate):
    claim = build_phantom_vendor_claim(
        _case(["INV-001", "INV-900"]), "H-001", estate
    )

    assert claim.claimed_amount is None
    assert claim.excluded_record_ids == ["INV-900"]
    assert any("mix invoices from issuers outside" in e for e in claim.errors)


def test_claim_refuses_without_any_in_scope_invoice(estate):
    claim = build_phantom_vendor_claim(_case([]), "H-001", estate)

    assert claim.claimed_amount is None
    assert any("no phantom_vendor amount to claim" in e for e in claim.errors)


def test_claim_refuses_without_cited_efos_record(estate):
    claim = build_phantom_vendor_claim(
        _case(["INV-001"], cite_efos=False), "H-001", estate
    )

    assert claim.claimed_amount is None
    assert any("efos_list record yields a usable RFC" in e for e in claim.errors)


def test_claim_refuses_for_other_schemes(estate):
    claim = build_phantom_vendor_claim(
        _case(["INV-001"], scheme_type="kickback"), "H-001", estate
    )

    assert claim.claimed_amount is None
    assert claim.scheme_type == "kickback"
    assert any("only applies to phantom_vendor" in e for e in claim.errors)


def test_claim_refuses_for_unknown_hypothesis(estate):
    claim = build_phantom_vendor_claim(_case(["INV-001"]), "H-404", estate)

    assert claim.claimed_amount is None
    assert any("does not exist in the CaseState" in e for e in claim.errors)


def test_claim_ignores_evidence_the_hypothesis_does_not_cite(estate):
    case_state = _case(["INV-001"])
    case_state.evidence.append(
        CaseEvidence(
            evidence_id="EV-UNCITED",
            statement="not cited by the hypothesis",
            direction="neutral",
            produced_by="test",
            source_refs=[
                EvidenceRef(source_table="invoices", record_id="INV-002")
            ],
        )
    )

    claim = build_phantom_vendor_claim(case_state, "H-001", estate)

    assert claim.contributing_record_ids == ["INV-001"]
    assert claim.claimed_amount == pytest.approx(400.50)
