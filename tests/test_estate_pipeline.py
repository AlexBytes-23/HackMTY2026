from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.estate import EstateRepository
from src.core.models import InvestigationLoopResult
from src.investigation.preverification_pipeline import PreVerificationResult
import src.investigation.estate_pipeline as estate_pipeline


class NeverCalledLLM:
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("LLM should not be called in this test.")


def _create_official_estate(path: Path, *, with_efos_match: bool = True) -> None:
    conn = sqlite3.connect(path)

    conn.executescript(
        """
        CREATE TABLE vendors (
            rfc TEXT PRIMARY KEY, legal_name TEXT, registered_date TEXT,
            address TEXT, bank_clabe TEXT, category TEXT, contact_email TEXT
        );
        CREATE TABLE invoices (
            uuid TEXT PRIMARY KEY, issuer_rfc TEXT, receiver_rfc TEXT,
            issue_date TEXT, subtotal REAL, iva REAL, total REAL,
            concepto_text TEXT, uso_cfdi TEXT, forma_pago TEXT,
            metodo_pago TEXT, status TEXT
        );
        CREATE TABLE ledger (
            entry_id INTEGER PRIMARY KEY, date TEXT, account_code TEXT,
            account_name TEXT, debit REAL, credit REAL, description TEXT,
            invoice_uuid TEXT, cost_center TEXT, approver TEXT
        );
        CREATE TABLE bank_txns (
            txn_id TEXT PRIMARY KEY, date TEXT, from_clabe TEXT, to_clabe TEXT,
            amount REAL, reference TEXT, channel TEXT
        );
        CREATE TABLE purchase_orders (
            po_id TEXT PRIMARY KEY, vendor_rfc TEXT, date TEXT, amount REAL,
            requester TEXT, approver TEXT, description TEXT
        );
        CREATE TABLE contracts (
            contract_id TEXT PRIMARY KEY, vendor_rfc TEXT, start_date TEXT,
            value REAL, scope_text TEXT
        );
        CREATE TABLE employees (
            emp_id TEXT PRIMARY KEY, name TEXT, role TEXT,
            bank_clabe TEXT, hire_date TEXT
        );
        CREATE TABLE efos_list (
            rfc TEXT PRIMARY KEY, legal_name TEXT, status TEXT,
            publication_date TEXT
        );
        """
    )

    conn.execute(
        "INSERT INTO vendors VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "AAA010101AAA",
            "Vendor Uno",
            "2020-01-01",
            "Address",
            "012345678901234567",
            "services",
            "vendor@example.com",
        ),
    )
    conn.execute(
        "INSERT INTO employees VALUES (?, ?, ?, ?, ?)",
        ("EMP001", "Employee", "Analyst", "999999999999999999", "2024-01-01"),
    )

    if with_efos_match:
        conn.execute(
            "INSERT INTO efos_list VALUES (?, ?, ?, ?)",
            ("AAA010101AAA", "Vendor Uno", "definitivo", "2025-01-01"),
        )

    conn.commit()
    conn.close()


def _blocked_preverification(case_state, **kwargs):
    initial = InvestigationLoopResult(
        case_state=case_state,
        decisions=[],
        iterations=0,
        stop_reason="close_inconclusive",
    )
    return PreVerificationResult(
        case_state=case_state,
        initial_investigation=initial,
        ready_for_verification=False,
        stop_reason="investigator_closed_inconclusive",
    )


def _ready_preverification(case_state, **kwargs):
    case_state.status = "ready_for_verification"
    initial = InvestigationLoopResult(
        case_state=case_state,
        decisions=[],
        iterations=0,
        stop_reason="ready_for_verification",
    )
    return PreVerificationResult(
        case_state=case_state,
        initial_investigation=initial,
        target_hypothesis_id="H1",
        ready_for_verification=True,
        stop_reason="ready_for_verification",
    )


def test_real_repository_runs_deterministic_discovery(tmp_path, monkeypatch):
    db_path = tmp_path / "estate.db"
    _create_official_estate(db_path, with_efos_match=True)
    monkeypatch.setattr(
        estate_pipeline,
        "run_preverification_pipeline",
        _blocked_preverification,
    )

    with EstateRepository(db_path) as estate:
        result = estate_pipeline.run_estate_preverification(
            estate=estate,
            llm_client=NeverCalledLLM(),
        )

    assert result.metadata.observation_count == 1
    assert result.metadata.lead_count == 1
    assert result.metadata.processed_case_count == 1
    assert result.metadata.blocked_case_count == 1
    assert result.metadata.error_count == 0
    assert result.observations[0].signal_type == "vendor_efos_record_match"
    assert result.leads[0].subject_entities == ["RFC:AAA010101AAA"]
    assert result.case_outcomes[0].case_id == "CASE-LEAD-0001"


def test_no_observations_means_no_cases_and_no_llm(tmp_path):
    db_path = tmp_path / "estate.db"
    _create_official_estate(db_path, with_efos_match=False)

    with EstateRepository(db_path) as estate:
        result = estate_pipeline.run_estate_preverification(
            estate=estate,
            llm_client=NeverCalledLLM(),
        )

    assert result.observations == []
    assert result.leads == []
    assert result.case_outcomes == []
    assert result.metadata.processed_case_count == 0


def test_ready_case_is_counted_separately(tmp_path, monkeypatch):
    db_path = tmp_path / "estate.db"
    _create_official_estate(db_path, with_efos_match=True)
    monkeypatch.setattr(
        estate_pipeline,
        "run_preverification_pipeline",
        _ready_preverification,
    )

    with EstateRepository(db_path) as estate:
        result = estate_pipeline.run_estate_preverification(
            estate=estate,
            llm_client=NeverCalledLLM(),
        )

    assert result.metadata.ready_for_verification_count == 1
    assert result.metadata.blocked_case_count == 0
    assert result.case_outcomes[0].status == "ready_for_verification"


def test_case_error_is_visible_not_relabelled_inconclusive(tmp_path, monkeypatch):
    db_path = tmp_path / "estate.db"
    _create_official_estate(db_path, with_efos_match=True)

    def explode(case_state, **kwargs):
        raise ValueError("bad model response")

    monkeypatch.setattr(estate_pipeline, "run_preverification_pipeline", explode)

    with EstateRepository(db_path) as estate:
        result = estate_pipeline.run_estate_preverification(
            estate=estate,
            llm_client=NeverCalledLLM(),
        )

    outcome = result.case_outcomes[0]
    assert outcome.status == "error"
    assert outcome.stop_reason == "case_processing_error"
    assert outcome.error_type == "ValueError"
    assert "bad model response" in outcome.error_message
    assert result.metadata.error_count == 1
    assert result.metadata.blocked_case_count == 0


def test_max_cases_must_be_positive(tmp_path):
    db_path = tmp_path / "estate.db"
    _create_official_estate(db_path, with_efos_match=True)

    with EstateRepository(db_path) as estate:
        with pytest.raises(ValueError, match="max_cases"):
            estate_pipeline.run_estate_preverification(
                estate=estate,
                llm_client=NeverCalledLLM(),
                max_cases=0,
            )
