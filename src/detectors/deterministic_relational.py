"""
Deterministic relational discovery for the Forensic Auditor.

These detectors surface auditable cross-table relationships and short-window
transaction patterns. They deliberately stop at observable facts/patterns:
none of the signals below proves a fraud scheme by itself.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pandas as pd

from src.core.estate import EstateRepository
from src.core.models import EvidenceRef, Observation
from src.graph.bank_graph import build_bank_multidigraph, find_directed_bank_cycles
from src.output.formatters import with_entity_prefix


def _clean_text(value) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _observation_id(prefix: str, *parts: str) -> str:
    raw = "|".join(parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()
    return f"OBS-REL-{prefix}-{digest}"


def detect_vendor_to_employee_transfers(
    vendors: pd.DataFrame,
    employees: pd.DataFrame,
    bank_txns: pd.DataFrame,
) -> list[Observation]:
    """Detect direct transfers from a recorded vendor CLABE to an employee CLABE.

    This establishes transfer direction and the account-to-entity associations
    present in the supplied estate. It does not establish quid pro quo,
    beneficial ownership, or a kickback by itself.
    """

    if not {"rfc", "bank_clabe"}.issubset(vendors.columns):
        return []
    if not {"emp_id", "bank_clabe"}.issubset(employees.columns):
        return []
    required_txn = {"txn_id", "from_clabe", "to_clabe", "amount"}
    if not required_txn.issubset(bank_txns.columns):
        return []

    vendor_rows = vendors[["rfc", "bank_clabe"]].copy()
    employee_rows = employees[["emp_id", "bank_clabe"]].copy()
    txns = bank_txns.copy()

    vendor_rows["rfc"] = vendor_rows["rfc"].map(_clean_text)
    vendor_rows["_clabe"] = vendor_rows["bank_clabe"].map(_clean_text)
    employee_rows["emp_id"] = employee_rows["emp_id"].map(_clean_text)
    employee_rows["_clabe"] = employee_rows["bank_clabe"].map(_clean_text)

    vendor_rows = vendor_rows.dropna(subset=["rfc", "_clabe"]).drop_duplicates()
    employee_rows = employee_rows.dropna(subset=["emp_id", "_clabe"]).drop_duplicates()

    txns["txn_id"] = txns["txn_id"].map(_clean_text)
    txns["_from"] = txns["from_clabe"].map(_clean_text)
    txns["_to"] = txns["to_clabe"].map(_clean_text)
    txns["_amount"] = pd.to_numeric(txns["amount"], errors="coerce")
    txns = txns.dropna(subset=["txn_id", "_from", "_to", "_amount"])

    vendors_by_clabe: dict[str, list[str]] = {}
    for clabe, group in vendor_rows.groupby("_clabe"):
        vendors_by_clabe[clabe] = sorted(set(group["rfc"].tolist()))

    employees_by_clabe: dict[str, list[str]] = {}
    for clabe, group in employee_rows.groupby("_clabe"):
        employees_by_clabe[clabe] = sorted(set(group["emp_id"].tolist()))

    observations: list[Observation] = []

    for _, txn in txns.sort_values(by=["txn_id"]).iterrows():
        from_clabe = txn["_from"]
        to_clabe = txn["_to"]
        if from_clabe not in vendors_by_clabe or to_clabe not in employees_by_clabe:
            continue

        for rfc in vendors_by_clabe[from_clabe]:
            for emp_id in employees_by_clabe[to_clabe]:
                txn_id = txn["txn_id"]
                amount = float(txn["_amount"])
                txn_date = _clean_text(txn.get("date"))

                observations.append(
                    Observation(
                        observation_id=_observation_id(
                            "VENDOR-TO-EMPLOYEE",
                            rfc,
                            emp_id,
                            txn_id,
                        ),
                        detector_name="deterministic_relational",
                        signal_type="vendor_to_employee_bank_transfer",
                        entities=[
                            f"RFC:{rfc}",
                            # An estate may already store emp_id as "EMP:0001".
                            with_entity_prefix("EMP:", emp_id),
                            f"CLABE:{from_clabe}",
                            f"CLABE:{to_clabe}",
                        ],
                        statement=(
                            "The supplied estate contains a bank transfer from "
                            f"vendor RFC {rfc}'s recorded CLABE to employee "
                            f"{emp_id}'s recorded CLABE."
                        ),
                        evidence=[
                            EvidenceRef(source_table="vendors", record_id=rfc),
                            EvidenceRef(source_table="employees", record_id=emp_id),
                            EvidenceRef(source_table="bank_txns", record_id=txn_id),
                        ],
                        facts={
                            "vendor_rfc": rfc,
                            "employee_id": emp_id,
                            "vendor_clabe": from_clabe,
                            "employee_clabe": to_clabe,
                            "txn_id": txn_id,
                            "transaction_date": txn_date,
                            "amount": amount,
                            "direction": "vendor_to_employee",
                        },
                        score=None,
                        score_semantics=None,
                        limitations=[
                            (
                                "The transfer direction and CLABE associations are "
                                "facts inside the supplied estate, but they do not "
                                "establish a kickback or quid pro quo by themselves."
                            ),
                            (
                                "The estate may not contain external ownership, "
                                "reimbursement, or relationship documentation."
                            ),
                        ],
                        legitimate_alternatives=[
                            "A legitimate reimbursement or repayment.",
                            "A documented personal or business relationship.",
                            "Stale or incorrect CLABE ownership data in the estate.",
                        ],
                        recommended_checks=[
                            (
                                "Inspect payments into the vendor CLABE before this "
                                "transfer and compare timing/amounts."
                            ),
                            (
                                "Review invoices, purchase orders, contracts, and "
                                "employee role/context around the transfer date."
                            ),
                        ],
                    )
                )

    return observations


def _candidate_invoice_windows(
    rows: pd.DataFrame,
    *,
    min_count: int,
    window_days: int,
    max_relative_spread: float,
) -> list[pd.DataFrame]:
    candidates: list[pd.DataFrame] = []

    rows = rows.sort_values(by=["_date", "uuid"]).reset_index(drop=True)

    for start in range(len(rows)):
        start_date = rows.loc[start, "_date"]
        end_date = start_date + timedelta(days=window_days)
        window = rows[(rows["_date"] >= start_date) & (rows["_date"] <= end_date)]

        if len(window) < min_count:
            continue

        median_amount = float(window["_total"].median())
        if median_amount <= 0:
            continue

        relative_spread = (
            float(window["_total"].max()) - float(window["_total"].min())
        ) / median_amount

        if relative_spread <= max_relative_spread:
            candidates.append(window.copy())

    # Keep maximal windows so overlapping starts do not create a stack of
    # near-duplicate observations about the same invoice cluster.
    unique: dict[tuple[str, ...], pd.DataFrame] = {}
    for window in candidates:
        key = tuple(sorted(window["uuid"].tolist()))
        unique[key] = window

    ordered = sorted(unique.items(), key=lambda item: (-len(item[0]), item[0]))
    retained: list[tuple[set[str], pd.DataFrame]] = []

    for ids, window in ordered:
        id_set = set(ids)
        if any(id_set.issubset(existing_ids) for existing_ids, _ in retained):
            continue
        retained.append((id_set, window))

    return [window for _, window in retained]


def detect_short_window_similar_invoice_clusters(
    invoices: pd.DataFrame,
    *,
    min_count: int = 3,
    window_days: int = 7,
    max_relative_spread: float = 0.03,
) -> list[Observation]:
    """Surface short-window clusters of similar invoice totals by issuer.

    This is a discovery heuristic for potentially split activity. The official
    estate does not provide an approval threshold, so the detector explicitly
    does *not* claim that any invoice is below or evades a threshold.
    """

    required = {"uuid", "issuer_rfc", "receiver_rfc", "issue_date", "total"}
    if not required.issubset(invoices.columns):
        return []
    if min_count < 2 or window_days < 0 or max_relative_spread < 0:
        raise ValueError("Invalid cluster detection parameters.")

    rows = invoices[["uuid", "issuer_rfc", "receiver_rfc", "issue_date", "total"]].copy()
    rows["uuid"] = rows["uuid"].map(_clean_text)
    rows["issuer_rfc"] = rows["issuer_rfc"].map(_clean_text)
    rows["receiver_rfc"] = rows["receiver_rfc"].map(_clean_text)
    rows["_date"] = pd.to_datetime(rows["issue_date"], errors="coerce")
    rows["_total"] = pd.to_numeric(rows["total"], errors="coerce")
    rows = rows.dropna(subset=["uuid", "issuer_rfc", "receiver_rfc", "_date", "_total"])
    rows = rows[rows["_total"] > 0].drop_duplicates(subset=["uuid"])

    observations: list[Observation] = []

    for (issuer_rfc, receiver_rfc), group in rows.groupby(["issuer_rfc", "receiver_rfc"]):
        for window in _candidate_invoice_windows(
            group,
            min_count=min_count,
            window_days=window_days,
            max_relative_spread=max_relative_spread,
        ):
            uuids = sorted(window["uuid"].tolist())
            amounts = [float(value) for value in window["_total"].tolist()]
            first_date = window["_date"].min().date().isoformat()
            last_date = window["_date"].max().date().isoformat()
            median_amount = float(window["_total"].median())
            relative_spread = (
                max(amounts) - min(amounts)
            ) / median_amount

            observations.append(
                Observation(
                    observation_id=_observation_id(
                        "INVOICE-CLUSTER",
                        issuer_rfc,
                        receiver_rfc,
                        *uuids,
                    ),
                    detector_name="deterministic_relational",
                    signal_type="short_window_similar_invoice_cluster",
                    entities=[f"RFC:{issuer_rfc}", f"RFC:{receiver_rfc}"],
                    statement=(
                        f"The supplied estate contains {len(uuids)} invoices "
                        f"from issuer RFC {issuer_rfc} to receiver RFC {receiver_rfc} "
                        f"between {first_date} and {last_date} with closely similar totals."
                    ),
                    evidence=[
                        EvidenceRef(source_table="invoices", record_id=uuid)
                        for uuid in uuids
                    ],
                    facts={
                        "issuer_rfc": issuer_rfc,
                        "receiver_rfc": receiver_rfc,
                        "invoice_uuids": uuids,
                        "invoice_count": len(uuids),
                        "first_issue_date": first_date,
                        "last_issue_date": last_date,
                        "invoice_totals": sorted(amounts),
                        "cluster_total": float(sum(amounts)),
                        "relative_amount_spread": float(relative_spread),
                        "detection_parameters": {
                            "min_count": min_count,
                            "window_days": window_days,
                            "max_relative_spread": max_relative_spread,
                        },
                    },
                    score=None,
                    score_semantics=None,
                    limitations=[
                        (
                            "The official estate does not provide an approval "
                            "threshold. This observation therefore does not prove "
                            "threshold splitting or threshold evasion."
                        ),
                        (
                            "The window size, minimum count, and similarity bound "
                            "are transparent discovery heuristics, not legal or "
                            "policy thresholds."
                        ),
                    ],
                    legitimate_alternatives=[
                        "Recurring or installment billing for a legitimate service.",
                        "Staged deliveries with similar invoice values.",
                        "A standard-rate service billed repeatedly in a short period.",
                    ],
                    recommended_checks=[
                        (
                            "Compare these invoices with purchase orders, contracts, "
                            "requesters, approvers, and descriptions."
                        ),
                        (
                            "Obtain or verify the applicable approval policy before "
                            "calling the pattern threshold splitting."
                        ),
                    ],
                )
            )

    return observations



def _owner_entities_by_clabe(
    vendors: pd.DataFrame | None,
    employees: pd.DataFrame | None,
) -> dict[str, list[str]]:
    """Map each bank CLABE recorded in the estate to the entities that own it.

    Ownership is read one account at a time: resolving account A to vendor X
    establishes the title of A only, and says nothing about any other account in
    the same cycle.  An account with no recorded owner is simply absent here --
    no owner is invented for it.
    """

    owners: dict[str, list[str]] = {}

    def _collect(frame: pd.DataFrame | None, id_column: str, prefix: str) -> None:
        if frame is None or not {id_column, "bank_clabe"}.issubset(frame.columns):
            return

        rows = frame[[id_column, "bank_clabe"]].copy()
        rows["_id"] = rows[id_column].map(_clean_text)
        rows["_clabe"] = rows["bank_clabe"].map(_clean_text)
        rows = rows.dropna(subset=["_id", "_clabe"]).drop_duplicates(
            subset=["_id", "_clabe"]
        )

        for _, row in rows.iterrows():
            entity = with_entity_prefix(prefix, row["_id"])
            bucket = owners.setdefault(row["_clabe"], [])
            if entity not in bucket:
                bucket.append(entity)

    _collect(vendors, "rfc", "RFC:")
    _collect(employees, "emp_id", "EMP:")

    return {clabe: sorted(entities) for clabe, entities in owners.items()}


def detect_directed_bank_transfer_cycles(
    bank_txns: pd.DataFrame,
    *,
    vendors: pd.DataFrame | None = None,
    employees: pd.DataFrame | None = None,
    max_cycle_length: int = 4,
    max_cycles: int = 50,
) -> list[Observation]:
    """Surface bounded directed transfer cycles as structural observations.

    A directed cycle establishes that the supplied estate contains transfers
    along every directed arc in the cycle.  It does *not* establish that the
    same funds traversed the cycle, nor does it establish fraudulent intent.

    ``vendors`` and ``employees`` are optional and are used only to name the
    registered owner of each account in the cycle, so that the observation's
    subject is a party rather than an account number.  A CLABE is an internal
    identifier that may never reach official output; an observation whose only
    entity is an account therefore loses its subject downstream.
    """

    clabe_owners = _owner_entities_by_clabe(vendors, employees)

    graph = build_bank_multidigraph(bank_txns)
    cycles = find_directed_bank_cycles(
        graph,
        max_cycle_length=max_cycle_length,
        max_cycles=max_cycles,
    )

    observations: list[Observation] = []
    for cycle in cycles:
        txn_ids = list(cycle.transaction_ids)
        account_path = list(cycle.closed_account_path)
        amounts = list(cycle.amounts)
        dates = list(cycle.dates)

        # Owners first, so the observation's subject is a party. The accounts stay
        # in the list because the Investigator needs them to pull transactions.
        owner_entities: list[str] = []
        for clabe in cycle.accounts:
            for entity in clabe_owners.get(clabe, ()):
                if entity not in owner_entities:
                    owner_entities.append(entity)

        observations.append(
            Observation(
                observation_id=_observation_id(
                    "BANK-CYCLE",
                    *cycle.accounts,
                    *txn_ids,
                ),
                detector_name="deterministic_relational",
                signal_type="directed_bank_transfer_cycle",
                entities=(
                    owner_entities
                    + [f"CLABE:{clabe}" for clabe in cycle.accounts]
                ),
                statement=(
                    "The supplied estate contains a directed cycle of recorded "
                    f"bank transfers across {len(cycle.accounts)} CLABEs."
                ),
                evidence=[
                    EvidenceRef(source_table="bank_txns", record_id=txn_id)
                    for txn_id in txn_ids
                ],
                facts={
                    "account_cycle": account_path,
                    "transaction_ids": txn_ids,
                    "transaction_dates": dates,
                    "transaction_amounts": amounts,
                    "date_span_days": cycle.date_span_days,
                    "detection_parameters": {
                        "max_cycle_length": max_cycle_length,
                        "max_cycles": max_cycles,
                    },
                },
                score=None,
                score_semantics=None,
                limitations=[
                    (
                        "A directed transfer cycle is a structural fact, but it "
                        "does not prove that the same funds returned to their "
                        "origin or that the activity is round-tripping fraud."
                    ),
                    (
                        "The official bank data provides dates but not intraday "
                        "timestamps, so transfers sharing a date cannot be ordered "
                        "within that day from this estate alone."
                    ),
                    (
                        "CLABE nodes may lack known ownership in the supplied estate."
                    ),
                    (
                        "Cycle discovery is intentionally bounded by max_cycle_length "
                        "and max_cycles; additional structural cycles may exist."
                    ),
                ],
                legitimate_alternatives=[
                    "Legitimate reciprocal payments or settlements between parties.",
                    "Treasury, cash-pooling, refund, or intercompany activity.",
                    "Multiple economically distinct transfers that only form a structural cycle.",
                ],
                recommended_checks=[
                    (
                        "Identify the owners and business roles of every CLABE in "
                        "the cycle using estate records and available documentation."
                    ),
                    (
                        "Compare transfer amounts and dates and inspect supporting "
                        "invoices, purchase orders, contracts, and ledger entries."
                    ),
                    (
                        "Do not construct a verified money trail until continuity "
                        "of the claimed funds is independently supported."
                    ),
                ],
            )
        )

    return observations

def run_deterministic_relational_detectors(
    estate: EstateRepository,
) -> list[Observation]:
    """Run cross-table deterministic discovery against the official estate."""

    vendors = estate.table_df("vendors")
    employees = estate.table_df("employees")
    bank_txns = estate.table_df("bank_txns")
    invoices = estate.table_df("invoices")

    observations: list[Observation] = []
    observations.extend(
        detect_vendor_to_employee_transfers(
            vendors=vendors,
            employees=employees,
            bank_txns=bank_txns,
        )
    )
    observations.extend(
        detect_short_window_similar_invoice_clusters(
            invoices=invoices,
        )
    )
    observations.extend(
        detect_directed_bank_transfer_cycles(
            bank_txns=bank_txns,
            vendors=vendors,
            employees=employees,
        )
    )
    return observations
