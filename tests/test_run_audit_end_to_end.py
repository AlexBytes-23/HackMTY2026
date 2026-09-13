"""End-to-end: estate.db -> src.run_audit -> submission.json -> official validator.

This is the one test that exercises the whole vertical path and then hands the
result to the real ``validate_format.py`` shipped with the challenge.  Every other
official-format test in this suite validates a hand-written Submission object,
which cannot catch a pipeline that emits the wrong shape.

The LLM is a scripted stub.  It only decides *what to investigate next* and returns
adversarial verdicts; every monetary fact, exhibit and authorization in the asserted
output is produced by deterministic Python.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from src.core.estate import ID_COLUMN, OFFICIAL_COLUMNS
from src.llm.runtime import InstrumentedLLMClient
from src.output.models import LeadNotPursued
from src.run_audit import _official_entity
from src.run_audit import main as run_audit_main


REPO_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_DIR = REPO_ROOT / "official_materials" / "student-materials" / "forensic-auditor"

EFOS_RFC = "PHA010101AA1"
CLEAN_RFC = "REA010101BB2"


# ==========================================================================
# ESTATE
# ==========================================================================

def _build_estate(db_path: Path) -> None:
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

    # A vendor that appears in the supplied EFOS table, with two invoices.
    conn.execute(
        "INSERT INTO efos_list (rfc, legal_name, status, publication_date) "
        f"VALUES ('{EFOS_RFC}', 'Phantom SA de CV', 'Definitivo', '2025-03-01')"
    )
    conn.execute(
        "INSERT INTO vendors (rfc, legal_name, bank_clabe, category) "
        f"VALUES ('{EFOS_RFC}', 'Phantom SA de CV', '012345678901234567', 'servicios')"
    )
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
        f"VALUES ('INV-0001', '{EFOS_RFC}', 'ACME010101AA1', '420000.00', '2025-04-01')"
    )
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
        f"VALUES ('INV-0002', '{EFOS_RFC}', 'ACME010101AA1', '380000.00', '2025-04-18')"
    )

    # A vendor that is NOT in the EFOS table. It must not produce a finding.
    conn.execute(
        "INSERT INTO vendors (rfc, legal_name, bank_clabe, category) "
        f"VALUES ('{CLEAN_RFC}', 'Real SA de CV', '098765432109876543', 'insumos')"
    )
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
        f"VALUES ('INV-0900', '{CLEAN_RFC}', 'ACME010101AA1', '111000.00', '2025-05-02')"
    )

    conn.commit()
    conn.close()


# ==========================================================================
# SCRIPTED LLM
# ==========================================================================

class ScriptedLLM:
    """Deterministic stand-in for the Investigator / Challenger / Method Critic.

    Routes on the system prompt, mirroring what each real agent is asked for.
    """

    provider = "scripted"
    model = "scripted-1"

    def __init__(self) -> None:
        self.calls = 0
        self.last_usage = None

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _subject_rfc(payload: dict) -> str | None:
        case_state = payload.get("case_state", payload)
        for entity in case_state.get("subject_entities", []):
            if entity.startswith("RFC:"):
                return entity[len("RFC:"):]
        return None

    @staticmethod
    def _hypothesis_id(payload: dict) -> str:
        # Each agent labels the target differently: the Method Critic gets a
        # top-level target_hypothesis_id, the Challenger a target_hypothesis object.
        if isinstance(payload.get("target_hypothesis_id"), str):
            return payload["target_hypothesis_id"]

        target = payload.get("target_hypothesis")
        if isinstance(target, dict) and "hypothesis_id" in target:
            return target["hypothesis_id"]

        case_state = payload.get("case_state", payload)
        hypotheses = case_state.get("hypotheses", [])
        if hypotheses:
            return hypotheses[0]["hypothesis_id"]
        return "H-001"

    @staticmethod
    def _payload(user_prompt: str) -> dict:
        # The Investigator prompt wraps its JSON in prose on both sides, so we
        # decode the first JSON object and ignore whatever trails it.
        start = user_prompt.find("{")
        payload, _ = json.JSONDecoder().raw_decode(user_prompt[start:])
        return payload

    # -- protocol -------------------------------------------------------
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        payload = self._payload(user_prompt)

        if "You are the Challenger" in system_prompt:
            return json.dumps(
                {
                    "target_hypothesis_id": self._hypothesis_id(payload),
                    "outcome": "survives",
                    "reasoning_summary": (
                        "No unresolved material objection was found with the "
                        "available evidence and tools."
                    ),
                }
            )

        if "You are the Method Critic" in system_prompt:
            return json.dumps(
                {
                    "target_hypothesis_id": self._hypothesis_id(payload),
                    "outcome": "clear",
                    "reasoning_summary": (
                        "The method relies on record matching against the supplied "
                        "estate, with no causal or probabilistic overreach."
                    ),
                }
            )

        # Investigator.
        case_state = payload["case_state"]
        rfc = self._subject_rfc(payload)
        hypothesis_id = self._hypothesis_id(payload)
        already_queried = {
            record["action_name"] for record in case_state.get("actions_taken", [])
        }

        hypothesis = {
            "hypothesis_id": hypothesis_id,
            "statement": (
                f"Invoices issued by {rfc} may correspond to simulated operations."
            ),
            "scheme_type": "phantom_vendor",
            "status": "supported",
            "supporting_evidence_ids": [],
            "counter_evidence_ids": [],
            "unresolved_questions": [],
        }

        if rfc and "get_vendor_invoices" not in already_queried:
            return json.dumps(
                {
                    "decision": "investigate",
                    "current_assessment": "The estate links this RFC to the EFOS table.",
                    "hypotheses": [hypothesis],
                    "next_action": {
                        "action_name": "get_vendor_invoices",
                        "arguments": {"vendor_rfc": rfc},
                        "reason": "Establish which invoices this RFC actually issued.",
                        "question_resolved": "Which invoices does this RFC have?",
                    },
                    "new_unknowns": [],
                    "resolved_unknowns": [],
                    "reason": "Invoice records are needed before any interpretation.",
                }
            )

        return json.dumps(
            {
                "decision": "request_review",
                "current_assessment": "The documentary core is assembled.",
                "hypotheses": [hypothesis],
                "next_action": None,
                "new_unknowns": [],
                "resolved_unknowns": [],
                "reason": "The hypothesis needs adversarial review before verification.",
            }
        )


# ==========================================================================
# FIXTURES
# ==========================================================================

@pytest.fixture
def estate_db(tmp_path):
    db_path = tmp_path / "estate.db"
    _build_estate(db_path)
    return db_path


def _run(estate_db, out_path, seed=4242):
    # Instrumented exactly as the real CLI instruments its client, so llm_calls in
    # run_metadata is a real count rather than a default.
    client = InstrumentedLLMClient(ScriptedLLM())
    exit_code = run_audit_main(
        [
            "--estate",
            str(estate_db),
            "--out",
            str(out_path),
            "--seed",
            str(seed),
        ],
        llm_client=client,
        replay_mode=True,
    )
    return exit_code, client


# ==========================================================================
# TESTS
# ==========================================================================

def test_cli_produces_a_submission_with_a_phantom_vendor_finding(estate_db, tmp_path):
    out_path = tmp_path / "runs" / "4242" / "submission.json"

    exit_code, client = _run(estate_db, out_path)

    assert exit_code == 0
    assert out_path.exists()
    assert client.llm_calls > 0

    submission = json.loads(out_path.read_text(encoding="utf-8"))

    assert submission["seed"] == 4242
    assert len(submission["findings"]) == 1

    finding = submission["findings"][0]
    assert finding["scheme_type"] == "phantom_vendor"
    assert finding["entities"] == [f"RFC:{EFOS_RFC}"]
    assert finding["confidence"] == "probable"

    # 420000 + 380000, computed by the scope rule, not by trying candidates.
    assert finding["peso_amount"] == pytest.approx(800000.00)

    assert finding["rule_broken"] == "SAT Articulo 69-B - Operaciones inexistentes (EFOS)"
    assert len(finding["exhibits"]) >= 3


def test_cli_does_not_accuse_the_vendor_absent_from_the_efos_table(estate_db, tmp_path):
    out_path = tmp_path / "submission.json"

    _run(estate_db, out_path)

    submission = json.loads(out_path.read_text(encoding="utf-8"))

    accused = {
        entity
        for finding in submission["findings"]
        for entity in finding["entities"]
    }
    assert f"RFC:{CLEAN_RFC}" not in accused

    cited_records = {
        exhibit["record_id"]
        for finding in submission["findings"]
        for exhibit in finding["exhibits"]
    }
    assert "INV-0900" not in cited_records


def test_no_clabe_identifier_reaches_the_submission(estate_db, tmp_path):
    out_path = tmp_path / "submission.json"

    _run(estate_db, out_path)

    raw = out_path.read_text(encoding="utf-8")
    assert "CLABE:" not in raw
    assert "012345678901234567" not in raw
    assert "098765432109876543" not in raw


def test_run_metadata_is_measured_not_fabricated(estate_db, tmp_path):
    out_path = tmp_path / "submission.json"

    _run(estate_db, out_path)

    metadata = json.loads(out_path.read_text(encoding="utf-8"))["run_metadata"]

    # wall_clock_seconds covers the whole run, so it is strictly positive even
    # when no provider call was made.
    assert metadata["wall_clock_seconds"] > 0
    assert isinstance(metadata["llm_calls"], int)
    assert metadata["mxn_cost"] == 0.0


def test_leads_not_pursued_reasons_are_specific(estate_db, tmp_path):
    out_path = tmp_path / "submission.json"

    _run(estate_db, out_path)

    leads = json.loads(out_path.read_text(encoding="utf-8"))["leads_not_pursued"]

    for lead in leads:
        LeadNotPursued.model_validate(lead)
        assert lead["entity"].startswith(("RFC:", "EMP:"))
        assert lead["reason"].strip()
        # A reason must name what was looked at, not merely assert a verdict.
        # Case-insensitive: the verdict is capitalised when it follows the
        # sentence naming the documents that were read.
        assert any(
            marker in lead["reason"].lower()
            for marker in ("reviewed ", "action bank call", "execution failure")
        ), lead["reason"]


def test_run_is_byte_for_byte_deterministic(estate_db, tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    _run(estate_db, first)
    _run(estate_db, second)

    first_data = json.loads(first.read_text(encoding="utf-8"))
    second_data = json.loads(second.read_text(encoding="utf-8"))

    # wall_clock_seconds is a real measurement and cannot repeat exactly.
    first_data["run_metadata"].pop("wall_clock_seconds")
    second_data["run_metadata"].pop("wall_clock_seconds")

    assert json.dumps(first_data, sort_keys=True) == json.dumps(
        second_data, sort_keys=True
    )


@pytest.mark.skipif(
    not OFFICIAL_DIR.exists(), reason="Official materials not present"
)
def test_pipeline_output_passes_the_official_validator(estate_db, tmp_path):
    sys.path.insert(0, str(OFFICIAL_DIR))
    import validate_format

    out_path = tmp_path / "submission.json"
    _run(estate_db, out_path)

    submission = json.loads(out_path.read_text(encoding="utf-8"))

    errors = validate_format.validate_structure(submission)
    errors += validate_format.validate_against_estate(submission, str(estate_db))

    assert not errors, "official validator rejected pipeline output:\n" + "\n".join(
        errors
    )


# ==========================================================================
# ENTITY PREFIX GUARD (CLABE is internal and must never reach a submission)
# ==========================================================================

class _ClabeEstate:
    """Minimal stand-in for the two lookups _official_entity performs."""

    def __init__(self, vendors=None, employees=None):
        self._vendors = vendors or {}
        self._employees = employees or {}

    def find_vendor_by_clabe(self, clabe):
        return self._vendors.get(clabe)

    def find_employee_by_clabe(self, clabe):
        return self._employees.get(clabe)


def test_official_entity_passes_through_rfc_and_emp():
    estate = _ClabeEstate()

    assert _official_entity("RFC:AAA010101AA1", estate) == "RFC:AAA010101AA1"
    assert _official_entity("  EMP:0007  ", estate) == "EMP:0007"


def test_official_entity_resolves_a_clabe_to_its_owner():
    estate = _ClabeEstate(
        vendors={"012345678901234567": {"rfc": "PHA010101AA1"}},
        employees={"098765432109876543": {"emp_id": "EMP-0042"}},
    )

    assert (
        _official_entity("CLABE:012345678901234567", estate) == "RFC:PHA010101AA1"
    )
    assert _official_entity("CLABE:098765432109876543", estate) == "EMP:EMP-0042"


def test_official_entity_refuses_an_unowned_clabe_rather_than_leaking_it():
    """Ownership unknown in the estate means the account number must not be emitted."""

    assert _official_entity("CLABE:999999999999999999", _ClabeEstate()) is None


def test_official_entity_rejects_empty_payloads_and_unknown_prefixes():
    estate = _ClabeEstate()

    assert _official_entity("RFC:", estate) is None
    assert _official_entity("EMP:", estate) is None
    assert _official_entity("CLABE:", estate) is None
    assert _official_entity("just-a-name", estate) is None


def test_official_entity_does_not_double_an_already_prefixed_id():
    """A judge's estate may store the prefix: emp_id is documented as 'EMP:0001'.

    'EMP:EMP:0001' matches no entity in the answer key, so a correct finding
    would score as a missed scheme and an unattributed accusation at once.
    """

    estate = _ClabeEstate(
        vendors={"111111111111111111": {"rfc": "RFC:AAA010101AA1"}},
        employees={"222222222222222222": {"emp_id": "EMP:0001"}},
    )

    assert _official_entity("CLABE:111111111111111111", estate) == "RFC:AAA010101AA1"
    assert _official_entity("CLABE:222222222222222222", estate) == "EMP:0001"


# ==========================================================================
# LEADS NOT PURSUED: THE ANSWER A JUDGE READS OFF THE PAGE
# ==========================================================================

DECLINED_RFC = "LAT010101DD4"


@pytest.fixture
def estate_db_with_a_declined_vendor(tmp_path):
    """A listed vendor whose cited invoices all predate its EFOS publication.

    The listing is real, but it does not reach these invoices, so the gate
    declines and the vendor becomes a lead rather than a finding.
    """

    db_path = tmp_path / "estate_declined.db"
    _build_estate(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO efos_list (rfc, legal_name, status, publication_date) "
        f"VALUES ('{DECLINED_RFC}', 'Late Listing SA de CV', 'definitivo', "
        "'2025-11-04')"
    )
    conn.execute(
        "INSERT INTO vendors (rfc, legal_name, bank_clabe, category) "
        f"VALUES ('{DECLINED_RFC}', 'Late Listing SA de CV', "
        "'055555555555555555', 'manufactura')"
    )
    conn.execute(
        "INSERT INTO contracts (contract_id, vendor_rfc, start_date, value, scope_text) "
        f"VALUES ('CTR-2023-0001', '{DECLINED_RFC}', '2023-02-01', 900000.0, "
        "'Manufactura de subensambles metalmecanicos.')"
    )
    conn.execute(
        "INSERT INTO purchase_orders (po_id, vendor_rfc, date, amount, requester, "
        "approver, description) "
        f"VALUES ('PO-0001', '{DECLINED_RFC}', '2025-01-10', 150000.0, 'req', "
        "'apr', 'subensambles')"
    )
    conn.execute(
        "INSERT INTO invoices (uuid, issuer_rfc, receiver_rfc, total, issue_date) "
        f"VALUES ('INV-0700', '{DECLINED_RFC}', 'ACME010101AA1', '150000.00', "
        "'2025-01-20')"
    )
    conn.commit()
    conn.close()
    return db_path


def _declined_lead(estate_db, tmp_path):
    out_path = tmp_path / "declined.json"
    _run(estate_db, out_path)
    submission = json.loads(out_path.read_text(encoding="utf-8"))

    leads = [
        lead
        for lead in submission["leads_not_pursued"]
        if lead["entity"] == f"RFC:{DECLINED_RFC}"
    ]
    assert leads, submission["leads_not_pursued"]
    return leads[0], submission


def test_a_declined_lead_names_the_documents_that_were_read(
    estate_db_with_a_declined_vendor, tmp_path
):
    lead, _ = _declined_lead(estate_db_with_a_declined_vendor, tmp_path)

    assert lead["reason"].startswith(
        "Read 1 invoice(s), 1 purchase order(s), 1 contract(s) for "
        f"{DECLINED_RFC} (Late Listing SA de CV)."
    )
    assert (
        "Contract CTR-2023-0001 states: Manufactura de subensambles"
        in lead["reason"]
    )


def test_a_declined_lead_quotes_the_check_that_did_not_clear(
    estate_db_with_a_declined_vendor, tmp_path
):
    """Naming the check is not enough; the judge needs what the records showed."""

    lead, _ = _declined_lead(estate_db_with_a_declined_vendor, tmp_path)

    assert "Unmet critical check(s): PHANTOM-EFOS-INVOICE-LINK" in lead["reason"]
    # The documentary specifics the verifier computed, not a paraphrase.
    assert "definitivo" in lead["reason"]
    assert "2025-11-04" in lead["reason"]  # publication_date
    assert "2025-01-20" in lead["reason"]  # the invoice that predates it


def test_a_declined_lead_reports_the_reads_it_actually_made(
    estate_db_with_a_declined_vendor, tmp_path
):
    lead, _ = _declined_lead(estate_db_with_a_declined_vendor, tmp_path)

    for call in (
        "get_vendor",
        "get_vendor_contracts",
        "get_vendor_purchase_orders",
        "get_vendor_invoices",
    ):
        assert call in lead["tool_calls_made"], lead["tool_calls_made"]
    # No call is reported twice.
    assert len(lead["tool_calls_made"]) == len(set(lead["tool_calls_made"]))


def test_the_declined_vendor_is_not_accused(
    estate_db_with_a_declined_vendor, tmp_path
):
    lead, submission = _declined_lead(estate_db_with_a_declined_vendor, tmp_path)

    accused = {
        entity
        for finding in submission["findings"]
        for entity in finding["entities"]
    }
    assert f"RFC:{DECLINED_RFC}" not in accused
    assert lead["closed_by"] == "validator"
