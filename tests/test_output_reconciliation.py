import pytest
from src.core.estate import EstateRepository
from src.core.models import EvidenceRef
from src.output.reconciliation import reconcile_pesos
import sqlite3

@pytest.fixture
def memory_estate():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE invoices (uuid TEXT PRIMARY KEY, total REAL)")
    conn.execute("CREATE TABLE bank_txns (txn_id TEXT PRIMARY KEY, amount REAL)")
    conn.execute("CREATE TABLE purchase_orders (po_id TEXT PRIMARY KEY, amount REAL)")
    conn.execute("CREATE TABLE contracts (contract_id TEXT PRIMARY KEY, value REAL)")

    conn.execute("INSERT INTO invoices VALUES ('inv_1', 1000.0)")
    conn.execute("INSERT INTO invoices VALUES ('inv_2', 1500.0)")

    conn.execute("INSERT INTO bank_txns VALUES ('txn_1', 1000.0)")
    conn.execute("INSERT INTO bank_txns VALUES ('txn_2', 1010.0)") # 1% diff
    conn.execute("INSERT INTO bank_txns VALUES ('txn_3', 1050.0)") # 5% diff
    conn.execute("INSERT INTO bank_txns VALUES ('txn_boundary', 1020.0)") # Exactly 2% boundary

    conn.execute("INSERT INTO purchase_orders VALUES ('po_1', 2000.0)")
    conn.execute("INSERT INTO contracts VALUES ('ctr_1', 5000.0)")

    class FakeEstate(EstateRepository):
        def __init__(self):
            self.conn = conn
        def _check_table(self, t): pass

    return FakeEstate()

def test_exact_match(memory_estate):
    exhibits = [EvidenceRef(source_table="invoices", record_id="inv_1")]
    res = reconcile_pesos(memory_estate, 1000.0, exhibits)
    assert res["reconciles"] is True
    assert res["best_matching_table"] == "invoices"

def test_inside_tolerance(memory_estate):
    exhibits = [EvidenceRef(source_table="bank_txns", record_id="txn_2")]
    res = reconcile_pesos(memory_estate, 1000.0, exhibits) # 1000 claimed vs 1010 actual = 10 diff <= 2% of 1010 (20.2)
    assert res["reconciles"] is True

def test_exact_2_percent_boundary(memory_estate):
    exhibits = [EvidenceRef(source_table="bank_txns", record_id="txn_boundary")]
    res = reconcile_pesos(memory_estate, 1000.0, exhibits)
    # Claimed: 1000, Actual: 1020. Diff: 20. Tolerance boundary = 1020 * 0.02 = 20.4
    # Reconciles because 20 <= 20.4. Wait, 1000 is 1.96% off from 1020.
    # If Claimed = 999.6, Diff = 20.4. Let's claim 999.6.
    res2 = reconcile_pesos(memory_estate, 999.6, exhibits)
    assert res2["reconciles"] is True

    # Just outside boundary (claim 999.5, diff = 20.5 > 20.4)
    res3 = reconcile_pesos(memory_estate, 999.5, exhibits)
    assert res3["reconciles"] is False

def test_outside_tolerance(memory_estate):
    exhibits = [EvidenceRef(source_table="bank_txns", record_id="txn_3")]
    res = reconcile_pesos(memory_estate, 1000.0, exhibits) # 1000 claimed vs 1050 actual = 50 diff > 2% of 1050 (21.0)
    assert res["reconciles"] is False
    assert res["error"] is not None

def test_multiple_exhibits_same_table(memory_estate):
    exhibits = [
        EvidenceRef(source_table="invoices", record_id="inv_1"),
        EvidenceRef(source_table="invoices", record_id="inv_2")
    ]
    # Sum is 1000 + 1500 = 2500
    res = reconcile_pesos(memory_estate, 2500.0, exhibits)
    assert res["reconciles"] is True
    assert res["per_table_sums"]["invoices"] == 2500.0

def test_purchase_orders_and_contracts(memory_estate):
    res_po = reconcile_pesos(memory_estate, 2000.0, [EvidenceRef(source_table="purchase_orders", record_id="po_1")])
    assert res_po["reconciles"] is True

    res_ctr = reconcile_pesos(memory_estate, 5000.0, [EvidenceRef(source_table="contracts", record_id="ctr_1")])
    assert res_ctr["reconciles"] is True

def test_invoice_plus_bank_no_double_count(memory_estate):
    exhibits = [
        EvidenceRef(source_table="invoices", record_id="inv_1"),
        EvidenceRef(source_table="bank_txns", record_id="txn_1")
    ]
    # Total invoices = 1000, Total bank = 1000. We claim 1000.
    res = reconcile_pesos(memory_estate, 1000.0, exhibits)
    assert res["reconciles"] is True
    # If they were double counted, best table would be 2000, but per_table isolates them!
    assert res["per_table_sums"]["invoices"] == 1000.0
    assert res["per_table_sums"]["bank_txns"] == 1000.0

def test_string_int_type_coercion_if_official(memory_estate):
    memory_estate.conn.execute("CREATE TABLE ledger (entry_id INTEGER PRIMARY KEY, amount REAL)")
    memory_estate.conn.execute("INSERT INTO ledger VALUES (1, 100.0)")

    # Notice we pass "1" as string
    val = memory_estate.get_record("ledger", "1")
    assert val is not None
    assert val["entry_id"] == 1
