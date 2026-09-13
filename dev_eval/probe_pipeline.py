"""Regression probe: run the production stack against the held-out estate.

    python dev_eval/probe_pipeline.py

DEV / EVAL ONLY.  This file imports ``src`` (an evaluation harness is allowed to);
``src`` must never import this, and never reads the answer key.

What it measures, in one command, so it can be re-run after every commit:

1. The six deterministic detectors, with every observation mapped onto the answer
   key: which schemes raise a lead, which decoys fire, how much unlabelled noise.
2. The deterministic post-verification path -- claim_builder -> OfficialVerifier
   -> EvidenceGate -> FindingBuilder -- driven for EVERY vendor whose RFC appears
   in ``efos_list``, not just a hand-picked pair.  Only the two LLM reviews are
   synthesised, exactly as ``tests/test_vertical_slice_smoke.py`` does.
3. Whether any authorised finding names an entity that is not in a planted
   scheme.  That is a false accusation, and it is what this probe exists to
   catch.

Exit code is 1 if a false accusation was authorised, or if a scheme that the
pipeline is expected to reach stopped raising a lead.  Everything else is
reported but does not fail the probe, because a missing verifier is a known gap
rather than a regression.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HERE = Path(__file__).resolve().parent
ESTATE = HERE / "robust_realism_estate.db"
ANSWERS = HERE / "robust_realism_estate_answers.db"
GROUND_TRUTH = HERE / "robust_realism_ground_truth.json"

# Schemes the current pipeline is expected to at least raise a lead for.  If one
# of these stops signalling, discovery has regressed.
EXPECTED_TO_SIGNAL = {
    "S1_phantom_vendor_1",
    "S2_kickback_1",
    "S4_threshold_splitting_1",
}

# Schemes that must still reach an authorised finding. One listed here and no
# longer authorising is a regression, not a judgement call.
EXPECTED_TO_AUTHORISE = {
    "S1_phantom_vendor_1",
    "S2_kickback_1",
}


def _hr(title: str) -> None:
    print()
    print("=" * 78)
    print("  " + title)
    print("=" * 78)


def load_answer_key() -> tuple[dict, dict, dict]:
    gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    company = "RFC:" + gt["company_rfc"]

    scheme_of: dict[str, list[str]] = {}
    for scheme in gt["schemes"]:
        for entity in scheme["entities"]:
            if entity == company:
                continue  # the audited company is in several schemes; not a label
            scheme_of.setdefault(entity, []).append(scheme["scheme_id"])

    conn = sqlite3.connect(ANSWERS)
    try:
        decoy_of = {entity: code for code, entity in conn.execute(
            "SELECT decoy_id, entity FROM answer_key_decoys")}
    finally:
        conn.close()
    return gt, scheme_of, decoy_of


def classify(entities, scheme_of, decoy_of, company) -> str:
    labels = []
    for entity in entities:
        if entity in scheme_of:
            labels.append("SCHEME " + ",".join(scheme_of[entity]))
        elif entity in decoy_of:
            labels.append("DECOY  " + decoy_of[entity])
        elif entity == company:
            labels.append("COMPANY")
    return "; ".join(labels) if labels else "unlabelled (honest)"


# ---------------------------------------------------------------- discovery ---

def run_discovery(gt, scheme_of, decoy_of):
    from src.core.estate import EstateRepository
    from src.detectors.deterministic_tabular import (
        run_deterministic_tabular_detectors)
    from src.detectors.deterministic_relational import (
        run_deterministic_relational_detectors)
    from src.investigation.lead_builder import build_leads
    from src.investigation.case_builder import build_case_state

    company = "RFC:" + gt["company_rfc"]
    started = time.perf_counter()
    repo = EstateRepository(ESTATE)
    observations = (run_deterministic_tabular_detectors(repo)
                    + run_deterministic_relational_detectors(repo))
    elapsed = time.perf_counter() - started

    _hr("DISCOVERY  (%d observations in %.2fs)" % (len(observations), elapsed))
    tally = Counter()
    for obs in sorted(observations, key=lambda o: o.observation_id):
        family = "/".join(obs.observation_id.split("-")[1:3])
        subject = obs.entities[0] if obs.entities else "?"
        label = classify(obs.entities, scheme_of, decoy_of, company)
        tally[label.split()[0]] += 1
        print("  %-16s %-26s %s" % (family, subject, label))
    print()
    for key, n in sorted(tally.items()):
        print("  %-12s %d" % (key, n))

    _hr("SCHEME COVERAGE")
    signalled = set()
    seen_entities = {e for o in observations for e in o.entities}
    missing_expected = []
    for scheme in gt["schemes"]:
        touched = [e for e in scheme["entities"]
                   if e != company and e in seen_entities]
        if touched:
            signalled.add(scheme["scheme_id"])
        status = "SIGNALLED" if touched else "NOT SEEN "
        print("  %-9s %-32s %-20s %s" % (status, scheme["scheme_id"],
                                         scheme["type"], touched or ""))
        if not touched and scheme["scheme_id"] in EXPECTED_TO_SIGNAL:
            missing_expected.append(scheme["scheme_id"])

    _hr("DECOY PRESSURE")
    fired = []
    for decoy in gt["decoys"]:
        hit = decoy["entity"] in seen_entities
        if hit:
            fired.append(decoy["entity"])
        print("  %-5s %-5s %-22s %s" % (decoy_of.get(decoy["entity"], "?"),
                                        "FIRES" if hit else "-",
                                        decoy["entity"], decoy["signal"]))

    leads = build_leads(observations)
    unofficial = [l for l in leads if l.subject_entities
                  and not l.subject_entities[0].startswith(("RFC:", "EMP:"))]
    malformed = sorted({e for o in observations for e in o.entities
                        if e.count(":") > 1 and not e.startswith("CLABE:")})

    case_errors = []
    for lead in leads:
        try:
            build_case_state(lead, observations)
        except Exception as exc:  # noqa: BLE001 - we are reporting, not handling
            case_errors.append((lead.lead_id, type(exc).__name__, str(exc)))

    _hr("LEADS")
    print("  %d leads, %d cases built, %d case errors"
          % (len(leads), len(leads) - len(case_errors), len(case_errors)))
    print("  leads whose subject is not an official entity id: %d"
          % len(unofficial))
    for lead in unofficial:
        print("     %s" % lead.subject_entities[0])
    print("  malformed entity ids emitted by detectors: %d" % len(malformed))
    for entity in malformed:
        print("     %s" % entity)

    repo.close()
    return {
        "observations": len(observations),
        "leads": len(leads),
        "unofficial_leads": len(unofficial),
        "malformed_entities": malformed,
        "case_errors": case_errors,
        "decoys_fired": fired,
        "missing_expected": missing_expected,
        "seconds": elapsed,
    }


# ------------------------------------------------------------ authorisation ---

def run_authorisation(gt, scheme_of, decoy_of, out_path: Path):
    """Try to authorise a phantom_vendor finding for every EFOS-matched vendor.

    Generalised on purpose: hand-picking two RFCs would stop measuring the moment
    the verifier changes.  Whatever the verifier decides, this asks it about
    every vendor the EFOS detector can reach.
    """
    from src.core.estate import EstateRepository
    from src.core.models import (CaseEvidence, CaseState, EvidenceRef,
                                 Hypothesis, InvestigationLoopResult)
    from src.agents.challenger import ChallengerReview
    from src.agents.method_critic import MethodCriticReview
    from src.investigation.preverification_pipeline import PreVerificationResult
    from src.investigation.review_orchestrator import (ReviewOrchestrationResult,
                                                       ReviewRound)
    from src.investigation.postverification_pipeline import (
        run_postverification_pipeline)
    from src.investigation.claim_builder import build_phantom_vendor_claim
    from src.output.finding_builder import build_finding
    from src.output.models import RunMetadata
    from src.output.submission_builder import build_submission, write_submission_json
    from src.rules.default_rules import (build_default_registry,
                                         default_rule_id_for_scheme)

    company = "RFC:" + gt["company_rfc"]
    repo = EstateRepository(ESTATE)
    registry = build_default_registry()
    rule_id = default_rule_id_for_scheme("phantom_vendor")

    conn = sqlite3.connect(ESTATE)
    targets = [r[0] for r in conn.execute(
        "SELECT DISTINCT e.rfc FROM efos_list e "
        "JOIN invoices i ON i.issuer_rfc = e.rfc ORDER BY e.rfc")]

    _hr("AUTHORISATION  (%d EFOS-matched vendors)" % len(targets))
    findings, false_accusations, authorised_schemes = [], [], []
    started = time.perf_counter()

    for rfc in targets:
        entity = "RFC:" + rfc
        truth = ("SCHEME " + ",".join(scheme_of[entity])) if entity in scheme_of \
            else ("DECOY " + decoy_of[entity]) if entity in decoy_of \
            else "unlabelled (honest)"
        invoices = [r[0] for r in conn.execute(
            "SELECT uuid FROM invoices WHERE issuer_rfc=? ORDER BY uuid", (rfc,))]

        evidence = [CaseEvidence(
            evidence_id="ev-efos",
            statement="RFC %s appears in the efos_list table supplied with the "
                      "estate." % rfc,
            direction="for",
            produced_by="deterministic_tabular.detect_efos_vendor_matches",
            source_refs=[EvidenceRef(source_table="efos_list", record_id=rfc,
                                     note="EFOS row for the issuer RFC.")])]
        for i, uuid_value in enumerate(invoices, start=1):
            evidence.append(CaseEvidence(
                evidence_id="ev-inv-%03d" % i,
                statement="Invoice %s was issued by %s to the audited company."
                          % (uuid_value, rfc),
                direction="for", produced_by="estate.get_vendor_invoices",
                source_refs=[EvidenceRef(source_table="invoices",
                                         record_id=uuid_value,
                                         note="CFDI issued by the listed RFC.")]))

        target = "probe-%s-H1" % rfc
        hypothesis = Hypothesis(
            hypothesis_id=target,
            statement="The issuer RFC %s appears in efos_list and issued CFDI to "
                      "the audited company." % rfc,
            scheme_type="phantom_vendor", status="supported",
            supporting_evidence_ids=[e.evidence_id for e in evidence])
        case = CaseState(case_id="probe-%s" % rfc, lead_id="probe-%s-L" % rfc,
                         status="ready_for_verification",
                         hypotheses=[hypothesis], evidence=evidence)

        review = ReviewOrchestrationResult(
            case_state=case, target_hypothesis_id=target,
            rounds=[ReviewRound(
                round_number=1, target_hypothesis_id=target,
                challenger_review=ChallengerReview(
                    target_hypothesis_id=target, outcome="survives",
                    reasoning_summary="Synthesised: this probe measures the "
                                      "deterministic path, not the agents.",
                    survives_challenge=True),
                method_critic_review=MethodCriticReview(
                    target_hypothesis_id=target, outcome="clear",
                    reasoning_summary="Synthesised for the same reason."))],
            review_rounds=1, ready_for_verification=True,
            stop_reason="ready_for_verification")
        pre = PreVerificationResult(
            case_state=case, target_hypothesis_id=target,
            ready_for_verification=True, stop_reason="ready_for_verification",
            initial_investigation=InvestigationLoopResult(
                case_state=case, decisions=[], iterations=0,
                stop_reason="ready_for_verification"),
            review_result=review)

        claim = build_phantom_vendor_claim(case, target, repo)
        post = run_postverification_pipeline(
            preverification_result=pre, estate=repo, rule_registry=registry,
            claimed_amount=claim.claimed_amount, requested_rule_id=rule_id)
        outcome = post.gate_decision.outcome

        blocking = [c.check_id for c in post.verification_report.checks
                    if c.critical and c.status != "verified"]
        print("  %-14s %-34s %-20s %s"
              % (rfc, truth, outcome,
                 ("blocked by " + ", ".join(blocking)) if blocking else ""))

        if outcome != "authorize_probable":
            continue
        finding = build_finding(
            case_state=post.preverification_result.case_state,
            target_hypothesis_id=post.target_hypothesis_id,
            gate_decision=post.gate_decision,
            verification_report=post.verification_report,
            rule_registry=registry, requested_rule_id=post.requested_rule_id,
            claimed_amount=post.claimed_amount, estate=repo)
        findings.append(finding)
        if entity in scheme_of:
            authorised_schemes.extend(scheme_of[entity])
        else:
            false_accusations.append((entity, truth, finding.peso_amount))

    elapsed = time.perf_counter() - started
    conn.close()

    metadata = RunMetadata(llm_calls=0, mxn_cost=0.0,
                           wall_clock_seconds=round(elapsed, 3),
                           deterministic=True)
    submission = build_submission(findings=findings, leads_not_pursued=[],
                                  run_metadata=metadata, seed=gt["seed"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_submission_json(submission, out_path)
    repo.close()

    return {
        "findings": len(findings),
        "authorised_schemes": sorted(set(authorised_schemes)),
        "false_accusations": false_accusations,
        "submission": out_path,
    }



def run_kickback_authorisation(gt, scheme_of, decoy_of, estate, registry):
    """Ask the kickback path about every vendor-to-employee transfer pair.

    The phantom stage enumerates EFOS-matched vendors; this one enumerates the
    other verifiable scheme, so the regression gate covers both instead of one.
    Hand-picking the planted pair would stop measuring the moment the verifier
    changes shape.
    """
    from src.core.models import (CaseEvidence, CaseState, EvidenceRef,
                                 Hypothesis, InvestigationLoopResult)
    from src.agents.challenger import ChallengerReview
    from src.agents.method_critic import MethodCriticReview
    from src.investigation.preverification_pipeline import PreVerificationResult
    from src.investigation.review_orchestrator import (ReviewOrchestrationResult,
                                                       ReviewRound)
    from src.investigation.postverification_pipeline import (
        run_postverification_pipeline)
    from src.output.finding_builder import build_finding
    from src.rules.default_rules import default_rule_id_for_scheme

    rule_id = default_rule_id_for_scheme("kickback")
    conn = sqlite3.connect(ESTATE)
    pairs = conn.execute(
        "SELECT DISTINCT v.rfc, e.emp_id FROM bank_txns b "
        "JOIN vendors v ON v.bank_clabe = b.from_clabe "
        "JOIN employees e ON e.bank_clabe = b.to_clabe "
        "ORDER BY v.rfc, e.emp_id").fetchall()

    _hr("KICKBACK AUTHORISATION  (%d vendor/employee pair(s))" % len(pairs))
    findings, false_accusations, authorised = [], [], []

    for rfc, emp in pairs:
        entity = "RFC:" + rfc
        if entity in scheme_of:
            truth = "SCHEME " + ",".join(scheme_of[entity])
        elif entity in decoy_of:
            truth = "DECOY " + decoy_of[entity]
        else:
            truth = "unlabelled (honest)"

        txns = [r[0] for r in conn.execute(
            "SELECT b.txn_id FROM bank_txns b "
            "JOIN vendors v ON v.bank_clabe = b.from_clabe "
            "JOIN employees e ON e.bank_clabe = b.to_clabe "
            "WHERE v.rfc = ? AND e.emp_id = ? ORDER BY b.txn_id", (rfc, emp))]
        if not txns:
            continue
        amount = sum(r[0] or 0 for r in conn.execute(
            "SELECT amount FROM bank_txns WHERE txn_id IN (%s)"
            % ",".join("?" * len(txns)), txns))
        if amount <= 0:
            continue
        pos = [r[0] for r in conn.execute(
            "SELECT po_id FROM purchase_orders WHERE vendor_rfc = ? ORDER BY po_id",
            (rfc,))]

        evidence = [
            CaseEvidence(evidence_id="ev-v", statement="Vendor bank account.",
                         direction="for", produced_by="probe",
                         source_refs=[EvidenceRef(source_table="vendors",
                                                  record_id=rfc,
                                                  note="Vendor bank account.")]),
            CaseEvidence(evidence_id="ev-e", statement="Employee bank account.",
                         direction="for", produced_by="probe",
                         source_refs=[EvidenceRef(source_table="employees",
                                                  record_id=emp,
                                                  note="Employee bank account.")]),
        ]
        for i, txn in enumerate(txns, start=1):
            evidence.append(CaseEvidence(
                evidence_id="ev-t%03d" % i, statement="Transfer.",
                direction="for", produced_by="probe",
                source_refs=[EvidenceRef(source_table="bank_txns", record_id=txn,
                                         note="Vendor to employee transfer.")]))
        for i, po in enumerate(pos, start=1):
            evidence.append(CaseEvidence(
                evidence_id="ev-p%03d" % i, statement="Purchase order.",
                direction="for", produced_by="probe",
                source_refs=[EvidenceRef(source_table="purchase_orders",
                                         record_id=po,
                                         note="Purchase order for that vendor.")]))

        target = "probe-k-%s-H1" % rfc
        case = CaseState(
            case_id="probe-k-%s" % rfc, lead_id="probe-k-%s-L" % rfc,
            status="ready_for_verification",
            hypotheses=[Hypothesis(
                hypothesis_id=target,
                statement="Value moved from vendor %s to employee %s." % (rfc, emp),
                scheme_type="kickback", status="supported",
                supporting_evidence_ids=[e.evidence_id for e in evidence])],
            evidence=evidence)
        review = ReviewOrchestrationResult(
            case_state=case, target_hypothesis_id=target,
            rounds=[ReviewRound(
                round_number=1, target_hypothesis_id=target,
                challenger_review=ChallengerReview(
                    target_hypothesis_id=target, outcome="survives",
                    reasoning_summary="Deterministic probe.",
                    survives_challenge=True),
                method_critic_review=MethodCriticReview(
                    target_hypothesis_id=target, outcome="clear",
                    reasoning_summary="Deterministic probe."))],
            review_rounds=1, ready_for_verification=True,
            stop_reason="ready_for_verification")
        pre = PreVerificationResult(
            case_state=case, target_hypothesis_id=target,
            ready_for_verification=True, stop_reason="ready_for_verification",
            initial_investigation=InvestigationLoopResult(
                case_state=case, decisions=[], iterations=0,
                stop_reason="ready_for_verification"),
            review_result=review)

        post = run_postverification_pipeline(
            preverification_result=pre, estate=estate, rule_registry=registry,
            claimed_amount=round(amount, 2), requested_rule_id=rule_id)
        outcome = post.gate_decision.outcome
        blocking = [c.check_id for c in post.verification_report.checks
                    if c.critical and c.status != "verified"]
        print("  %-14s %-34s %-20s %s"
              % (rfc, truth, outcome,
                 ("blocked by " + ", ".join(blocking)) if blocking else ""))

        if outcome != "authorize_probable":
            continue
        finding = build_finding(
            case_state=post.preverification_result.case_state,
            target_hypothesis_id=post.target_hypothesis_id,
            gate_decision=post.gate_decision,
            verification_report=post.verification_report,
            rule_registry=registry, requested_rule_id=post.requested_rule_id,
            claimed_amount=post.claimed_amount, estate=estate)
        findings.append(finding)
        if entity in scheme_of:
            authorised.extend(scheme_of[entity])
        else:
            false_accusations.append((entity, truth, finding.peso_amount))
    conn.close()
    return {"findings": len(findings),
            "authorised_schemes": sorted(set(authorised)),
            "false_accusations": false_accusations}


# ------------------------------------------------------------------- main ---

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",
                        default=str(REPO_ROOT / "runs" / "robust_realism"
                                    / "submission.json"))
    args = parser.parse_args(argv)

    if not ESTATE.exists():
        print("estate missing; run python dev_eval/generate_robust_realism_estate.py")
        return 2

    gt, scheme_of, decoy_of = load_answer_key()
    discovery = run_discovery(gt, scheme_of, decoy_of)
    authorisation = run_authorisation(gt, scheme_of, decoy_of, Path(args.out))

    from src.core.estate import EstateRepository
    from src.rules.default_rules import build_default_registry
    estate = EstateRepository(ESTATE)
    try:
        kickback = run_kickback_authorisation(gt, scheme_of, decoy_of, estate,
                                              build_default_registry())
    finally:
        estate.close()

    _hr("VERDICT")
    print("  observations %d   leads %d   findings %d (%d phantom, %d kickback)"
          "   %.2fs"
          % (discovery["observations"], discovery["leads"],
             authorisation["findings"] + kickback["findings"],
             authorisation["findings"], kickback["findings"],
             discovery["seconds"]))
    all_authorised = sorted(set(authorisation["authorised_schemes"])
                            | set(kickback["authorised_schemes"]))
    print("  schemes authorised : %s" % (", ".join(all_authorised) or "none"))
    print("  submission         : %s" % authorisation["submission"])

    failures = []
    for entity, truth, amount in (authorisation["false_accusations"]
                                  + kickback["false_accusations"]):
        failures.append("FALSE ACCUSATION  %s  %s  MXN %s"
                        % (entity, truth, "{:,.2f}".format(amount)))
    for scheme_id in discovery["missing_expected"]:
        failures.append("DISCOVERY REGRESSION  %s no longer raises a lead"
                        % scheme_id)
    for scheme_id in sorted(EXPECTED_TO_AUTHORISE - set(all_authorised)):
        failures.append("AUTHORISATION REGRESSION  %s no longer reaches a finding"
                        % scheme_id)
    for lead_id, exc_type, message in discovery["case_errors"]:
        failures.append("CASE BUILD ERROR  %s  %s: %s"
                        % (lead_id, exc_type, message))

    if discovery["malformed_entities"]:
        print("  WARNING: malformed entity ids still emitted: %s"
              % ", ".join(discovery["malformed_entities"]))
    if discovery["unofficial_leads"]:
        print("  WARNING: %d lead(s) still keyed on a CLABE subject"
              % discovery["unofficial_leads"])

    print()
    if failures:
        for line in failures:
            print("  FAIL  " + line)
        return 1
    print("  PASS  no false accusation authorised, no discovery regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
