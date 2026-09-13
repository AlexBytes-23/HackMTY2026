"""Contract tests for the robust-realism evaluation estate.

These assert properties an adversarial evaluation estate MUST have:

* it conforms to the official eight-table schema and carries no answer key;
* the answer key conforms to the official ``ground_truth_schema.json`` shape;
* it is internally consistent (VAT arithmetic, double entry, referential
  integrity, valid CLABE check digits, dates inside the evaluation period);
* the same seed produces byte-identical output;
* the traps are actually present — the EFOS name twin really does have a
  different RFC, the pre-publication decoy's invoices really do all predate its
  publication date, the benign cycle really does have the same topology as the
  round trip;
* and nothing in ``src/`` can reach the answer key.

The generator lives in ``dev_eval/`` and is imported by path, never by ``src``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEV_EVAL = REPO_ROOT / "dev_eval"
GENERATOR = DEV_EVAL / "generate_robust_realism_estate.py"
ESTATE_DB = DEV_EVAL / "robust_realism_estate.db"
ANSWERS_DB = DEV_EVAL / "robust_realism_estate_answers.db"
ESTATE_ZIP = DEV_EVAL / "robust_realism_estate_csv.zip"
GROUND_TRUTH = DEV_EVAL / "robust_realism_ground_truth.json"
MANIFEST = DEV_EVAL / "robust_realism_manifest.md"
DEP_MAP = DEV_EVAL / "evidence_dependency_map.json"

OFFICIAL_TABLES = {
    "vendors", "invoices", "ledger", "bank_txns",
    "purchase_orders", "contracts", "employees", "efos_list",
}
SCHEME_TYPES = {"phantom_vendor", "kickback", "round_tripping",
                "threshold_splitting", "revenue_inflation"}

pytestmark = pytest.mark.skipif(
    not ESTATE_DB.exists(),
    reason="estate not generated; run python dev_eval/generate_robust_realism_estate.py",
)


def _load_generator():
    spec = importlib.util.spec_from_file_location("_rr_generator", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_rr_generator"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load_generator()


@pytest.fixture(scope="module")
def db():
    conn = sqlite3.connect(ESTATE_DB)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def gt():
    return json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- schema ---

def test_estate_has_exactly_the_official_tables(db):
    names = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == OFFICIAL_TABLES


def test_estate_columns_match_the_official_schema(db):
    expected = {
        "vendors": ["rfc", "legal_name", "registered_date", "address",
                    "bank_clabe", "category", "contact_email"],
        "invoices": ["uuid", "issuer_rfc", "receiver_rfc", "issue_date",
                     "subtotal", "iva", "total", "concepto_text", "uso_cfdi",
                     "forma_pago", "metodo_pago", "status"],
        "ledger": ["entry_id", "date", "account_code", "account_name", "debit",
                   "credit", "description", "invoice_uuid", "cost_center",
                   "approver"],
        "bank_txns": ["txn_id", "date", "from_clabe", "to_clabe", "amount",
                      "reference", "channel"],
        "purchase_orders": ["po_id", "vendor_rfc", "date", "amount",
                            "requester", "approver", "description"],
        "contracts": ["contract_id", "vendor_rfc", "start_date", "value",
                      "scope_text"],
        "employees": ["emp_id", "name", "role", "bank_clabe", "hire_date"],
        "efos_list": ["rfc", "legal_name", "status", "publication_date"],
    }
    for table, cols in expected.items():
        actual = [r[1] for r in db.execute("PRAGMA table_info(%s)" % table)]
        assert actual == cols, table


def test_clean_estate_carries_no_answer_key(db):
    """No is_fraud / scheme_type / label column anywhere. Invariant 11."""
    for table in OFFICIAL_TABLES:
        for row in db.execute("PRAGMA table_info(%s)" % table):
            name = row[1].lower()
            assert not any(tok in name for tok in
                           ("fraud", "scheme", "ground", "answer", "label",
                            "decoy", "planted")), (table, name)


def test_clean_estate_has_no_answer_key_tables(db):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
    assert not any(n.startswith("answer_key") for n in names)


def test_answers_db_carries_the_estate_and_the_answer_key():
    conn = sqlite3.connect(ANSWERS_DB)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert OFFICIAL_TABLES <= names
        assert {"answer_key_meta", "answer_key_schemes",
                "answer_key_scheme_records", "answer_key_decoys",
                "answer_key_decoy_records"} <= names
        n = conn.execute("SELECT COUNT(*) FROM answer_key_schemes").fetchone()[0]
        assert n == 5
    finally:
        conn.close()


def test_answers_db_estate_tables_match_the_clean_estate(db):
    """The two files must describe the same company, or the answer key lies."""
    conn = sqlite3.connect(ANSWERS_DB)
    try:
        for table in sorted(OFFICIAL_TABLES):
            a = db.execute("SELECT * FROM %s" % table).fetchall()
            b = conn.execute("SELECT * FROM %s" % table).fetchall()
            assert [tuple(r) for r in a] == [tuple(r) for r in b], table
    finally:
        conn.close()


# ----------------------------------------------------------- ground truth ---

def test_ground_truth_matches_the_official_schema_shape(gt):
    assert set(gt) == {"seed", "company_rfc", "schemes", "decoys"}
    assert isinstance(gt["seed"], int)
    assert isinstance(gt["company_rfc"], str) and gt["company_rfc"]

    for s in gt["schemes"]:
        assert {"scheme_id", "type", "entities", "peso_amount",
                "difficulty"} <= set(s)
        assert s["type"] in SCHEME_TYPES
        assert s["difficulty"] in {"easy", "medium", "hard"}
        assert isinstance(s["peso_amount"], (int, float)) and s["peso_amount"] > 0
        assert s["entities"], s["scheme_id"]
        for e in s["entities"]:
            assert e.startswith(("RFC:", "EMP:")), e

    for d in gt["decoys"]:
        assert {"entity", "signal", "why_innocent"} <= set(d)
        assert d["entity"].startswith(("RFC:", "EMP:")), d["entity"]
        assert len(d["why_innocent"].split()) >= 8, d["entity"]


def test_all_five_official_schemes_are_planted(gt):
    assert {s["type"] for s in gt["schemes"]} == SCHEME_TYPES


def test_schemes_are_entangled(gt):
    """At least two schemes must share an entity - judges plant entangled ones."""
    seen: dict[str, list[str]] = {}
    for s in gt["schemes"]:
        for e in s["entities"]:
            if e == "RFC:" + gt["company_rfc"]:
                continue
            seen.setdefault(e, []).append(s["scheme_id"])
    shared = {e: ids for e, ids in seen.items() if len(ids) > 1}
    assert shared, "no entity is shared between two schemes"


def test_ground_truth_record_ids_all_exist_in_the_estate(db, gt):
    invoices = {r[0] for r in db.execute("SELECT uuid FROM invoices")}
    txns = {r[0] for r in db.execute("SELECT txn_id FROM bank_txns")}
    vendors = {r[0] for r in db.execute("SELECT rfc FROM vendors")}
    employees = {r[0] for r in db.execute("SELECT emp_id FROM employees")}

    for s in gt["schemes"]:
        for u in s.get("supporting_invoices", []):
            assert u in invoices, (s["scheme_id"], u)
        for t in s.get("supporting_txns", []):
            assert t in txns, (s["scheme_id"], t)
        for e in s["entities"]:
            prefix, _, value = e.partition(":")
            if prefix == "RFC":
                assert value in vendors or value == gt["company_rfc"], e
            else:
                assert e in employees, e

    for d in gt["decoys"]:
        for u in d.get("invoices", []):
            assert u in invoices, (d["entity"], u)


def test_scheme_peso_amounts_reconcile_to_their_own_records(db, gt):
    """Whatever the answer key claims must add up in the estate itself."""
    for s in gt["schemes"]:
        totals = {}
        if s.get("supporting_invoices"):
            marks = ",".join("?" * len(s["supporting_invoices"]))
            totals["invoices"] = db.execute(
                "SELECT COALESCE(SUM(total),0) FROM invoices WHERE uuid IN (%s)"
                % marks, s["supporting_invoices"]).fetchone()[0]
        if s.get("supporting_txns"):
            marks = ",".join("?" * len(s["supporting_txns"]))
            totals["bank_txns"] = db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM bank_txns WHERE txn_id IN (%s)"
                % marks, s["supporting_txns"]).fetchone()[0]
        assert totals, s["scheme_id"]
        best = min(totals.values(), key=lambda v: abs(s["peso_amount"] - v))
        assert abs(s["peso_amount"] - best) <= 0.02 * max(best, 1), (
            s["scheme_id"], s["peso_amount"], totals)


def test_no_scheme_entity_is_also_listed_as_a_decoy(gt):
    scheme_entities = {e for s in gt["schemes"] for e in s["entities"]
                       if e != "RFC:" + gt["company_rfc"]}
    decoy_entities = {d["entity"] for d in gt["decoys"]}
    assert not (scheme_entities & decoy_entities)


# ------------------------------------------------------------ consistency ---

def test_invoice_vat_arithmetic_holds(db):
    n = db.execute(
        "SELECT COUNT(*) FROM invoices WHERE ABS(total-(subtotal+iva))>0.011"
    ).fetchone()[0]
    assert n == 0


def test_three_vat_rates_coexist(db):
    """total == subtotal*1.16 must NOT be a safe universal check."""
    rates = {round(r[0], 2) for r in db.execute(
        "SELECT ROUND(iva/subtotal,2) FROM invoices WHERE subtotal>0")}
    assert {0.0, 0.08, 0.16} <= rates


def test_ledger_is_balanced_double_entry(db):
    diff = db.execute("SELECT SUM(debit)-SUM(credit) FROM ledger").fetchone()[0]
    assert abs(diff) < 0.01


def test_referential_integrity(db):
    assert db.execute(
        "SELECT COUNT(*) FROM ledger WHERE invoice_uuid IS NOT NULL "
        "AND invoice_uuid NOT IN (SELECT uuid FROM invoices)").fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM purchase_orders WHERE vendor_rfc NOT IN "
        "(SELECT rfc FROM vendors)").fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM contracts WHERE vendor_rfc NOT IN "
        "(SELECT rfc FROM vendors)").fetchone()[0] == 0


def test_every_clabe_has_a_valid_check_digit(db, gen):
    for rfc, c in db.execute("SELECT rfc, bank_clabe FROM vendors"):
        assert gen.clabe_is_valid(c), (rfc, c)
    for emp, c in db.execute("SELECT emp_id, bank_clabe FROM employees"):
        assert gen.clabe_is_valid(c), (emp, c)
    for txn, a, b in db.execute(
            "SELECT txn_id, from_clabe, to_clabe FROM bank_txns"):
        assert gen.clabe_is_valid(a), (txn, a)
        assert gen.clabe_is_valid(b), (txn, b)


def test_all_money_movements_fall_inside_the_evaluation_period(db, gen):
    lo, hi = gen.iso(gen.PERIOD_START), gen.iso(gen.PERIOD_END)
    for table, col in (("invoices", "issue_date"), ("ledger", "date"),
                       ("bank_txns", "date")):
        mn, mx = db.execute(
            "SELECT MIN(%s), MAX(%s) FROM %s" % (col, col, table)).fetchone()
        assert lo <= mn and mx <= hi, (table, mn, mx)


def test_ppd_invoices_use_forma_pago_99(db):
    """The Anexo 20 filling rule. Also the D14 decoy's whole point."""
    assert db.execute(
        "SELECT COUNT(*) FROM invoices WHERE metodo_pago='PPD' "
        "AND forma_pago<>'99'").fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM invoices WHERE metodo_pago='PPD'").fetchone()[0] > 50


def test_employee_ids_use_the_official_prefixed_form(db):
    for (emp_id,) in db.execute("SELECT emp_id FROM employees"):
        assert emp_id.startswith("EMP:"), emp_id


def test_honest_traffic_dominates(db, gt):
    """A showcase estate would be mostly fraud. This one must not be."""
    total = db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    planted = {u for s in gt["schemes"] for u in s.get("supporting_invoices", [])}
    assert len(planted) / total < 0.05, (len(planted), total)
    assert total > 1000


def test_ids_do_not_leak_the_answer_key(db, gt):
    """Planted rows are generated last; if ids were assigned in generation
    order every scheme transaction would sit at the end of the sequence and a
    detector could cheat on ordinality alone."""
    all_txns = [r[0] for r in db.execute(
        "SELECT txn_id FROM bank_txns ORDER BY txn_id")]
    planted = {t for s in gt["schemes"] for t in s.get("supporting_txns", [])}
    positions = [i for i, t in enumerate(all_txns) if t in planted]
    assert positions
    # The planted rows must not be concentrated in the last decile.
    assert min(positions) < 0.5 * len(all_txns)
    assert max(positions) - min(positions) > 0.3 * len(all_txns)


# ------------------------------------------------------------- the traps ---

def test_efos_name_twin_has_a_different_rfc(db, gt):
    """D2: near-identical legal name, different RFC, absent from efos_list."""
    listed = {r[0] for r in db.execute("SELECT rfc FROM efos_list")}
    phantom = [s for s in gt["schemes"]
               if s["type"] == "phantom_vendor"][0]["entities"][0][4:]
    phantom_name = db.execute(
        "SELECT legal_name FROM vendors WHERE rfc=?", (phantom,)).fetchone()[0]
    stem = phantom_name.split()[1][:4].lower()
    twins = [r for r in db.execute("SELECT rfc, legal_name FROM vendors")
             if stem in r[1].lower() and r[0] != phantom]
    assert twins, "the name-similarity trap is missing"
    for rfc, _name in twins:
        assert rfc not in listed, rfc


def test_prepublication_efos_decoy_predates_its_listing(db):
    """D16: on the list as 'presunto', every invoice issued long before."""
    rows = db.execute(
        "SELECT e.rfc, e.publication_date, MAX(i.issue_date) "
        "FROM efos_list e JOIN invoices i ON i.issuer_rfc = e.rfc "
        "WHERE e.status='presunto' GROUP BY e.rfc, e.publication_date").fetchall()
    assert rows, "the publication-timing trap is missing"
    for rfc, pub, latest in rows:
        assert latest < pub, (rfc, latest, pub)


def test_the_real_phantom_vendor_invoiced_after_its_definitive_listing(db):
    rows = db.execute(
        "SELECT e.rfc, e.publication_date, MIN(i.issue_date) "
        "FROM efos_list e JOIN invoices i ON i.issuer_rfc = e.rfc "
        "WHERE e.status='definitivo' GROUP BY e.rfc, e.publication_date").fetchall()
    assert rows
    for rfc, pub, earliest in rows:
        assert earliest > pub, (rfc, earliest, pub)


def test_a_benign_cycle_shares_the_topology_of_the_round_trip(db, gt):
    """Topology alone must not separate S3 from D6."""
    edges = {(r[0], r[1]) for r in db.execute(
        "SELECT from_clabe, to_clabe FROM bank_txns")}
    adjacency: dict[str, set[str]] = {}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
    triangles = set()
    for a in adjacency:
        for b in adjacency.get(a, ()):
            for c in adjacency.get(b, ()):
                if a in adjacency.get(c, ()):
                    triangles.add(tuple(sorted((a, b, c))))
    assert len(triangles) >= 2, "need at least one honest cycle and one scheme cycle"


def test_a_vendor_and_an_employee_legitimately_share_a_clabe(db):
    shared = db.execute(
        "SELECT v.rfc, e.emp_id FROM vendors v JOIN employees e "
        "ON v.bank_clabe = e.bank_clabe").fetchall()
    assert shared, "the shared-CLABE decoy is missing"


def test_two_vendors_share_one_clabe(db):
    rows = db.execute(
        "SELECT bank_clabe, COUNT(*) c FROM vendors GROUP BY bank_clabe "
        "HAVING c > 1").fetchall()
    assert rows, "the shared-vendor-CLABE decoy is missing"


def test_the_approval_ladder_is_inferable_but_leaky(db):
    """It must be discoverable, and it must NOT be perfectly crisp - an auditor
    that infers a hard threshold from this estate has over-fitted."""
    below = db.execute(
        "SELECT approver, COUNT(*) FROM purchase_orders WHERE amount < 100000 "
        "GROUP BY approver").fetchall()
    above = db.execute(
        "SELECT approver, COUNT(*) FROM purchase_orders WHERE amount >= 500000 "
        "GROUP BY approver").fetchall()
    low_signers = {r[0] for r in below}
    high_signers = {r[0] for r in above}
    assert high_signers, "no large purchase orders"
    assert not high_signers <= low_signers or len(low_signers) > len(high_signers)
    leak = db.execute(
        "SELECT COUNT(*) FROM purchase_orders p JOIN employees e "
        "ON p.approver = e.name WHERE p.amount < 100000 "
        "AND e.role LIKE 'Gerente%'").fetchone()[0]
    assert leak > 0, "the ladder is perfectly crisp; that is unrealistic"


def test_cancelled_invoices_exist_and_are_never_settled(db):
    cancelled = db.execute(
        "SELECT uuid FROM invoices WHERE status='cancelado'").fetchall()
    assert len(cancelled) >= 10
    for (uuid_value,) in cancelled:
        settled = db.execute(
            "SELECT COUNT(*) FROM bank_txns WHERE reference = ?",
            ("Pago CFDI " + uuid_value[:8],)).fetchone()[0]
        assert settled == 0, uuid_value


def test_the_sat_generic_rfc_is_present_as_a_placeholder(db):
    row = db.execute(
        "SELECT legal_name FROM vendors WHERE rfc='XAXX010101000'").fetchone()
    assert row is not None


# ----------------------------------------------------------- csv / zip ---

def test_csv_zip_carries_the_same_data_as_the_sqlite_estate(db):
    with zipfile.ZipFile(ESTATE_ZIP) as zf:
        names = set(zf.namelist())
        assert names == {t + ".csv" for t in OFFICIAL_TABLES}
        for table in sorted(OFFICIAL_TABLES):
            text = zf.read(table + ".csv").decode("utf-8")
            lines = text.strip().split("\n")
            n = db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            assert len(lines) - 1 == n, table


# ----------------------------------------------------------- isolation ---

def test_src_cannot_reach_the_answer_key():
    """The judges run exactly this grep. Invariant 1."""
    result = subprocess.run(
        ["git", "grep", "-nIE", "ground_truth|answer_key|robust_realism", "--",
         "src/"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.stdout.strip() == "", result.stdout


def test_generator_does_not_import_production_code():
    """dev_eval may describe src/ in prose; it may never depend on it."""
    import ast
    tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("src"), alias.name
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("src"), node.module


# -------------------------------------------------------- determinism ---

def test_same_seed_produces_byte_identical_output(tmp_path, gen):
    a, b = tmp_path / "a", tmp_path / "b"
    gen.generate(a, gen.SEED)
    gen.generate(b, gen.SEED)
    produced = sorted(p.name for p in a.iterdir())
    assert len(produced) == 6
    for name in produced:
        ha = hashlib.sha256((a / name).read_bytes()).hexdigest()
        hb = hashlib.sha256((b / name).read_bytes()).hexdigest()
        assert ha == hb, name


def test_a_different_seed_produces_a_different_estate(tmp_path, gen):
    a, b = tmp_path / "s1", tmp_path / "s2"
    gen.generate(a, gen.SEED)
    gen.generate(b, gen.SEED + 1)
    ha = hashlib.sha256((a / "robust_realism_estate.db").read_bytes()).hexdigest()
    hb = hashlib.sha256((b / "robust_realism_estate.db").read_bytes()).hexdigest()
    assert ha != hb


# --------------------------------------------------------- companions ---

def test_manifest_names_every_scheme_and_decoy(gt):
    text = MANIFEST.read_text(encoding="utf-8")
    for s in gt["schemes"]:
        assert s["scheme_id"] in text
    for d in gt["decoys"]:
        assert d["entity"] in text


def test_dependency_map_flags_shared_evidence(gt):
    payload = json.loads(DEP_MAP.read_text(encoding="utf-8"))
    assert payload["same_money_groups"], "no same-money group recorded"
    for group in payload["same_money_groups"]:
        assert group["ledger_entry_ids"] or group["bank_txn_ids"]
    multi = payload["records_feeding_three_or_more_analytics"]
    assert multi, "no record is recorded as feeding three analytics"
