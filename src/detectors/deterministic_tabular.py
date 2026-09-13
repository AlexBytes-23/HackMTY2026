"""
Deterministic tabular discovery for the Forensic Auditor.

This module intentionally reports observable relationships and patterns.
It does NOT decide whether fraud occurred.

Production principles:
- official estate schema only,
- provenance to exact records,
- no arbitrary fraud probabilities,
- no invented legal requirements,
- absence in the estate is not automatically absence in reality.
"""

from __future__ import annotations

import hashlib

import pandas as pd

from src.core.estate import EstateRepository
from src.core.models import EvidenceRef, Observation
from src.output.formatters import with_entity_prefix


def _clean_text(value) -> str | None:
    """Normalize identifiers while preserving their literal value."""
    if pd.isna(value):
        return None

    text = str(value).strip()

    if not text:
        return None

    return text


def _observation_id(prefix: str, *parts: str) -> str:
    """
    Produce a deterministic observation ID without embedding long/sensitive
    identifiers directly in the ID.
    """
    raw = "|".join(parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()

    return f"OBS-TAB-{prefix}-{digest}"


def detect_vendor_employee_shared_clabe(
    vendors: pd.DataFrame,
    employees: pd.DataFrame,
) -> list[Observation]:
    """
    Detect exact CLABE values that appear both in vendors and employees.

    This establishes an account-link fact only.
    It does NOT establish a kickback, ownership, collusion, or fraud.
    """

    required_vendor = {"rfc", "bank_clabe"}
    required_employee = {"emp_id", "bank_clabe"}

    if not required_vendor.issubset(vendors.columns):
        return []

    if not required_employee.issubset(employees.columns):
        return []

    vendor_rows = vendors[["rfc", "bank_clabe"]].copy()
    employee_rows = employees[["emp_id", "bank_clabe"]].copy()

    vendor_rows["_clabe"] = vendor_rows["bank_clabe"].map(_clean_text)
    employee_rows["_clabe"] = employee_rows["bank_clabe"].map(_clean_text)

    vendor_rows["rfc"] = vendor_rows["rfc"].map(_clean_text)
    employee_rows["emp_id"] = employee_rows["emp_id"].map(_clean_text)

    vendor_rows = vendor_rows.dropna(subset=["rfc", "_clabe"])
    employee_rows = employee_rows.dropna(subset=["emp_id", "_clabe"])

    vendor_rows = vendor_rows.drop_duplicates(subset=["rfc", "_clabe"])
    employee_rows = employee_rows.drop_duplicates(
        subset=["emp_id", "_clabe"]
    )

    matches = vendor_rows.merge(
        employee_rows,
        on="_clabe",
        how="inner",
        suffixes=("_vendor", "_employee"),
    )

    matches = matches.sort_values(
        by=["rfc", "emp_id", "_clabe"]
    )

    observations: list[Observation] = []

    for _, row in matches.iterrows():
        rfc = row["rfc"]
        emp_id = row["emp_id"]
        clabe = row["_clabe"]

        observations.append(
            Observation(
                observation_id=_observation_id(
                    "VENDOR-EMPLOYEE-CLABE",
                    rfc,
                    emp_id,
                    clabe,
                ),
                detector_name="deterministic_tabular",
                signal_type="vendor_employee_shared_clabe",
                entities=[
                    f"RFC:{rfc}",
                    # An estate may already store emp_id as "EMP:0001".
                    with_entity_prefix("EMP:", emp_id),
                    f"CLABE:{clabe}",
                ],
                statement=(
                    "The supplied estate contains the same bank CLABE "
                    f"for vendor RFC {rfc} and employee {emp_id}."
                ),
                evidence=[
                    EvidenceRef(
                        source_table="vendors",
                        record_id=rfc,
                    ),
                    EvidenceRef(
                        source_table="employees",
                        record_id=emp_id,
                    ),
                ],
                facts={
                    "vendor_rfc": rfc,
                    "employee_id": emp_id,
                    "shared_bank_clabe": clabe,
                    "match_type": "exact_clabe_match",
                },
                score=None,
                score_semantics=None,
                limitations=[
                    (
                        "An exact shared CLABE establishes an account link "
                        "inside the supplied estate; it does not by itself "
                        "establish a kickback, collusion, ownership, or fraud."
                    ),
                    (
                        "The relationship may require investigation for "
                        "data-quality issues or legitimate business context."
                    ),
                ],
                legitimate_alternatives=[
                    "A data-quality or duplicate-record issue.",
                    (
                        "A legitimate relationship or settlement arrangement "
                        "not represented elsewhere in the estate."
                    ),
                ],
                recommended_checks=[
                    (
                        "Inspect bank transactions involving the shared CLABE "
                        "and determine the direction, timing, and counterparties."
                    ),
                    (
                        "Review vendor and employee context for a documented "
                        "legitimate relationship."
                    ),
                ],
            )
        )

    return observations


def detect_shared_vendor_clabe(
    vendors: pd.DataFrame,
) -> list[Observation]:
    """
    Detect a bank CLABE associated with multiple distinct vendor RFCs.

    This establishes shared account usage in the supplied estate only.
    """

    required = {"rfc", "bank_clabe"}

    if not required.issubset(vendors.columns):
        return []

    rows = vendors[["rfc", "bank_clabe"]].copy()

    rows["rfc"] = rows["rfc"].map(_clean_text)
    rows["_clabe"] = rows["bank_clabe"].map(_clean_text)

    rows = rows.dropna(subset=["rfc", "_clabe"])
    rows = rows.drop_duplicates(subset=["rfc", "_clabe"])

    observations: list[Observation] = []

    for clabe, group in rows.groupby("_clabe"):
        rfcs = sorted(set(group["rfc"].tolist()))

        if len(rfcs) < 2:
            continue

        observations.append(
            Observation(
                observation_id=_observation_id(
                    "SHARED-VENDOR-CLABE",
                    clabe,
                    *rfcs,
                ),
                detector_name="deterministic_tabular",
                signal_type="shared_vendor_clabe",
                entities=[
                    f"CLABE:{clabe}",
                    *[f"RFC:{rfc}" for rfc in rfcs],
                ],
                statement=(
                    "The supplied estate associates one bank CLABE "
                    f"with {len(rfcs)} distinct vendor RFCs."
                ),
                evidence=[
                    EvidenceRef(
                        source_table="vendors",
                        record_id=rfc,
                    )
                    for rfc in rfcs
                ],
                facts={
                    "shared_bank_clabe": clabe,
                    "vendor_rfcs": rfcs,
                    "vendor_count": len(rfcs),
                    "match_type": "exact_clabe_match",
                },
                score=None,
                score_semantics=None,
                limitations=[
                    (
                        "Shared account usage does not establish common "
                        "ownership, collusion, or fraudulent activity."
                    )
                ],
                legitimate_alternatives=[
                    "Affiliated companies may legitimately share treasury.",
                    "A payment intermediary or shared settlement account may exist.",
                    "The estate may contain duplicate or stale banking data.",
                ],
                recommended_checks=[
                    (
                        "Compare vendor legal names, addresses, contracts, "
                        "and transaction counterparties."
                    )
                ],
            )
        )

    return observations


def detect_efos_vendor_matches(
    vendors: pd.DataFrame,
    efos_list: pd.DataFrame,
) -> list[Observation]:
    """
    Detect vendor RFCs that also occur in the supplied EFOS table.

    The detector deliberately does not interpret EFOS membership as proof
    that a particular transaction is fraudulent.
    """

    if "rfc" not in vendors.columns:
        return []

    if "rfc" not in efos_list.columns:
        return []

    vendor_rows = vendors[["rfc"]].copy()
    vendor_rows["rfc"] = vendor_rows["rfc"].map(_clean_text)
    vendor_rows = vendor_rows.dropna().drop_duplicates()

    efos_rows = efos_list.copy()
    efos_rows["rfc"] = efos_rows["rfc"].map(_clean_text)
    efos_rows = efos_rows.dropna(subset=["rfc"])

    vendor_rfcs = set(vendor_rows["rfc"])

    observations: list[Observation] = []

    for rfc, group in efos_rows.groupby("rfc"):
        if rfc not in vendor_rfcs:
            continue

        statuses = []

        if "status" in group.columns:
            statuses = sorted(
                {
                    status
                    for status in (
                        _clean_text(value)
                        for value in group["status"]
                    )
                    if status is not None
                }
            )

        publication_dates = []

        if "publication_date" in group.columns:
            publication_dates = sorted(
                {
                    date
                    for date in (
                        _clean_text(value)
                        for value in group["publication_date"]
                    )
                    if date is not None
                }
            )

        observations.append(
            Observation(
                observation_id=_observation_id(
                    "EFOS-MATCH",
                    rfc,
                    *statuses,
                    *publication_dates,
                ),
                detector_name="deterministic_tabular",
                signal_type="vendor_efos_record_match",
                entities=[f"RFC:{rfc}"],
                statement=(
                    f"Vendor RFC {rfc} appears in the EFOS table "
                    "supplied with the estate."
                ),
                evidence=[
                    EvidenceRef(
                        source_table="vendors",
                        record_id=rfc,
                    ),
                    EvidenceRef(
                        source_table="efos_list",
                        record_id=rfc,
                    ),
                ],
                facts={
                    "vendor_rfc": rfc,
                    "efos_statuses": statuses,
                    "publication_dates": publication_dates,
                    "match_type": "exact_rfc_match",
                },
                score=None,
                score_semantics=None,
                limitations=[
                    (
                        "Presence in the EFOS table does not establish that "
                        "every transaction involving this RFC is fraudulent."
                    ),
                    (
                        "Status, publication timing, transaction timing, and "
                        "transaction-specific evidence must still be evaluated."
                    ),
                ],
                legitimate_alternatives=[
                    (
                        "The EFOS status or publication timing may not support "
                        "the specific transaction hypothesis being investigated."
                    )
                ],
                recommended_checks=[
                    "Inspect EFOS status and publication timing.",
                    (
                        "Compare relevant transaction dates against the EFOS "
                        "record before drawing transaction-specific conclusions."
                    ),
                ],
            )
        )

    return observations


def run_deterministic_tabular_detectors(
    estate: EstateRepository,
) -> list[Observation]:
    """
    Run the production-safe deterministic discovery subset.

    Additional detectors should be added only after their semantics and
    provenance requirements have been reviewed.
    """

    # Detectors operate on DataFrames. Use the repository's explicit
    # DataFrame boundary rather than get_all(), which returns list[dict].
    vendors = estate.table_df("vendors")
    employees = estate.table_df("employees")
    efos_list = estate.table_df("efos_list")

    observations: list[Observation] = []

    observations.extend(
        detect_vendor_employee_shared_clabe(
            vendors=vendors,
            employees=employees,
        )
    )

    observations.extend(
        detect_shared_vendor_clabe(
            vendors=vendors,
        )
    )

    observations.extend(
        detect_efos_vendor_matches(
            vendors=vendors,
            efos_list=efos_list,
        )
    )

    return observations