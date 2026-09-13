import pytest
import sqlite3
import tempfile
from pathlib import Path
from src.core.estate import EstateRepository, OFFICIAL_COLUMNS

@pytest.fixture
def temp_estate():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    conn = sqlite3.connect(db_path)

    # Create all required tables
    for table, cols in OFFICIAL_COLUMNS.items():
        if table == "ledger":
            conn.execute(f"CREATE TABLE {table} (entry_id INTEGER PRIMARY KEY, {','.join(c for c in cols if c != 'entry_id')})")
        elif table == "vendors":
            conn.execute(f"CREATE TABLE {table} (rfc TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'rfc')})")
        elif table == "employees":
            conn.execute(f"CREATE TABLE {table} (emp_id TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'emp_id')})")
        elif table == "invoices":
            conn.execute(f"CREATE TABLE {table} (uuid TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'uuid')})")
        elif table == "bank_txns":
            conn.execute(f"CREATE TABLE {table} (txn_id TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'txn_id')})")
        elif table == "purchase_orders":
            conn.execute(f"CREATE TABLE {table} (po_id TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'po_id')})")
        elif table == "contracts":
            conn.execute(f"CREATE TABLE {table} (contract_id TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'contract_id')})")
        elif table == "efos_list":
            conn.execute(f"CREATE TABLE {table} (rfc TEXT PRIMARY KEY, {','.join(c for c in cols if c != 'rfc')})")

    conn.commit()
    conn.close()

    repo = EstateRepository(db_path)
    yield repo
    repo.close()
    Path(db_path).unlink()

def test_clabe_leading_zero_preservation(temp_estate):
    clabe = "000000000000000001"
    temp_estate.conn.execute("INSERT INTO vendors (rfc, bank_clabe) VALUES ('RFC1', ?)", (clabe,))
    temp_estate.conn.commit()

    vendor = temp_estate.get_record("vendors", "RFC1")
    assert vendor["bank_clabe"] == "000000000000000001"

def test_nullable_official_fields(temp_estate):
    # invoice_uuid in ledger is nullable
    temp_estate.conn.execute("INSERT INTO ledger (entry_id, date, invoice_uuid) VALUES (1, '2026', NULL)")
    temp_estate.conn.commit()

    record = temp_estate.get_record("ledger", 1)
    assert record["invoice_uuid"] is None

def test_integer_id_resolves_from_string(temp_estate):
    temp_estate.conn.execute("INSERT INTO ledger (entry_id, date) VALUES (42, '2026')")
    temp_estate.conn.commit()

    # Query with string '42'
    record = temp_estate.get_record("ledger", "42")
    assert record is not None
    assert record["entry_id"] == 42
