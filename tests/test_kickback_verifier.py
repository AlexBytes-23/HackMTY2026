"""El verificador de kickback y los dos señuelos que debe resistir.

Los negativos se escribieron ANTES que el positivo, a proposito. Un verificador
de kickback ingenuo -- "el proveedor y el empleado comparten CLABE" -- acusa a un
contratista legitimo, y ese caso existe de verdad en la estate adversarial.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core.estate import EstateRepository, ID_COLUMN, OFFICIAL_COLUMNS
from src.core.models import CaseEvidence, CaseState, EvidenceRef, Hypothesis
from src.investigation.claim_builder import build_claim
from src.verifier.official_verifier import (
    KICKBACK_CHECK_ID,
    OfficialVerifier,
    assess_kickback_link,
)

VENDOR_CLABE = "012" + "1" * 15
EMP_CLABE = "044" + "2" * 15
SHARED_CLABE = "036" + "3" * 15


@pytest.fixture
def estate(tmp_path):
    db = tmp_path / "estate.db"
    conn = sqlite3.connect(db)
    for table, cols in OFFICIAL_COLUMNS.items():
        idc = ID_COLUMN[table]
        rest = ",".join(f'"{c}"' for c in sorted(cols) if c != idc)
        conn.execute(
            f'CREATE TABLE {table} ("{idc}" TEXT PRIMARY KEY'
            + (f",{rest}" if rest else "")
            + ")"
        )

    # --- kickback real: cuentas distintas + el empleado aprueba la PO ---
    conn.execute(
        "INSERT INTO vendors (rfc, legal_name, bank_clabe) "
        f"VALUES ('KIC010101AA1', 'Proveedor Kick', '{VENDOR_CLABE}')"
    )
    conn.execute(
        "INSERT INTO employees (emp_id, name, role, bank_clabe) "
        f"VALUES ('0007', 'Persona Siete', 'Gerente de Compras', '{EMP_CLABE}')"
    )
    conn.execute(
        "INSERT INTO bank_txns (txn_id, date, from_clabe, to_clabe, amount) "
        f"VALUES ('BNK-KICK', '2025-05-02', '{VENDOR_CLABE}', '{EMP_CLABE}', '48000.00')"
    )
    conn.execute(
        "INSERT INTO purchase_orders (po_id, vendor_rfc, date, amount, approver) "
        "VALUES ('PO-KICK', 'KIC010101AA1', '2025-04-20', '400000.00', 'Persona Siete')"
    )

    # --- SEÑUELO D4: el proveedor ES el empleado. Misma cuenta, con contrato. ---
    conn.execute(
        "INSERT INTO vendors (rfc, legal_name, bank_clabe) "
        f"VALUES ('PFA010101BB2', 'Persona Fisica Act Empresarial', '{SHARED_CLABE}')"
    )
    conn.execute(
        "INSERT INTO employees (emp_id, name, role, bank_clabe) "
        f"VALUES ('0031', 'Persona Treinta y Uno', 'Consultor', '{SHARED_CLABE}')"
    )
    conn.execute(
        "INSERT INTO purchase_orders (po_id, vendor_rfc, date, amount, approver) "
        "VALUES ('PO-PFA', 'PFA010101BB2', '2025-03-01', '90000.00', 'Persona Treinta y Uno')"
    )

    # --- SEÑUELO D5: reembolso documentado; el empleado NO aprueba sus POs ---
    conn.execute(
        "INSERT INTO vendors (rfc, legal_name, bank_clabe) "
        f"VALUES ('REE010101CC3', 'Proveedor Reembolso', '{'014' + '4' * 15}')"
    )
    conn.execute(
        "INSERT INTO employees (emp_id, name, role, bank_clabe) "
        f"VALUES ('0022', 'Persona Veintidos', 'Analista', '{'021' + '5' * 15}')"
    )
    conn.execute(
        "INSERT INTO bank_txns (txn_id, date, from_clabe, to_clabe, amount, reference) "
        f"VALUES ('BNK-REE', '2025-06-01', '{'014' + '4' * 15}', '{'021' + '5' * 15}', "
        "'8432.50', 'Reembolso gastos comprobados EXP-2025-0412')"
    )
    conn.execute(
        "INSERT INTO purchase_orders (po_id, vendor_rfc, date, amount, approver) "
        "VALUES ('PO-REE', 'REE010101CC3', '2025-05-15', '50000.00', 'Otra Persona')"
    )

    conn.commit()
    conn.close()

    repo = EstateRepository(db)
    yield repo
    repo.close()


def _refs(*pairs) -> list[EvidenceRef]:
    return [EvidenceRef(source_table=t, record_id=r) for t, r in pairs]


# ==========================================================================
# SEÑUELOS — se escriben primero
# ==========================================================================

def test_a_vendor_who_IS_the_employee_is_not_a_kickback(estate):
    """Persona fisica con actividad empresarial: comparte los 18 digitos.

    Comparten cuenta legitimamente. No hay dos partes, asi que no puede haber
    un flujo entre partes. Un verificador basado en igualdad de CLABE acusaria
    a un contratista legitimo.
    """

    result = assess_kickback_link(
        estate, _refs(("vendors", "PFA010101BB2"), ("employees", "0031"),
                      ("purchase_orders", "PO-PFA"))
    )

    assert result.status == "unresolved"
    assert "same person" in result.calculation
    assert not result.vendor_rfcs


def test_a_documented_reimbursement_is_not_a_kickback(estate):
    """Transferencia real entre cuentas distintas, pero sin conflicto de interes."""

    result = assess_kickback_link(
        estate, _refs(("vendors", "REE010101CC3"), ("employees", "0022"),
                      ("bank_txns", "BNK-REE"), ("purchase_orders", "PO-REE"))
    )

    assert result.status == "unresolved"
    assert "approved by that employee" in result.calculation


def test_a_shared_account_alone_never_verifies(estate):
    """Sin transferencia citada no hay mecanismo, solo un identificador igual."""

    result = assess_kickback_link(
        estate, _refs(("vendors", "PFA010101BB2"), ("employees", "0031"))
    )
    assert result.status == "unresolved"


def test_missing_one_side_is_unresolved_not_failed(estate):
    """Ausencia de datos no es prueba de ausencia: 'unresolved', nunca 'failed'."""

    result = assess_kickback_link(estate, _refs(("vendors", "KIC010101AA1")))
    assert result.status == "unresolved"
    assert "supplied estate" in result.calculation


# ==========================================================================
# POSITIVO
# ==========================================================================

def test_the_real_kickback_verifies(estate):
    result = assess_kickback_link(
        estate,
        _refs(("vendors", "KIC010101AA1"), ("employees", "0007"),
              ("bank_txns", "BNK-KICK"), ("purchase_orders", "PO-KICK")),
    )

    assert result.status == "verified"
    assert result.vendor_rfcs == ["KIC010101AA1"]
    assert result.employee_ids == ["0007"]
    assert "BNK-KICK" in result.calculation
    assert "PO-KICK" in result.calculation


def _case(record_pairs, scheme="kickback") -> CaseState:
    ev = CaseEvidence(
        evidence_id="EV-1",
        statement="registros citados",
        direction="for",
        produced_by="test",
        source_refs=_refs(*record_pairs),
    )
    return CaseState(
        case_id="C", lead_id="L",
        hypotheses=[Hypothesis(hypothesis_id="H-1", statement="kickback",
                               scheme_type=scheme, status="supported",
                               supporting_evidence_ids=["EV-1"])],
        evidence=[ev],
    )


def test_the_verifier_emits_the_kickback_check(estate):
    case = _case([("vendors", "KIC010101AA1"), ("employees", "0007"),
                  ("bank_txns", "BNK-KICK"), ("purchase_orders", "PO-KICK")])

    report = OfficialVerifier().verify(case, "H-1", estate, claimed_amount=48000.0)
    by_id = {c.check_id: c for c in report.checks}

    assert by_id[KICKBACK_CHECK_ID].status == "verified"
    assert by_id[KICKBACK_CHECK_ID].critical is True


# ==========================================================================
# EL MONTO
# ==========================================================================

def test_the_claim_is_the_flow_between_the_parties_not_the_invoices(estate):
    """El perjuicio atribuible al mecanismo es lo que se movio, no lo facturado."""

    claim = build_claim(
        _case([("vendors", "KIC010101AA1"), ("employees", "0007"),
               ("bank_txns", "BNK-KICK"), ("purchase_orders", "PO-KICK")]),
        "H-1", estate,
    )

    assert claim.errors == []
    assert claim.claimed_amount == pytest.approx(48000.0)
    assert claim.source_table == "bank_txns"
    assert claim.contributing_record_ids == ["BNK-KICK"]
    assert set(claim.matched_entities) == {"RFC:KIC010101AA1", "EMP:0007"}
    assert "BNK-KICK=48000.00" in claim.formula


def test_the_claim_refuses_when_no_transfer_between_parties_is_cited(estate):
    claim = build_claim(
        _case([("vendors", "PFA010101BB2"), ("employees", "0031")]), "H-1", estate
    )
    assert claim.claimed_amount is None
    assert any("no kickback amount to claim" in e for e in claim.errors)


def test_the_dispatcher_refuses_a_scheme_with_no_scope_rule(estate):
    claim = build_claim(
        _case([("vendors", "KIC010101AA1")], scheme="round_tripping"), "H-1", estate
    )
    assert claim.claimed_amount is None
    assert any("No scope rule is defined" in e for e in claim.errors)
    assert any("remains a lead" in e for e in claim.errors)


def test_the_dispatcher_still_routes_phantom_vendor(estate):
    claim = build_claim(
        _case([("vendors", "KIC010101AA1")], scheme="phantom_vendor"), "H-1", estate
    )
    # Rechaza por falta de EFOS, no por no saber rutear.
    assert claim.scheme_type == "phantom_vendor"
    assert "efos_list" in claim.scope_rule

# ==========================================================================
# EL CAMPO approver GUARDA UN NOMBRE, NO UN emp_id
# ==========================================================================
# El ejemplo oficial trae approver = "D. Ejemplo". Comparar contra el emp_id no
# coincide nunca sobre datos realistas, y el verificador quedaba muerto: siempre
# `unresolved`. Mis primeros tests pasaban porque el fixture ponia el emp_id en
# approver -- probaban mi suposicion, no el esquema.

def test_the_approver_is_matched_by_name(estate):
    """El fixture ya usa un nombre real; si esto falla el check esta muerto."""

    result = assess_kickback_link(
        estate,
        _refs(("vendors", "KIC010101AA1"), ("employees", "0007"),
              ("bank_txns", "BNK-KICK"), ("purchase_orders", "PO-KICK")),
    )
    assert result.status == "verified"


def test_the_approver_is_also_matched_by_emp_id(tmp_path):
    """Un estate que guarde el emp_id en approver debe seguir funcionando."""

    db = tmp_path / "e.db"
    conn = sqlite3.connect(db)
    for table, cols in OFFICIAL_COLUMNS.items():
        idc = ID_COLUMN[table]
        rest = ",".join(f'"{c}"' for c in sorted(cols) if c != idc)
        conn.execute(
            f'CREATE TABLE {table} ("{idc}" TEXT PRIMARY KEY'
            + (f",{rest}" if rest else "") + ")"
        )
    conn.execute(
        "INSERT INTO vendors (rfc, bank_clabe) VALUES ('V1', '111111111111111111')"
    )
    conn.execute(
        "INSERT INTO employees (emp_id, name, bank_clabe) "
        "VALUES ('0007', 'Alguien', '222222222222222222')"
    )
    conn.execute(
        "INSERT INTO bank_txns (txn_id, from_clabe, to_clabe, amount) "
        "VALUES ('T1', '111111111111111111', '222222222222222222', '100.00')"
    )
    conn.execute(
        "INSERT INTO purchase_orders (po_id, vendor_rfc, approver) "
        "VALUES ('P1', 'V1', '0007')"
    )
    conn.commit(); conn.close()

    with EstateRepository(db) as est:
        result = assess_kickback_link(
            est, _refs(("vendors", "V1"), ("employees", "0007"),
                       ("bank_txns", "T1"), ("purchase_orders", "P1"))
        )
    assert result.status == "verified"


def test_a_name_that_merely_ends_with_another_does_not_match(tmp_path):
    """"Ana Trevino" es sufijo de "Mariana Trevino". Comparacion EXACTA."""

    db = tmp_path / "e.db"
    conn = sqlite3.connect(db)
    for table, cols in OFFICIAL_COLUMNS.items():
        idc = ID_COLUMN[table]
        rest = ",".join(f'"{c}"' for c in sorted(cols) if c != idc)
        conn.execute(
            f'CREATE TABLE {table} ("{idc}" TEXT PRIMARY KEY'
            + (f",{rest}" if rest else "") + ")"
        )
    conn.execute(
        "INSERT INTO vendors (rfc, bank_clabe) VALUES ('V1', '111111111111111111')"
    )
    conn.execute(
        "INSERT INTO employees (emp_id, name, bank_clabe) "
        "VALUES ('0001', 'Ana Trevino', '222222222222222222')"
    )
    conn.execute(
        "INSERT INTO bank_txns (txn_id, from_clabe, to_clabe, amount) "
        "VALUES ('T1', '111111111111111111', '222222222222222222', '100.00')"
    )
    # Otra persona, cuyo nombre TERMINA con el de la empleada citada.
    conn.execute(
        "INSERT INTO purchase_orders (po_id, vendor_rfc, approver) "
        "VALUES ('P1', 'V1', 'Mariana Trevino')"
    )
    conn.commit(); conn.close()

    with EstateRepository(db) as est:
        result = assess_kickback_link(
            est, _refs(("vendors", "V1"), ("employees", "0001"),
                       ("bank_txns", "T1"), ("purchase_orders", "P1"))
        )

    assert result.status == "unresolved"
    assert "approved by that employee" in result.calculation
