import sqlite3

import pytest

from src.core.estate import EstateRepository


OFFICIAL_SCHEMA = """
CREATE TABLE vendors (
    rfc TEXT PRIMARY KEY,
    legal_name TEXT,
    registered_date TEXT,
    address TEXT,
    bank_clabe TEXT,
    category TEXT,
    contact_email TEXT
);

CREATE TABLE invoices (
    uuid TEXT PRIMARY KEY,
    issuer_rfc TEXT,
    receiver_rfc TEXT,
    issue_date TEXT,
    subtotal REAL,
    iva REAL,
    total REAL,
    concepto_text TEXT,
    uso_cfdi TEXT,
    forma_pago TEXT,
    metodo_pago TEXT,
    status TEXT
);

CREATE TABLE ledger (
    entry_id TEXT PRIMARY KEY,
    date TEXT,
    account_code TEXT,
    account_name TEXT,
    debit REAL,
    credit REAL,
    description TEXT,
    invoice_uuid TEXT,
    cost_center TEXT,
    approver TEXT
);

CREATE TABLE bank_txns (
    txn_id TEXT PRIMARY KEY,
    date TEXT,
    from_clabe TEXT,
    to_clabe TEXT,
    amount REAL,
    reference TEXT,
    channel TEXT
);

CREATE TABLE purchase_orders (
    po_id TEXT PRIMARY KEY,
    vendor_rfc TEXT,
    date TEXT,
    amount REAL,
    requester TEXT,
    approver TEXT,
    description TEXT
);

CREATE TABLE contracts (
    contract_id TEXT PRIMARY KEY,
    vendor_rfc TEXT,
    start_date TEXT,
    value REAL,
    scope_text TEXT
);

CREATE TABLE employees (
    emp_id TEXT PRIMARY KEY,
    name TEXT,
    role TEXT,
    bank_clabe TEXT,
    hire_date TEXT
);

CREATE TABLE efos_list (
    rfc TEXT PRIMARY KEY,
    legal_name TEXT,
    status TEXT,
    publication_date TEXT
);
"""


def create_valid_estate(path):
    conn = sqlite3.connect(path)

    conn.executescript(OFFICIAL_SCHEMA)

    conn.execute(
        """
        INSERT INTO vendors
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "VEN010101AAA",
            "Proveedor Uno SA de CV",
            "2025-01-01",
            "Guadalajara, Jalisco",
            "012345678901234567",
            "services",
            "vendor@example.com",
        ),
    )

    conn.execute(
        """
        INSERT INTO invoices
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "UUID-001",
            "VEN010101AAA",
            "AUD010101AAA",
            "2026-01-10",
            1000.0,
            160.0,
            1160.0,
            "Servicio de prueba",
            "G03",
            "03",
            "PUE",
            "Vigente",
        ),
    )

    conn.execute(
        """
        INSERT INTO ledger
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "LED-001",
            "2026-01-10",
            "6000",
            "Servicios",
            1160.0,
            0.0,
            "Registro factura UUID-001",
            "UUID-001",
            "CC-01",
            "EMP001",
        ),
    )

    conn.execute(
        """
        INSERT INTO bank_txns
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "TXN-001",
            "2026-01-12",
            "999999999999999999",
            "012345678901234567",
            1160.0,
            "UUID-001",
            "SPEI",
        ),
    )

    conn.execute(
        """
        INSERT INTO purchase_orders
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "PO-001",
            "VEN010101AAA",
            "2026-01-05",
            1160.0,
            "EMP001",
            "EMP002",
            "Servicio de prueba",
        ),
    )

    conn.execute(
        """
        INSERT INTO contracts
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "CTR-001",
            "VEN010101AAA",
            "2026-01-01",
            15000.0,
            "Servicios recurrentes",
        ),
    )

    conn.execute(
        """
        INSERT INTO employees
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "EMP001",
            "Empleado Uno",
            "Requester",
            "111111111111111111",
            "2024-01-01",
        ),
    )

    conn.execute(
        """
        INSERT INTO efos_list
        VALUES (?, ?, ?, ?)
        """,
        (
            "EFOS010101AAA",
            "Empresa EFOS SA",
            "Presunto",
            "2025-12-01",
        ),
    )

    conn.commit()
    conn.close()


def test_estate_repository_accepts_official_schema(tmp_path):
    db_path = tmp_path / "estate.db"

    create_valid_estate(db_path)

    with EstateRepository(db_path) as estate:
        vendor = estate.get_vendor("VEN010101AAA")

        assert vendor is not None
        assert vendor["rfc"] == "VEN010101AAA"
        assert vendor["bank_clabe"] == "012345678901234567"

        assert estate.record_exists(
            "bank_txns",
            "TXN-001",
        )

        assert estate.record_exists(
            "invoices",
            "UUID-001",
        )


def test_estate_repository_rejects_wrong_schema(tmp_path):
    db_path = tmp_path / "bad_estate.db"

    conn = sqlite3.connect(db_path)

    conn.execute(
        """
        CREATE TABLE vendors (
            vendor_id TEXT PRIMARY KEY,
            name TEXT,
            clabe TEXT
        )
        """
    )

    conn.commit()
    conn.close()

    with pytest.raises(
        ValueError,
        match="El estate no cumple el esquema oficial",
    ):
        EstateRepository(db_path)