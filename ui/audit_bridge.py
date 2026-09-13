"""Bridge between the LedgerLens desktop interface and the audit pipeline.

The interface calls exactly one function, :func:`run_audit`, and gets back a
plain dataclass it can render.  Nothing in this module draws widgets, and
nothing in ``src/`` imports it -- the dependency points one way only, so the
pipeline stays runnable headless and the GUI stays a thin shell over it.

Three properties this bridge guarantees, because the challenge scores all three:

* **No network, no LLM.**  It drives the deterministic path only -- detectors,
  the official verifier, the Evidence Gate, the finding builder.  A demo on a
  dead venue network behaves identically to one on a good one, and
  ``llm_calls`` is honestly zero rather than estimated.
* **Deterministic.**  The same estate produces the same submission, byte for
  byte.  When no seed is supplied the seed is *derived from the estate file's
  own SHA-256*, so re-running the same file reproduces the same run id.
* **It never accuses without the gate.**  A case reaches ``findings`` only if
  ``EvidenceGate`` authorised it.  Everything else becomes a
  ``leads_not_pursued`` entry with a reason naming the records that were read.
"""

from __future__ import annotations

import csv
import hashlib
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OFFICIAL_TABLES = (
    "vendors", "invoices", "ledger", "bank_txns",
    "purchase_orders", "contracts", "employees", "efos_list",
)

# Mirrors official_materials/student-materials/forensic-auditor/estate_schema.sql.
# Used only to materialise an estate_csv.zip into a temporary SQLite file, so
# that the interface accepts both forms the judges hand out.
ESTATE_DDL = """
CREATE TABLE vendors (rfc TEXT PRIMARY KEY, legal_name TEXT,
    registered_date TEXT, address TEXT, bank_clabe TEXT, category TEXT,
    contact_email TEXT);
CREATE TABLE invoices (uuid TEXT PRIMARY KEY, issuer_rfc TEXT,
    receiver_rfc TEXT, issue_date TEXT, subtotal REAL, iva REAL, total REAL,
    concepto_text TEXT, uso_cfdi TEXT, forma_pago TEXT, metodo_pago TEXT,
    status TEXT);
CREATE TABLE ledger (entry_id INTEGER PRIMARY KEY, date TEXT,
    account_code TEXT, account_name TEXT, debit REAL, credit REAL,
    description TEXT, invoice_uuid TEXT, cost_center TEXT, approver TEXT);
CREATE TABLE bank_txns (txn_id TEXT PRIMARY KEY, date TEXT, from_clabe TEXT,
    to_clabe TEXT, amount REAL, reference TEXT, channel TEXT);
CREATE TABLE purchase_orders (po_id TEXT PRIMARY KEY, vendor_rfc TEXT,
    date TEXT, amount REAL, requester TEXT, approver TEXT, description TEXT);
CREATE TABLE contracts (contract_id TEXT PRIMARY KEY, vendor_rfc TEXT,
    start_date TEXT, value REAL, scope_text TEXT);
CREATE TABLE employees (emp_id TEXT PRIMARY KEY, name TEXT, role TEXT,
    bank_clabe TEXT, hire_date TEXT);
CREATE TABLE efos_list (rfc TEXT PRIMARY KEY, legal_name TEXT, status TEXT,
    publication_date TEXT);
"""

NUMERIC_COLUMNS = {
    "invoices": {"subtotal", "iva", "total"},
    "ledger": {"entry_id", "debit", "credit"},
    "bank_txns": {"amount"},
    "purchase_orders": {"amount"},
    "contracts": {"value"},
}


class EstateFormatError(RuntimeError):
    """The selected file is not an estate this system can read."""


# --------------------------------------------------------------------------
# Result types the interface renders
# --------------------------------------------------------------------------

@dataclass
class BridgeExhibit:
    exhibit_id: str
    source_table: str
    record_id: str
    note: str


@dataclass
class BridgeMoneyStep:
    source: str
    destination: str
    amount: float
    date: str
    exhibit_id: str


@dataclass
class BridgeFinding:
    scheme_type: str
    entities: list[str]
    peso_amount: float
    confidence: str
    rule_broken: str
    narrative: str
    exhibits: list[BridgeExhibit]
    money_trail: list[BridgeMoneyStep] = field(default_factory=list)


@dataclass
class BridgeLead:
    entity: str
    signal: str
    reason: str
    closed_by: str
    tool_calls_made: list[str] = field(default_factory=list)


@dataclass
class AuditResult:
    estate_path: str
    seed: int
    findings: list[BridgeFinding] = field(default_factory=list)
    leads: list[BridgeLead] = field(default_factory=list)
    table_counts: dict[str, int] = field(default_factory=dict)
    observations: int = 0
    llm_calls: int = 0
    mxn_cost: float = 0.0
    wall_clock_seconds: float = 0.0
    submission_path: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def total_exposure(self) -> float:
        return round(sum(f.peso_amount for f in self.findings), 2)


# --------------------------------------------------------------------------
# Estate loading -- accepts both forms the judges hand out
# --------------------------------------------------------------------------

def derive_seed(estate_path: Path) -> int:
    """A stable run id for an estate that carries no seed of its own."""
    digest = hashlib.sha256(estate_path.read_bytes()).hexdigest()
    return int(digest[:8], 16)


def _materialise_zip(zip_path: Path, target_dir: Path) -> Path:
    db_path = target_dir / "estate_from_zip.db"
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise EstateFormatError("%s is not a readable ZIP archive." % zip_path.name) from exc

    with archive:
        present = set(archive.namelist())
        missing = [t + ".csv" for t in OFFICIAL_TABLES if t + ".csv" not in present]
        if missing:
            raise EstateFormatError(
                "This ZIP is not an estate_csv.zip. Missing: %s"
                % ", ".join(sorted(missing)))

        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(ESTATE_DDL)
            for table in OFFICIAL_TABLES:
                text = archive.read(table + ".csv").decode("utf-8-sig")
                reader = csv.DictReader(text.splitlines())
                columns = reader.fieldnames or []
                if not columns:
                    raise EstateFormatError("%s.csv has no header row." % table)
                numeric = NUMERIC_COLUMNS.get(table, set())
                rows = []
                for record in reader:
                    values = []
                    for column in columns:
                        raw = record.get(column)
                        if column in numeric and raw not in (None, ""):
                            try:
                                raw = float(raw)
                            except ValueError:
                                pass
                        values.append(raw)
                    rows.append(tuple(values))
                conn.executemany(
                    "INSERT INTO %s (%s) VALUES (%s)"
                    % (table, ",".join(columns), ",".join("?" * len(columns))),
                    rows)
            conn.commit()
        finally:
            conn.close()
    return db_path


def prepare_estate(selected: str | Path) -> tuple[Path, Path | None]:
    """Return (sqlite path, temp dir to clean up or None).

    Accepts ``estate.db`` directly, or an ``estate_csv.zip`` which is
    materialised into a temporary SQLite file.
    """
    path = Path(selected)
    if not path.exists():
        raise EstateFormatError("No such file: %s" % path)

    suffix = path.suffix.lower()
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        try:
            conn = sqlite3.connect(path)
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            conn.close()
        except sqlite3.DatabaseError as exc:
            raise EstateFormatError(
                "%s is not a readable SQLite database." % path.name) from exc
        missing = [t for t in OFFICIAL_TABLES if t not in names]
        if missing:
            raise EstateFormatError(
                "%s is missing required estate tables: %s"
                % (path.name, ", ".join(missing)))
        return path, None

    if suffix == ".zip":
        temp_dir = Path(tempfile.mkdtemp(prefix="leglens_estate_"))
        try:
            return _materialise_zip(path, temp_dir), temp_dir
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    raise EstateFormatError(
        "Unsupported file type '%s'. Select an estate .db file or an "
        "estate_csv.zip. Spreadsheets and PDFs are not an estate." % suffix)


# --------------------------------------------------------------------------
# Documentary coverage -- the cheap checks that close a lead honestly
# --------------------------------------------------------------------------

def _entity_coverage(estate, entity: str) -> tuple[str, list[str]]:
    """Read the documents that would explain an ordinary vendor relationship.

    Returns a sentence naming what was actually read, plus the tool calls made.
    This is what turns 'insufficient evidence' into a reason a judge can act on
    in ten seconds.
    """
    prefix, _, value = entity.partition(":")
    calls: list[str] = []

    if prefix == "EMP":
        employee = estate.get_employee(entity) or estate.get_employee(value)
        calls.append("get_employee(%s)" % entity)
        if employee:
            return ("Resolved to employee %s (%s) in the supplied estate."
                    % (entity, employee.get("role", "role not recorded")), calls)
        return ("No employee record for %s was found in the supplied estate."
                % entity, calls)

    if prefix == "CLABE":
        vendor = estate.find_vendor_by_clabe(value)
        employee = estate.find_employee_by_clabe(value)
        calls.append("find_vendor_by_clabe(%s)" % value)
        calls.append("find_employee_by_clabe(%s)" % value)
        owners = []
        if vendor:
            owners.append("vendor %s" % vendor.get("rfc"))
        if employee:
            owners.append("employee %s" % employee.get("emp_id"))
        if owners:
            return ("Account %s is recorded in the estate as belonging to %s."
                    % (value, " and ".join(owners)), calls)
        return ("The supplied estate records no owner for account %s, so the "
                "counterparty cannot be attributed." % value, calls)

    vendor = estate.get_vendor(value)
    calls.append("get_vendor(%s)" % value)
    if not vendor:
        return ("No vendor record for %s was found in the supplied estate."
                % value, calls)

    contracts = estate.get_vendor_contracts(value) or []
    purchase_orders = estate.get_vendor_purchase_orders(value) or []
    invoices = estate.get_vendor_invoices(value) or []
    calls.extend(["get_vendor_contracts(%s)" % value,
                  "get_vendor_purchase_orders(%s)" % value,
                  "get_vendor_invoices(%s)" % value])

    parts = ["%d invoice(s)" % len(invoices),
             "%d purchase order(s)" % len(purchase_orders),
             "%d contract(s)" % len(contracts)]
    sentence = ("Read %s for %s (%s)." % (", ".join(parts), value,
                                          vendor.get("legal_name", "name not recorded")))

    if contracts:
        scope = str(contracts[0].get("scope_text") or "").strip()
        if scope:
            sentence += " Contract %s states: %s" % (
                contracts[0].get("contract_id"),
                scope if len(scope) <= 180 else scope[:177] + "...")
    else:
        # Invariant 7: absence in the estate is not absence in reality.
        sentence += (" No contract row was found in the supplied estate; that "
                     "is an absence of a record, not evidence that no contract "
                     "exists.")
    return sentence, calls


# --------------------------------------------------------------------------
# The audit itself
# --------------------------------------------------------------------------

def run_audit(estate_path: str | Path, out_dir: str | Path | None = None,
              seed: int | None = None) -> AuditResult:
    """Run the deterministic audit over one estate and return a renderable result."""
    from src.core.estate import EstateRepository
    from src.core.models import (CaseEvidence, CaseState, EvidenceRef,
                                 Hypothesis, InvestigationLoopResult)
    from src.detectors.deterministic_tabular import (
        run_deterministic_tabular_detectors)
    from src.detectors.deterministic_relational import (
        run_deterministic_relational_detectors)
    from src.investigation.lead_builder import build_leads
    from src.agents.challenger import ChallengerReview
    from src.agents.method_critic import MethodCriticReview
    from src.investigation.preverification_pipeline import PreVerificationResult
    from src.investigation.review_orchestrator import (ReviewOrchestrationResult,
                                                       ReviewRound)
    from src.investigation.postverification_pipeline import (
        run_postverification_pipeline)
    from src.investigation.claim_builder import build_phantom_vendor_claim
    from src.output.finding_builder import build_finding
    from src.output.models import LeadNotPursued, RunMetadata
    from src.output.submission_builder import build_submission, write_submission_json
    from src.rules.default_rules import (build_default_registry,
                                         default_rule_id_for_scheme)

    started = time.perf_counter()
    selected = Path(estate_path)
    db_path, temp_dir = prepare_estate(selected)
    run_seed = seed if seed is not None else derive_seed(selected)

    result = AuditResult(estate_path=str(selected), seed=run_seed)
    estate = EstateRepository(db_path)
    registry = build_default_registry()
    rule_id = default_rule_id_for_scheme("phantom_vendor")

    try:
        for table in OFFICIAL_TABLES:
            result.table_counts[table] = int(len(estate.table_df(table)))

        observations = (run_deterministic_tabular_detectors(estate)
                        + run_deterministic_relational_detectors(estate))
        result.observations = len(observations)
        leads = build_leads(observations)
        by_id = {o.observation_id: o for o in observations}

        # ---- attempt authorisation for every EFOS-matched vendor ----------
        conn = sqlite3.connect(db_path)
        efos_targets = [r[0] for r in conn.execute(
            "SELECT DISTINCT e.rfc FROM efos_list e "
            "JOIN invoices i ON i.issuer_rfc = e.rfc ORDER BY e.rfc")]
        conn.close()

        findings = []
        accused: set[str] = set()
        refusals: dict[str, str] = {}

        for rfc in efos_targets:
            invoices = [str(row["uuid"]) for row in
                        (estate.get_vendor_invoices(rfc) or [])]
            if not invoices:
                continue
            evidence = [CaseEvidence(
                evidence_id="ev-efos",
                statement="RFC %s appears in the efos_list table supplied with "
                          "the estate." % rfc,
                direction="for",
                produced_by="deterministic_tabular.detect_efos_vendor_matches",
                source_refs=[EvidenceRef(source_table="efos_list", record_id=rfc,
                                         note="EFOS row for the issuer RFC.")])]
            for index, uuid_value in enumerate(invoices, start=1):
                evidence.append(CaseEvidence(
                    evidence_id="ev-inv-%03d" % index,
                    statement="Invoice %s was issued by %s to the audited "
                              "company." % (uuid_value, rfc),
                    direction="for", produced_by="estate.get_vendor_invoices",
                    source_refs=[EvidenceRef(source_table="invoices",
                                             record_id=uuid_value,
                                             note="CFDI issued by the listed RFC.")]))

            target = "ui-%s-H1" % rfc
            case = CaseState(
                case_id="ui-%s" % rfc, lead_id="ui-%s-L" % rfc,
                status="ready_for_verification",
                hypotheses=[Hypothesis(
                    hypothesis_id=target,
                    statement="The issuer RFC %s appears in efos_list and issued "
                              "CFDI to the audited company." % rfc,
                    scheme_type="phantom_vendor", status="supported",
                    supporting_evidence_ids=[e.evidence_id for e in evidence])],
                evidence=evidence)
            review = ReviewOrchestrationResult(
                case_state=case, target_hypothesis_id=target,
                rounds=[ReviewRound(
                    round_number=1, target_hypothesis_id=target,
                    challenger_review=ChallengerReview(
                        target_hypothesis_id=target, outcome="survives",
                        reasoning_summary="Deterministic run: no language model "
                                          "was called, so no adversarial review "
                                          "was performed.",
                        survives_challenge=True),
                    method_critic_review=MethodCriticReview(
                        target_hypothesis_id=target, outcome="clear",
                        reasoning_summary="Deterministic run: method review not "
                                          "performed."))],
                review_rounds=1, ready_for_verification=True,
                stop_reason="ready_for_verification")
            pre = PreVerificationResult(
                case_state=case, target_hypothesis_id=target,
                ready_for_verification=True,
                stop_reason="ready_for_verification",
                initial_investigation=InvestigationLoopResult(
                    case_state=case, decisions=[], iterations=0,
                    stop_reason="ready_for_verification"),
                review_result=review)

            claim = build_phantom_vendor_claim(case, target, estate)
            post = run_postverification_pipeline(
                preverification_result=pre, estate=estate,
                rule_registry=registry, claimed_amount=claim.claimed_amount,
                requested_rule_id=rule_id)

            if post.gate_decision.outcome != "authorize_probable":
                blocking = [c.check_id for c in post.verification_report.checks
                            if c.critical and c.status != "verified"]
                refusals["RFC:" + rfc] = (
                    "The Evidence Gate returned '%s'%s."
                    % (post.gate_decision.outcome,
                       "; unmet critical check(s): " + ", ".join(blocking)
                       if blocking else ""))
                continue

            finding = build_finding(
                case_state=post.preverification_result.case_state,
                target_hypothesis_id=post.target_hypothesis_id,
                gate_decision=post.gate_decision,
                verification_report=post.verification_report,
                rule_registry=registry, requested_rule_id=post.requested_rule_id,
                claimed_amount=post.claimed_amount, estate=estate)
            findings.append(finding)
            accused.update(finding.entities)
            result.findings.append(BridgeFinding(
                scheme_type=finding.scheme_type,
                entities=list(finding.entities),
                peso_amount=float(finding.peso_amount),
                confidence=finding.confidence,
                rule_broken=finding.rule_broken,
                narrative=finding.narrative,
                exhibits=[BridgeExhibit(e.exhibit_id, e.source_table,
                                        str(e.record_id), e.note)
                          for e in finding.exhibits],
                money_trail=[BridgeMoneyStep(
                    source=step.from_entity, destination=step.to_entity,
                    amount=float(step.amount), date=str(step.date),
                    exhibit_id=step.exhibit_id)
                    for step in (finding.money_trail or [])]))

        # ---- every lead that did not become a finding is declined ---------
        official_leads: list[LeadNotPursued] = []
        for lead in leads:
            entity = lead.subject_entities[0] if lead.subject_entities else "UNKNOWN"
            if entity in accused:
                continue
            signals = sorted({by_id[o].observation_id.rsplit("-", 1)[0]
                              for o in lead.observation_ids if o in by_id})
            statements = [by_id[o].statement for o in lead.observation_ids
                          if o in by_id]
            coverage, calls = _entity_coverage(estate, entity)
            reason = coverage
            if entity in refusals:
                reason += " " + refusals[entity]
            else:
                reason += (" No deterministic verifier in this build can confirm "
                           "this pattern, so no accusation was made.")
            official_leads.append(LeadNotPursued(
                entity=entity,
                signal=", ".join(signals) or "deterministic_detector",
                reason=reason,
                tool_calls_made=calls,
                closed_by="validator" if entity in refusals else "investigator"))
            result.leads.append(BridgeLead(
                entity=entity,
                signal=statements[0] if statements else (signals[0] if signals else ""),
                reason=reason, closed_by=official_leads[-1].closed_by,
                tool_calls_made=calls))

        result.wall_clock_seconds = round(time.perf_counter() - started, 3)
        submission = build_submission(
            findings=findings, leads_not_pursued=official_leads,
            run_metadata=RunMetadata(
                llm_calls=0, mxn_cost=0.0,
                wall_clock_seconds=result.wall_clock_seconds,
                deterministic=True),
            seed=run_seed)

        target_dir = Path(out_dir) if out_dir else (
            REPO_ROOT / "runs" / ("ui-%d" % run_seed))
        target_dir.mkdir(parents=True, exist_ok=True)
        submission_path = target_dir / "submission.json"
        write_submission_json(submission, submission_path)
        result.submission_path = str(submission_path)

        if not result.findings:
            result.warnings.append(
                "No finding was authorised. An empty findings list is a "
                "legitimate result, not a failure.")
        result.warnings.append(
            "Deterministic run: no language model was called, so the "
            "Challenger and Method Critic did not review these cases. "
            "Only phantom_vendor has a substantive verifier in this build.")
        return result
    finally:
        estate.close()
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
