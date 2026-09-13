"""Tests for the interface layer: the audit bridge and the credential store.

The widgets themselves are not asserted on -- what matters is that the bridge
behaves, that the interface cannot be made to accuse without the gate, and that
no password is ever written to disk.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui import auth
from ui.audit_bridge import (AuditResult, EstateFormatError, derive_seed,
                             prepare_estate, run_audit)

ESTATE_DB = REPO_ROOT / "dev_eval" / "robust_realism_estate.db"
ESTATE_ZIP = REPO_ROOT / "dev_eval" / "robust_realism_estate_csv.zip"
GROUND_TRUTH = REPO_ROOT / "dev_eval" / "robust_realism_ground_truth.json"

needs_estate = pytest.mark.skipif(
    not ESTATE_DB.exists(),
    reason="run python dev_eval/generate_robust_realism_estate.py first")


# ------------------------------------------------------------------ auth ---

def test_password_is_never_written_to_disk(tmp_path):
    store = tmp_path / "credentials.json"
    auth.create_user("auditor", "a very long password", store=store)
    blob = store.read_text(encoding="utf-8")
    assert "a very long password" not in blob
    assert "password" not in json.loads(blob)["users"][0]


def test_correct_password_verifies_and_wrong_one_does_not(tmp_path):
    store = tmp_path / "credentials.json"
    auth.create_user("auditor", "a very long password", store=store)
    assert auth.verify_user("auditor", "a very long password", store=store)
    assert not auth.verify_user("auditor", "a very long passwore", store=store)
    assert not auth.verify_user("auditor", "", store=store)


def test_unknown_user_is_rejected_without_crashing(tmp_path):
    store = tmp_path / "credentials.json"
    auth.create_user("auditor", "a very long password", store=store)
    assert not auth.verify_user("someone-else", "a very long password",
                                store=store)


def test_username_match_is_case_insensitive_but_display_is_preserved(tmp_path):
    store = tmp_path / "credentials.json"
    auth.create_user("Auditor", "a very long password", store=store)
    assert auth.verify_user("auditor", "a very long password", store=store)
    with pytest.raises(auth.AuthError):
        auth.create_user("AUDITOR", "another long password", store=store)


def test_weak_credentials_are_refused(tmp_path):
    store = tmp_path / "credentials.json"
    with pytest.raises(auth.AuthError):
        auth.create_user("ab", "a very long password", store=store)
    with pytest.raises(auth.AuthError):
        auth.create_user("auditor", "short", store=store)
    assert auth.user_count(store) == 0


def test_two_users_with_the_same_password_get_different_hashes(tmp_path):
    store = tmp_path / "credentials.json"
    auth.create_user("one", "the same password here", store=store)
    auth.create_user("two", "the same password here", store=store)
    users = json.loads(store.read_text(encoding="utf-8"))["users"]
    assert users[0]["salt"] != users[1]["salt"]
    assert users[0]["hash"] != users[1]["hash"]


def test_missing_store_is_not_an_error(tmp_path):
    assert auth.user_count(tmp_path / "nope.json") == 0
    assert not auth.verify_user("anyone", "anything",
                                store=tmp_path / "nope.json")


# ------------------------------------------------------ estate selection ---

def test_non_estate_files_are_refused_with_a_usable_message(tmp_path):
    spreadsheet = tmp_path / "accounts.xlsx"
    spreadsheet.write_bytes(b"not really a spreadsheet")
    with pytest.raises(EstateFormatError) as excinfo:
        prepare_estate(spreadsheet)
    assert "estate" in str(excinfo.value).lower()

    with pytest.raises(EstateFormatError):
        prepare_estate(tmp_path / "does-not-exist.db")


def test_a_sqlite_file_without_the_official_tables_is_refused(tmp_path):
    path = tmp_path / "other.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE something (id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(EstateFormatError) as excinfo:
        prepare_estate(path)
    assert "missing required estate tables" in str(excinfo.value)


def test_a_zip_that_is_not_an_estate_bundle_is_refused(tmp_path):
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "hello")
    with pytest.raises(EstateFormatError) as excinfo:
        prepare_estate(path)
    assert "estate_csv.zip" in str(excinfo.value)


@needs_estate
def test_the_sqlite_estate_is_accepted_without_copying(tmp_path):
    path, temp_dir = prepare_estate(ESTATE_DB)
    assert path == ESTATE_DB
    assert temp_dir is None


@needs_estate
def test_the_csv_zip_is_materialised_and_cleaned_up():
    import shutil
    path, temp_dir = prepare_estate(ESTATE_ZIP)
    try:
        assert temp_dir is not None and path.exists()
        conn = sqlite3.connect(path)
        n = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        conn.close()
        assert n > 0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# --------------------------------------------------------------- bridge ---

@needs_estate
def test_run_audit_produces_findings_and_declined_leads(tmp_path):
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    assert isinstance(result, AuditResult)
    assert result.table_counts["invoices"] > 0
    assert result.observations > 0
    assert result.leads, "every run should close some leads explicitly"
    assert Path(result.submission_path).exists()


@needs_estate
def test_the_bridge_makes_no_llm_call_and_says_so(tmp_path):
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    assert result.llm_calls == 0
    assert result.mxn_cost == 0.0
    payload = json.loads(Path(result.submission_path).read_text(encoding="utf-8"))
    assert payload["run_metadata"]["llm_calls"] == 0
    assert payload["run_metadata"]["deterministic"] is True


@needs_estate
def test_the_same_estate_produces_the_same_submission(tmp_path):
    first = run_audit(ESTATE_DB, out_dir=tmp_path / "a")
    second = run_audit(ESTATE_DB, out_dir=tmp_path / "b")
    assert first.seed == second.seed == derive_seed(ESTATE_DB)
    a = json.loads(Path(first.submission_path).read_text(encoding="utf-8"))
    b = json.loads(Path(second.submission_path).read_text(encoding="utf-8"))
    for payload in (a, b):
        payload["run_metadata"]["wall_clock_seconds"] = 0
    assert a == b


@needs_estate
def test_both_estate_forms_give_the_same_answer(tmp_path):
    from_db = run_audit(ESTATE_DB, out_dir=tmp_path / "db", seed=1)
    from_zip = run_audit(ESTATE_ZIP, out_dir=tmp_path / "zip", seed=1)
    assert from_db.table_counts == from_zip.table_counts
    assert ([(f.scheme_type, f.entities, f.peso_amount) for f in from_db.findings]
            == [(f.scheme_type, f.entities, f.peso_amount)
                for f in from_zip.findings])


@needs_estate
def test_the_interface_never_accuses_a_known_decoy(tmp_path):
    """The scoring rule: an accusation against a non-scheme entity is a false
    accusation. This is the one assertion that would cost us the most."""
    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    company = "RFC:" + truth["company_rfc"]
    scheme_entities = {e for s in truth["schemes"] for e in s["entities"]}
    decoy_entities = {d["entity"] for d in truth["decoys"]}

    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    accused = {e for f in result.findings for e in f.entities}
    assert not (accused & decoy_entities), accused & decoy_entities
    assert accused <= (scheme_entities | {company}), accused - scheme_entities


@needs_estate
def test_declined_leads_name_the_records_that_were_read(tmp_path):
    """Generic text such as 'insufficient evidence' is scored as generic."""
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    for lead in result.leads:
        assert lead.reason.strip()
        assert "insufficient evidence" not in lead.reason.lower()
        assert lead.tool_calls_made, lead.entity
        # The reason must cite something concrete from the estate.
        assert any(token in lead.reason for token in
                   ("invoice(s)", "Account ", "employee", "Evidence Gate")), \
            lead.reason


@needs_estate
def test_the_submission_passes_the_official_validator(tmp_path):
    import subprocess
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    validator = (REPO_ROOT / "official_materials" / "student-materials"
                 / "forensic-auditor" / "validate_format.py")
    completed = subprocess.run(
        [sys.executable, str(validator), "--submission", result.submission_path,
         "--estate", str(ESTATE_DB)],
        capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout


# ------------------------------------------------------------ app module ---

def test_the_app_can_be_imported_without_starting_a_main_loop():
    """Importing must not seize the thread, or nothing can ever test it."""
    pytest.importorskip("customtkinter")
    if not sys.platform.startswith(("win", "darwin")) and not __import__(
            "os").environ.get("DISPLAY"):
        pytest.skip("no display available")
    import ui.leglens_app as app
    assert hasattr(app, "main")
    assert callable(app.initial_page)
    app.root.destroy()


def test_the_app_does_not_reference_the_plaintext_credential_file():
    source = (REPO_ROOT / "ui" / "leglens_app.py").read_text(encoding="utf-8")
    body = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    body = body.split('"""', 2)[-1]  # drop the module docstring
    assert "UsernamePassword" not in body
    assert "read_excel" not in body


# ------------------------------------------------------------- case file ---

@needs_estate
def test_case_file_has_every_required_section_in_order(tmp_path):
    """official_materials/.../case_file_structure.md lists five sections and
    requires them in this order. A judge reads this document."""
    from ui import case_file
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    html = case_file.render_html(result)

    order = ["Case file", "Executive summary", "Findings",
             "Leads not pursued", "Method and limits"]
    positions = [html.find(section) for section in order]
    assert all(p >= 0 for p in positions), dict(zip(order, positions))
    assert positions == sorted(positions), "sections are out of order"


@needs_estate
def test_case_file_header_carries_the_three_cost_numbers(tmp_path):
    from ui import case_file
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    html = case_file.render_html(result)
    for token in ("Estate seed", "Model calls", "Cost", "Wall clock",
                  "Deterministic"):
        assert token in html, token


@needs_estate
def test_money_trail_is_a_rendered_diagram_not_prose(tmp_path):
    """Prose-only caps Clarity at 3, so this is worth a test of its own."""
    from ui import case_file
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    if not result.findings:
        pytest.skip("no finding in this run to draw a trail for")
    html = case_file.render_html(result)
    assert "<svg" in html
    assert "Money trail" in html


@needs_estate
def test_case_file_fetches_nothing_so_it_renders_offline(tmp_path):
    """Judges may ask us to open it with connectivity disabled."""
    import re
    from ui import case_file
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    html = case_file.render_html(result)
    refs = re.findall(r'(?:src|href)\s*=\s*["\'](?!#)([^"\']+)', html)
    assert refs == [], refs
    # The SVG namespace URI is a declaration, not a fetch: opening the file
    # requests nothing from w3.org. Any OTHER absolute URL would be a request.
    fetched = [u for u in re.findall(r'https?://[^\s"\'<>]+', html)
               if not u.startswith("http://www.w3.org/")]
    assert fetched == [], fetched


@needs_estate
def test_case_file_states_what_the_system_cannot_do(tmp_path):
    """Stating limits plainly scores better than implying completeness."""
    from ui import case_file
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    html = case_file.render_html(result)
    assert "cannot detect" in html
    assert "no record was found in the supplied estate" in html.lower()
    assert "phantom_vendor" in html


@needs_estate
def test_case_file_lists_every_closed_lead_with_its_reason(tmp_path):
    from ui import case_file
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    html = case_file.render_html(result)
    for lead in result.leads[:6]:
        assert lead.entity in html, lead.entity
    assert "Tools called" in html and "Closed by" in html


@needs_estate
def test_case_file_export_writes_a_readable_file(tmp_path):
    from ui import case_file
    result = run_audit(ESTATE_DB, out_dir=tmp_path)
    out = case_file.export(result, tmp_path / "case_file.html")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
    assert len(text) > 3000
