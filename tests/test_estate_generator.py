"""Tests for the development-only synthetic estate generator.

``dev/`` is not ``src/``, so importing it here is allowed and expected: the
answer key must never be reachable from the agent, only from tests and the
evaluation harness.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dev import generate_estate as generate_estate_cli
from dev.estate_generator import (
    COMPANY_CLABE,
    COMPANY_RFC,
    INTERNAL_APPROVAL_THRESHOLD_MXN,
    SCHEME_TYPES,
    EstateSpec,
    dump_answer_key,
    generate_estate,
)
from src.core.estate import ID_COLUMN, OFFICIAL_COLUMNS, EstateRepository


SEED = 101
OTHER_SEED = 202

REPO_ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH_SCHEMA = (
    REPO_ROOT
    / "official_materials"
    / "student-materials"
    / "forensic-auditor"
    / "ground_truth_schema.json"
)


# ==========================================================================
# FIXTURES
# ==========================================================================

@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> tuple[Path, dict]:
    db_path = tmp_path_factory.mktemp("estate") / "estate.db"
    answer_key = generate_estate(EstateSpec(seed=SEED), db_path)
    return db_path, answer_key


@pytest.fixture(scope="module")
def repository(generated) -> EstateRepository:
    db_path, _ = generated
    repo = EstateRepository(db_path)
    yield repo
    repo.close()


def _dump_all(db_path: Path) -> dict[str, list[tuple]]:
    """Sorted row dump of every official table, for determinism comparisons."""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        dump: dict[str, list[tuple]] = {}
        for table in sorted(OFFICIAL_COLUMNS):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            dump[table] = sorted(
                tuple(str(value) for value in tuple(row)) for row in rows
            )
        return dump
    finally:
        conn.close()


# ==========================================================================
# SCHEMA CONFORMANCE
# ==========================================================================

def test_generated_estate_passes_repository_schema_validation(generated):
    db_path, _ = generated
    # EstateRepository raises if a table or an official column is missing.
    repo = EstateRepository(db_path)
    try:
        for table in sorted(OFFICIAL_COLUMNS):
            assert repo.get_all(table) is not None
    finally:
        repo.close()


def test_every_official_table_has_rows(repository):
    for table in sorted(OFFICIAL_COLUMNS):
        assert repository.get_all(table), f"{table} is empty"


def test_no_extra_columns_beyond_the_official_schema(generated):
    db_path, _ = generated
    conn = sqlite3.connect(db_path)
    try:
        for table, columns in sorted(OFFICIAL_COLUMNS.items()):
            actual = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert actual == columns
    finally:
        conn.close()


def test_ledger_entry_id_is_an_integer(repository):
    entries = repository.get_all("ledger")
    assert entries
    assert all(isinstance(entry["entry_id"], int) for entry in entries)


def test_ids_follow_the_required_formats(repository):
    for table, prefix in (
        ("invoices", "INV-"),
        ("bank_txns", "BNK-"),
        ("purchase_orders", "PO-"),
        ("contracts", "CTR-"),
    ):
        id_column = ID_COLUMN[table]
        for row in repository.get_all(table):
            value = row[id_column]
            assert value.startswith(prefix)
            suffix = value[len(prefix):]
            assert len(suffix) == 5 and suffix.isdigit()

    for employee in repository.get_all("employees"):
        assert len(employee["emp_id"]) == 4
        assert employee["emp_id"].isdigit()
        assert "EMP:" not in employee["emp_id"]


def test_rfcs_and_clabes_are_well_formed_and_unique(repository):
    rfcs = [vendor["rfc"] for vendor in repository.get_all("vendors")]
    assert len(rfcs) == len(set(rfcs))
    for rfc in rfcs + [COMPANY_RFC]:
        assert 12 <= len(rfc) <= 13
        assert rfc == rfc.upper()

    clabes = [vendor["bank_clabe"] for vendor in repository.get_all("vendors")]
    clabes += [
        employee["bank_clabe"] for employee in repository.get_all("employees")
    ]
    for clabe in clabes + [COMPANY_CLABE]:
        assert len(clabe) == 18
        assert clabe.isdigit()


def test_efos_status_uses_the_official_lowercase_values(repository):
    statuses = {row["status"] for row in repository.get_all("efos_list")}
    assert statuses <= {"definitivo", "presunto"}


def test_company_is_the_receiver_and_payer(repository):
    purchase_invoices = [
        invoice
        for invoice in repository.get_all("invoices")
        if invoice["issuer_rfc"] != COMPANY_RFC
    ]
    assert purchase_invoices
    assert all(
        invoice["receiver_rfc"] == COMPANY_RFC for invoice in purchase_invoices
    )

    company_payments = [
        txn
        for txn in repository.get_all("bank_txns")
        if txn["reference"].startswith("Pago factura")
    ]
    assert company_payments
    assert all(txn["from_clabe"] == COMPANY_CLABE for txn in company_payments)


# ==========================================================================
# ARITHMETIC
# ==========================================================================

def test_every_invoice_reconciles_to_the_cent(repository):
    invoices = repository.get_all("invoices")
    assert invoices
    for invoice in invoices:
        subtotal = invoice["subtotal"]
        iva = invoice["iva"]
        total = invoice["total"]
        assert abs(round(subtotal * 0.16, 2) - iva) < 0.005
        assert abs(subtotal + iva - total) < 0.005


def test_ledger_entries_for_an_invoice_balance(repository):
    invoices = repository.get_all("invoices")
    assert invoices
    for invoice in invoices:
        entries = repository.get_ledger_for_invoice(invoice["uuid"])
        assert entries, invoice["uuid"]
        debit = round(sum(entry["debit"] for entry in entries), 2)
        credit = round(sum(entry["credit"] for entry in entries), 2)
        assert debit == credit


def test_every_invoice_uses_the_official_catalog_codes(repository):
    for invoice in repository.get_all("invoices"):
        assert invoice["uso_cfdi"] == "G03"
        assert invoice["forma_pago"] == "03"
        assert invoice["metodo_pago"] == "PUE"
        assert invoice["status"] in {"vigente", "cancelado"}


def test_every_honest_vendor_has_a_paper_trail(repository):
    vendors_without_full_trail = []

    for vendor in repository.get_all("vendors"):
        rfc = vendor["rfc"]
        invoices = repository.get_vendor_invoices(rfc)
        if not invoices:
            # Only the inactive-EFOS decoy has no activity at all.
            continue
        has_cover = bool(
            repository.get_vendor_contracts(rfc)
            or repository.get_vendor_purchase_orders(rfc)
        )
        paid = [
            txn
            for txn in repository.get_bank_transactions_for_clabe(
                vendor["bank_clabe"]
            )
            if txn["from_clabe"] == COMPANY_CLABE
        ]
        if not (has_cover and paid):
            vendors_without_full_trail.append(rfc)

    # Only planted schemes may lack a contract/PO or a payment; the phantom
    # vendor has no contract and no PO, and the round-tripping conduits after
    # the first one carry no invoices of their own.
    assert len(vendors_without_full_trail) <= len(SCHEME_TYPES)


# ==========================================================================
# DETERMINISM
# ==========================================================================

def test_same_seed_produces_identical_estate_and_answer_key(tmp_path):
    first_db = tmp_path / "first" / "estate.db"
    second_db = tmp_path / "second" / "estate.db"

    first_key = generate_estate(EstateSpec(seed=SEED), first_db)
    second_key = generate_estate(EstateSpec(seed=SEED), second_db)

    assert first_key == second_key
    assert _dump_all(first_db) == _dump_all(second_db)

    first_json = dump_answer_key(first_key, tmp_path / "first.json")
    second_json = dump_answer_key(second_key, tmp_path / "second.json")
    assert first_json.read_bytes() == second_json.read_bytes()


def test_different_seeds_produce_different_estates(tmp_path):
    first_db = tmp_path / "first" / "estate.db"
    second_db = tmp_path / "second" / "estate.db"

    first_key = generate_estate(EstateSpec(seed=SEED), first_db)
    second_key = generate_estate(EstateSpec(seed=OTHER_SEED), second_db)

    assert first_key != second_key
    assert _dump_all(first_db) != _dump_all(second_db)


def test_regenerating_over_an_existing_file_is_idempotent(tmp_path):
    db_path = tmp_path / "estate.db"
    first_key = generate_estate(EstateSpec(seed=SEED), db_path)
    first_dump = _dump_all(db_path)

    second_key = generate_estate(EstateSpec(seed=SEED), db_path)
    assert second_key == first_key
    assert _dump_all(db_path) == first_dump


# ==========================================================================
# ANSWER KEY
# ==========================================================================

def _required_keys(schema: dict, path: tuple[str, ...]) -> list[str]:
    node = schema["ground_truth"]
    for step in path:
        node = node["properties"][step]["items"]
    return node["required"]


def test_answer_key_matches_the_official_required_keys(generated):
    _, answer_key = generated
    schema = json.loads(GROUND_TRUTH_SCHEMA.read_text(encoding="utf-8"))

    for key in schema["ground_truth"]["required"]:
        assert key in answer_key

    assert answer_key["seed"] == SEED
    assert answer_key["company_rfc"] == COMPANY_RFC
    assert isinstance(answer_key["schemes"], list)
    assert isinstance(answer_key["decoys"], list)

    scheme_required = _required_keys(schema, ("schemes",))
    allowed_types = schema["ground_truth"]["properties"]["schemes"]["items"][
        "properties"
    ]["type"]["enum"]
    allowed_difficulty = schema["ground_truth"]["properties"]["schemes"][
        "items"
    ]["properties"]["difficulty"]["enum"]

    for scheme in answer_key["schemes"]:
        for key in scheme_required:
            assert key in scheme, (scheme.get("scheme_id"), key)
        assert scheme["type"] in allowed_types
        assert scheme["difficulty"] in allowed_difficulty
        assert isinstance(scheme["peso_amount"], float)
        assert scheme["peso_amount"] > 0

    decoy_required = _required_keys(schema, ("decoys",))
    for decoy in answer_key["decoys"]:
        for key in decoy_required:
            assert key in decoy, (decoy.get("entity"), key)


def test_all_requested_scheme_types_appear_once(generated):
    _, answer_key = generated
    types = [scheme["type"] for scheme in answer_key["schemes"]]
    assert sorted(types) == sorted(SCHEME_TYPES)

    scheme_ids = [scheme["scheme_id"] for scheme in answer_key["schemes"]]
    assert len(scheme_ids) == len(set(scheme_ids))


def test_scheme_entities_use_official_prefixes_only(generated):
    _, answer_key = generated
    entities = [
        entity
        for scheme in answer_key["schemes"]
        for entity in scheme["entities"]
    ] + [decoy["entity"] for decoy in answer_key["decoys"]]

    assert entities
    for entity in entities:
        assert entity.startswith(("RFC:", "EMP:"))
        assert not entity.startswith("CLABE:")


def test_scheme_entities_exist_in_the_estate(repository, generated):
    _, answer_key = generated
    for scheme in answer_key["schemes"]:
        for entity in scheme["entities"]:
            prefix, _, value = entity.partition(":")
            if prefix == "EMP":
                assert repository.record_exists("employees", value)
            elif value != COMPANY_RFC:
                assert repository.record_exists("vendors", value)


def test_supporting_ids_exist_in_the_estate(repository, generated):
    _, answer_key = generated
    for scheme in answer_key["schemes"]:
        for uuid in scheme["supporting_invoices"]:
            assert repository.record_exists("invoices", uuid), uuid
        for txn_id in scheme["supporting_txns"]:
            assert repository.record_exists("bank_txns", txn_id), txn_id

    for decoy in answer_key["decoys"]:
        for uuid in decoy.get("invoices", []):
            assert repository.record_exists("invoices", uuid), uuid


def test_scheme_peso_amount_matches_the_cited_invoice_totals(
    repository, generated
):
    _, answer_key = generated
    for scheme in answer_key["schemes"]:
        if scheme["type"] == "round_tripping":
            # Documented exception: the cycle's peso amount is the outbound leg.
            continue
        expected = round(
            sum(
                repository.get_invoice(uuid)["total"]
                for uuid in scheme["supporting_invoices"]
            ),
            2,
        )
        assert abs(scheme["peso_amount"] - expected) < 0.02 * max(expected, 1)


def test_decoy_count_and_non_empty_explanations(generated):
    _, answer_key = generated
    assert len(answer_key["decoys"]) == EstateSpec(seed=SEED).n_decoys
    for decoy in answer_key["decoys"]:
        assert decoy["why_innocent"].strip()
        assert decoy["signal"].strip()
        # Absence in the estate is never phrased as absence in reality.
        assert "does not exist" not in decoy["why_innocent"]


def test_decoy_shapes_cover_the_required_signals(generated):
    _, answer_key = generated
    signals = {decoy["signal"] for decoy in answer_key["decoys"]}
    assert {
        "vendor_efos_record_match",
        "vendor_employee_shared_bank_prefix",
        "directed_bank_transfer_cycle",
        "short_window_similar_invoice_cluster",
        "period_end_cancelled_revenue_invoice",
        "shared_vendor_clabe",
        "vendor_to_employee_bank_transfer",
    } <= signals


def test_decoy_entities_are_never_also_scheme_entities(generated):
    _, answer_key = generated
    scheme_entities = {
        entity
        for scheme in answer_key["schemes"]
        for entity in scheme["entities"]
    }
    decoy_entities = {decoy["entity"] for decoy in answer_key["decoys"]}
    assert not (scheme_entities & decoy_entities) - {f"RFC:{COMPANY_RFC}"}


def test_decoy_count_is_configurable(tmp_path):
    answer_key = generate_estate(
        EstateSpec(seed=SEED, n_decoys=3), tmp_path / "estate.db"
    )
    assert len(answer_key["decoys"]) == 3


def test_requesting_a_subset_of_schemes(tmp_path):
    answer_key = generate_estate(
        EstateSpec(seed=SEED, schemes=("kickback",)),
        tmp_path / "estate.db",
    )
    assert [scheme["type"] for scheme in answer_key["schemes"]] == ["kickback"]


def test_unknown_scheme_type_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown scheme"):
        generate_estate(
            EstateSpec(seed=SEED, schemes=("wire_fraud",)),
            tmp_path / "estate.db",
        )


# ==========================================================================
# PLANTED SCHEME SHAPES
# ==========================================================================

def _scheme(answer_key: dict, scheme_type: str) -> dict:
    for scheme in answer_key["schemes"]:
        if scheme["type"] == scheme_type:
            return scheme
    raise AssertionError(f"{scheme_type} was not planted")


def test_phantom_vendor_is_listed_in_efos_before_its_invoices(
    repository, generated
):
    _, answer_key = generated
    scheme = _scheme(answer_key, "phantom_vendor")
    rfc = scheme["entities"][0].removeprefix("RFC:")

    efos = repository.get_efos_record(rfc)
    assert efos is not None
    assert efos["status"] == "definitivo"

    for uuid in scheme["supporting_invoices"]:
        invoice = repository.get_invoice(uuid)
        assert invoice["issue_date"] > efos["publication_date"]

    assert repository.get_vendor_contracts(rfc) == []
    assert repository.get_vendor_purchase_orders(rfc) == []


def test_kickback_moves_money_from_the_vendor_to_the_approver(
    repository, generated
):
    _, answer_key = generated
    scheme = _scheme(answer_key, "kickback")
    rfc = next(
        entity.removeprefix("RFC:")
        for entity in scheme["entities"]
        if entity.startswith("RFC:")
    )
    emp_id = next(
        entity.removeprefix("EMP:")
        for entity in scheme["entities"]
        if entity.startswith("EMP:")
    )

    vendor = repository.get_vendor(rfc)
    employee = repository.get_employee(emp_id)
    assert vendor is not None and employee is not None

    kickbacks = [
        repository.get_bank_txn(txn_id)
        for txn_id in scheme["supporting_txns"]
    ]
    to_employee = [
        txn
        for txn in kickbacks
        if txn["from_clabe"] == vendor["bank_clabe"]
        and txn["to_clabe"] == employee["bank_clabe"]
    ]
    assert to_employee

    approved = repository.get_vendor_purchase_orders(rfc)
    assert approved
    assert all(po["approver"] == employee["name"] for po in approved)


def test_round_tripping_cycle_returns_to_the_company(repository, generated):
    _, answer_key = generated
    scheme = _scheme(answer_key, "round_tripping")
    txns = [
        repository.get_bank_txn(txn_id) for txn_id in scheme["supporting_txns"]
    ]
    assert 3 <= len(txns) <= 4

    dates = [txn["date"] for txn in txns]
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates)

    assert txns[0]["from_clabe"] == COMPANY_CLABE
    assert txns[-1]["to_clabe"] == COMPANY_CLABE
    for previous, following in zip(txns, txns[1:]):
        assert previous["to_clabe"] == following["from_clabe"]

    amounts = [txn["amount"] for txn in txns]
    assert (max(amounts) - min(amounts)) / max(amounts) < 0.10


def test_threshold_splitting_stays_under_the_internal_limit(
    repository, generated
):
    _, answer_key = generated
    scheme = _scheme(answer_key, "threshold_splitting")
    rfc = scheme["entities"][0].removeprefix("RFC:")

    invoices = [
        repository.get_invoice(uuid) for uuid in scheme["supporting_invoices"]
    ]
    assert len(invoices) >= 3
    assert all(
        invoice["total"] < INTERNAL_APPROVAL_THRESHOLD_MXN
        for invoice in invoices
    )
    assert sum(invoice["total"] for invoice in invoices) > (
        2 * INTERNAL_APPROVAL_THRESHOLD_MXN
    )

    dates = sorted(invoice["issue_date"] for invoice in invoices)
    assert dates[-1] > dates[0]

    orders = repository.get_vendor_purchase_orders(rfc)
    assert orders
    assert all(
        order["amount"] < INTERNAL_APPROVAL_THRESHOLD_MXN for order in orders
    )


def test_threshold_splitting_records_the_internal_limit_in_notes(generated):
    _, answer_key = generated
    scheme = _scheme(answer_key, "threshold_splitting")

    assert "notes" in scheme
    assert isinstance(scheme["notes"], str)
    assert f"{INTERNAL_APPROVAL_THRESHOLD_MXN:,.2f}" in scheme["notes"]
    # The threshold must not be smuggled into the identifier.
    assert scheme["scheme_id"] == "S4_threshold_splitting_1"
    assert "100" not in scheme["scheme_id"]
    assert "description" not in scheme


def test_revenue_inflation_invoices_are_issued_by_the_company(
    repository, generated
):
    _, answer_key = generated
    scheme = _scheme(answer_key, "revenue_inflation")
    invoices = [
        repository.get_invoice(uuid) for uuid in scheme["supporting_invoices"]
    ]
    assert invoices
    assert all(invoice["issuer_rfc"] == COMPANY_RFC for invoice in invoices)
    assert any(invoice["status"] == "cancelado" for invoice in invoices)

    # No bank receipt anywhere in the estate references these invoices.
    references = [txn["reference"] for txn in repository.get_all("bank_txns")]
    for invoice in invoices:
        assert all(invoice["uuid"] not in reference for reference in references)

    for invoice in invoices:
        entries = repository.get_ledger_for_invoice(invoice["uuid"])
        assert any(entry["credit"] > 0 for entry in entries)


# ==========================================================================
# CLI
# ==========================================================================

def test_cli_writes_both_artifacts(tmp_path, capsys):
    out_dir = tmp_path / "estates" / "101"
    exit_code = generate_estate_cli.main(
        ["--seed", str(SEED), "--out-dir", str(out_dir)]
    )
    assert exit_code == 0

    db_path = out_dir / "estate.db"
    key_path = out_dir / "ground_truth.json"
    assert db_path.exists()
    assert key_path.exists()

    answer_key = json.loads(key_path.read_text(encoding="utf-8"))
    assert answer_key["seed"] == SEED
    assert answer_key["company_rfc"] == COMPANY_RFC

    repo = EstateRepository(db_path)
    repo.close()

    captured = capsys.readouterr()
    assert "estate:" in captured.out
    assert "answer key:" in captured.out
