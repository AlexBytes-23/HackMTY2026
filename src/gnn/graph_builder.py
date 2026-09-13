from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch_geometric.data import HeteroData

from src.core.estate import EstateRepository
from src.core.models import EvidenceRef
from src.gnn.contracts import EdgeType, GNNGraphBundle
from src.gnn.features import (
    ACCOUNT_FEATURES,
    BINARY_FEATURES,
    EMPLOYEE_FEATURES,
    VENDOR_FEATURES,
    assert_low_level_feature_contract,
    rows_to_tensor,
)


TRANSFER_EDGE: EdgeType = ("account", "transfers_to", "account")
VENDOR_OWNS_EDGE: EdgeType = ("vendor", "owns", "account")
ACCOUNT_VENDOR_EDGE: EdgeType = ("account", "owned_by_vendor", "vendor")
EMPLOYEE_OWNS_EDGE: EdgeType = ("employee", "owns", "account")
ACCOUNT_EMPLOYEE_EDGE: EdgeType = ("account", "owned_by_employee", "employee")

TRANSFER_EDGE_FEATURES = (
    "txn_count",
    "total_amount",
    "mean_amount",
    "max_abs_amount",
    "date_span_days",
)


def _clean_text(value: Any) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _numeric(value: Any) -> float | None:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return None
    number = float(number)
    return number if np.isfinite(number) else None


def _valid_bank_rows(bank_txns: pd.DataFrame) -> pd.DataFrame:
    required = {"txn_id", "date", "from_clabe", "to_clabe", "amount"}
    if not required.issubset(bank_txns.columns):
        raise ValueError("bank_txns is missing columns required by GNN graph builder")

    rows = bank_txns.copy()
    rows["txn_id"] = rows["txn_id"].map(_clean_text)
    rows["_from"] = rows["from_clabe"].map(_clean_text)
    rows["_to"] = rows["to_clabe"].map(_clean_text)
    rows["_amount"] = pd.to_numeric(rows["amount"], errors="coerce")
    rows["_date"] = pd.to_datetime(rows["date"], errors="coerce")
    rows = rows.dropna(subset=["txn_id", "_from", "_to", "_amount"])
    rows = rows[np.isfinite(rows["_amount"].astype(float))].copy()
    return rows


def _ids(df: pd.DataFrame, column: str) -> tuple[str, ...]:
    if column not in df.columns:
        return ()
    values = {_clean_text(value) for value in df[column].tolist()}
    return tuple(sorted(value for value in values if value is not None))


def _vendor_ids(
    vendors: pd.DataFrame,
    invoices: pd.DataFrame,
    purchase_orders: pd.DataFrame,
    contracts: pd.DataFrame,
) -> tuple[str, ...]:
    """Vendor-like RFCs actually referenced inside the estate.

    A vendor RFC can be forensically relevant even when it is missing from the
    vendor master table.  We therefore preserve RFCs that occur in transaction
    or procurement tables, while deliberately *not* expanding every RFC from
    the external EFOS list into a graph node.
    """

    values: set[str] = set()
    for frame, column in (
        (vendors, "rfc"),
        (invoices, "issuer_rfc"),
        (purchase_orders, "vendor_rfc"),
        (contracts, "vendor_rfc"),
    ):
        if column not in frame.columns:
            continue
        values.update(
            value
            for value in (_clean_text(item) for item in frame[column].tolist())
            if value is not None
        )
    return tuple(sorted(values))


def _account_ids(
    vendors: pd.DataFrame,
    employees: pd.DataFrame,
    bank_rows: pd.DataFrame,
) -> tuple[str, ...]:
    values: set[str] = set()
    for frame in (vendors, employees):
        if "bank_clabe" in frame.columns:
            values.update(
                value
                for value in (_clean_text(item) for item in frame["bank_clabe"].tolist())
                if value is not None
            )
    values.update(bank_rows["_from"].astype(str).tolist())
    values.update(bank_rows["_to"].astype(str).tolist())
    return tuple(sorted(values))


def _bank_stats_by_account(bank_rows: pd.DataFrame) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "total_in": 0.0,
            "total_out": 0.0,
            "count_in": 0.0,
            "count_out": 0.0,
            "mean_amount_in": 0.0,
            "mean_amount_out": 0.0,
            "std_amount": 0.0,
            "max_abs_amount": 0.0,
        }
    )
    incoming_values: dict[str, list[float]] = defaultdict(list)
    outgoing_values: dict[str, list[float]] = defaultdict(list)
    all_values: dict[str, list[float]] = defaultdict(list)
    senders: dict[str, set[str]] = defaultdict(set)
    receivers: dict[str, set[str]] = defaultdict(set)

    for row in bank_rows.to_dict(orient="records"):
        source = str(row["_from"])
        target = str(row["_to"])
        amount = float(row["_amount"])
        stats[source]["total_out"] += amount
        stats[source]["count_out"] += 1.0
        stats[target]["total_in"] += amount
        stats[target]["count_in"] += 1.0
        outgoing_values[source].append(amount)
        incoming_values[target].append(amount)
        all_values[source].append(amount)
        all_values[target].append(amount)
        receivers[source].add(target)
        senders[target].add(source)

    for account in set(stats) | set(incoming_values) | set(outgoing_values):
        in_values = incoming_values.get(account, [])
        out_values = outgoing_values.get(account, [])
        values = all_values.get(account, [])
        stats[account]["mean_amount_in"] = float(np.mean(in_values)) if in_values else 0.0
        stats[account]["mean_amount_out"] = float(np.mean(out_values)) if out_values else 0.0
        stats[account]["std_amount"] = float(np.std(values)) if values else 0.0
        stats[account]["max_abs_amount"] = max((abs(v) for v in values), default=0.0)
        stats[account]["unique_senders"] = float(len(senders.get(account, set())))
        stats[account]["unique_receivers"] = float(len(receivers.get(account, set())))

    return stats


def _ownership_counts(
    vendors: pd.DataFrame,
    employees: pd.DataFrame,
) -> tuple[dict[str, int], dict[str, int]]:
    vendor_counts: dict[str, int] = defaultdict(int)
    employee_counts: dict[str, int] = defaultdict(int)

    if {"rfc", "bank_clabe"}.issubset(vendors.columns):
        seen: set[tuple[str, str]] = set()
        for row in vendors[["rfc", "bank_clabe"]].to_dict(orient="records"):
            rfc = _clean_text(row["rfc"])
            clabe = _clean_text(row["bank_clabe"])
            if rfc and clabe and (rfc, clabe) not in seen:
                seen.add((rfc, clabe))
                vendor_counts[clabe] += 1

    if {"emp_id", "bank_clabe"}.issubset(employees.columns):
        seen_emp: set[tuple[str, str]] = set()
        for row in employees[["emp_id", "bank_clabe"]].to_dict(orient="records"):
            emp_id = _clean_text(row["emp_id"])
            clabe = _clean_text(row["bank_clabe"])
            if emp_id and clabe and (emp_id, clabe) not in seen_emp:
                seen_emp.add((emp_id, clabe))
                employee_counts[clabe] += 1

    return vendor_counts, employee_counts


def _vendor_rows(
    vendor_ids: tuple[str, ...],
    vendors: pd.DataFrame,
    invoices: pd.DataFrame,
    ledger: pd.DataFrame,
    purchase_orders: pd.DataFrame,
    contracts: pd.DataFrame,
    efos: pd.DataFrame,
    bank_rows: pd.DataFrame,
) -> tuple[dict[str, float], ...]:
    invoice_groups: dict[str, pd.DataFrame] = {}
    if {"issuer_rfc", "total", "issue_date", "uuid"}.issubset(invoices.columns):
        temp = invoices.copy()
        temp["issuer_rfc"] = temp["issuer_rfc"].map(_clean_text)
        temp["_total"] = pd.to_numeric(temp["total"], errors="coerce")
        temp["_date"] = pd.to_datetime(temp["issue_date"], errors="coerce")
        invoice_groups = {str(k): v for k, v in temp.dropna(subset=["issuer_rfc"]).groupby("issuer_rfc")}

    po_groups = {}
    if {"vendor_rfc", "amount"}.issubset(purchase_orders.columns):
        temp = purchase_orders.copy()
        temp["vendor_rfc"] = temp["vendor_rfc"].map(_clean_text)
        temp["_amount"] = pd.to_numeric(temp["amount"], errors="coerce")
        po_groups = {str(k): v for k, v in temp.dropna(subset=["vendor_rfc"]).groupby("vendor_rfc")}

    contract_groups = {}
    if {"vendor_rfc", "value"}.issubset(contracts.columns):
        temp = contracts.copy()
        temp["vendor_rfc"] = temp["vendor_rfc"].map(_clean_text)
        temp["_value"] = pd.to_numeric(temp["value"], errors="coerce")
        contract_groups = {str(k): v for k, v in temp.dropna(subset=["vendor_rfc"]).groupby("vendor_rfc")}

    efos_rfcs = set()
    if "rfc" in efos.columns:
        efos_rfcs = {value for value in (_clean_text(v) for v in efos["rfc"].tolist()) if value}

    vendor_clabe: dict[str, str] = {}
    if {"rfc", "bank_clabe"}.issubset(vendors.columns):
        for row in vendors[["rfc", "bank_clabe"]].to_dict(orient="records"):
            rfc = _clean_text(row["rfc"])
            clabe = _clean_text(row["bank_clabe"])
            if rfc and clabe and rfc not in vendor_clabe:
                vendor_clabe[rfc] = clabe

    bank_stats = _bank_stats_by_account(bank_rows)
    registered_rfcs = set(_ids(vendors, "rfc"))
    counterparties: dict[str, set[str]] = defaultdict(set)
    for row in bank_rows.to_dict(orient="records"):
        source = str(row["_from"])
        target = str(row["_to"])
        counterparties[source].add(target)
        counterparties[target].add(source)

    invoice_to_vendor: dict[str, str] = {}
    if {"uuid", "issuer_rfc"}.issubset(invoices.columns):
        for row in invoices[["uuid", "issuer_rfc"]].to_dict(orient="records"):
            uuid = _clean_text(row["uuid"])
            issuer = _clean_text(row["issuer_rfc"])
            if uuid and issuer:
                invoice_to_vendor[uuid] = issuer

    ledger_stats: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0.0, "debit": 0.0, "credit": 0.0}
    )
    if {"invoice_uuid", "debit", "credit"}.issubset(ledger.columns):
        for row in ledger[["invoice_uuid", "debit", "credit"]].to_dict(orient="records"):
            uuid = _clean_text(row["invoice_uuid"])
            vendor = invoice_to_vendor.get(uuid or "")
            if not vendor:
                continue
            debit = _numeric(row["debit"]) or 0.0
            credit = _numeric(row["credit"]) or 0.0
            ledger_stats[vendor]["count"] += 1.0
            ledger_stats[vendor]["debit"] += debit
            ledger_stats[vendor]["credit"] += credit

    rows: list[dict[str, float]] = []
    for rfc in vendor_ids:
        inv = invoice_groups.get(rfc)
        inv_amounts = [] if inv is None else [float(v) for v in inv["_total"].dropna().tolist() if np.isfinite(float(v))]
        inv_dates = pd.Series([], dtype="datetime64[ns]") if inv is None else inv["_date"].dropna()
        active_span = 0.0
        if len(inv_dates) >= 2:
            active_span = float((inv_dates.max() - inv_dates.min()).days)

        po = po_groups.get(rfc)
        po_amounts = [] if po is None else [float(v) for v in po["_amount"].dropna().tolist() if np.isfinite(float(v))]
        contract = contract_groups.get(rfc)
        contract_amounts = [] if contract is None else [float(v) for v in contract["_value"].dropna().tolist() if np.isfinite(float(v))]
        clabe = vendor_clabe.get(rfc)
        bank = bank_stats.get(clabe or "", {})
        ledger_row = ledger_stats.get(rfc, {})

        unique_counterparties = float(len(counterparties.get(clabe or "", set())))
        rows.append(
            {
                "registered_vendor": 1.0 if rfc in registered_rfcs else 0.0,
                "invoice_count": float(len(inv_amounts)),
                "invoice_total": float(sum(inv_amounts)),
                "invoice_mean": float(np.mean(inv_amounts)) if inv_amounts else 0.0,
                "invoice_std": float(np.std(inv_amounts)) if inv_amounts else 0.0,
                "invoice_active_span_days": active_span,
                "po_count": float(len(po_amounts)),
                "po_total": float(sum(po_amounts)),
                "contract_count": float(len(contract_amounts)),
                "contract_total": float(sum(contract_amounts)),
                "ledger_entry_count": float(ledger_row.get("count", 0.0)),
                "ledger_debit_total": float(ledger_row.get("debit", 0.0)),
                "ledger_credit_total": float(ledger_row.get("credit", 0.0)),
                "efos_match": 1.0 if rfc in efos_rfcs else 0.0,
                "bank_total_in": float(bank.get("total_in", 0.0)),
                "bank_total_out": float(bank.get("total_out", 0.0)),
                "bank_txn_count_in": float(bank.get("count_in", 0.0)),
                "bank_txn_count_out": float(bank.get("count_out", 0.0)),
                "bank_unique_counterparties": unique_counterparties,
            }
        )
    return tuple(rows)


def _employee_rows(
    employee_ids: tuple[str, ...],
    employees: pd.DataFrame,
    vendors: pd.DataFrame,
    bank_rows: pd.DataFrame,
) -> tuple[dict[str, float], ...]:
    employee_clabe: dict[str, str] = {}
    if {"emp_id", "bank_clabe"}.issubset(employees.columns):
        for row in employees[["emp_id", "bank_clabe"]].to_dict(orient="records"):
            emp_id = _clean_text(row["emp_id"])
            clabe = _clean_text(row["bank_clabe"])
            if emp_id and clabe and emp_id not in employee_clabe:
                employee_clabe[emp_id] = clabe

    vendor_accounts = set()
    if "bank_clabe" in vendors.columns:
        vendor_accounts = {value for value in (_clean_text(v) for v in vendors["bank_clabe"].tolist()) if value}

    bank_stats = _bank_stats_by_account(bank_rows)
    counterparties: dict[str, set[str]] = defaultdict(set)
    for row in bank_rows.to_dict(orient="records"):
        source = str(row["_from"])
        target = str(row["_to"])
        counterparties[source].add(target)
        counterparties[target].add(source)

    rows: list[dict[str, float]] = []
    for emp_id in employee_ids:
        clabe = employee_clabe.get(emp_id)
        bank = bank_stats.get(clabe or "", {})
        peers = counterparties.get(clabe or "", set())
        rows.append(
            {
                "bank_total_in": float(bank.get("total_in", 0.0)),
                "bank_total_out": float(bank.get("total_out", 0.0)),
                "bank_txn_count_in": float(bank.get("count_in", 0.0)),
                "bank_txn_count_out": float(bank.get("count_out", 0.0)),
                "bank_unique_counterparties": float(len(peers)),
                "vendor_owned_counterparties": float(len(peers & vendor_accounts)),
            }
        )
    return tuple(rows)


def _account_rows(
    account_ids: tuple[str, ...],
    vendors: pd.DataFrame,
    employees: pd.DataFrame,
    bank_rows: pd.DataFrame,
) -> tuple[dict[str, float], ...]:
    stats = _bank_stats_by_account(bank_rows)
    vendor_counts, employee_counts = _ownership_counts(vendors, employees)
    rows: list[dict[str, float]] = []
    for clabe in account_ids:
        bank = stats.get(clabe, {})
        rows.append(
            {
                "total_in": float(bank.get("total_in", 0.0)),
                "total_out": float(bank.get("total_out", 0.0)),
                "count_in": float(bank.get("count_in", 0.0)),
                "count_out": float(bank.get("count_out", 0.0)),
                "unique_senders": float(bank.get("unique_senders", 0.0)),
                "unique_receivers": float(bank.get("unique_receivers", 0.0)),
                "mean_amount_in": float(bank.get("mean_amount_in", 0.0)),
                "mean_amount_out": float(bank.get("mean_amount_out", 0.0)),
                "std_amount": float(bank.get("std_amount", 0.0)),
                "max_abs_amount": float(bank.get("max_abs_amount", 0.0)),
                "vendor_owner_count": float(vendor_counts.get(clabe, 0)),
                "employee_owner_count": float(employee_counts.get(clabe, 0)),
            }
        )
    return tuple(rows)


def _edge_index(pairs: list[tuple[int, int]]) -> torch.Tensor:
    if not pairs:
        return torch.empty((2, 0), dtype=torch.long)
    return torch.tensor(pairs, dtype=torch.long).t().contiguous()


def _ownership_edges(
    frame: pd.DataFrame,
    id_column: str,
    node_index: dict[str, int],
    account_index: dict[str, int],
    source_table: str,
) -> tuple[list[tuple[int, int]], list[tuple[EvidenceRef, ...]]]:
    pairs: list[tuple[int, int]] = []
    provenance: list[tuple[EvidenceRef, ...]] = []
    if not {id_column, "bank_clabe"}.issubset(frame.columns):
        return pairs, provenance

    seen: set[tuple[str, str]] = set()
    rows = []
    for row in frame[[id_column, "bank_clabe"]].to_dict(orient="records"):
        entity = _clean_text(row[id_column])
        clabe = _clean_text(row["bank_clabe"])
        if entity and clabe:
            rows.append((entity, clabe))
    for entity, clabe in sorted(rows):
        if (entity, clabe) in seen:
            continue
        seen.add((entity, clabe))
        if entity not in node_index or clabe not in account_index:
            continue
        pairs.append((node_index[entity], account_index[clabe]))
        provenance.append((EvidenceRef(source_table=source_table, record_id=entity),))
    return pairs, provenance


def _transfer_edges(
    bank_rows: pd.DataFrame,
    account_index: dict[str, int],
) -> tuple[list[tuple[int, int]], list[list[float]], list[tuple[EvidenceRef, ...]]]:
    pairs: list[tuple[int, int]] = []
    attributes: list[list[float]] = []
    provenance: list[tuple[EvidenceRef, ...]] = []

    grouped = bank_rows.groupby(["_from", "_to"], sort=True)
    for (source, target), group in grouped:
        source = str(source)
        target = str(target)
        amounts = [float(v) for v in group["_amount"].tolist()]
        dates = group["_date"].dropna()
        span_days = float((dates.max() - dates.min()).days) if len(dates) >= 2 else 0.0
        pairs.append((account_index[source], account_index[target]))
        attributes.append(
            [
                float(len(group)),
                float(sum(amounts)),
                float(np.mean(amounts)),
                max((abs(value) for value in amounts), default=0.0),
                span_days,
            ]
        )
        refs = tuple(
            EvidenceRef(source_table="bank_txns", record_id=str(txn_id))
            for txn_id in sorted(group["txn_id"].astype(str).tolist())
        )
        provenance.append(refs)
    return pairs, attributes, provenance


def build_gnn_graph(estate: EstateRepository) -> GNNGraphBundle:
    """Build a low-level heterogeneous graph from the official estate.

    The representation is deliberately descriptive rather than accusatory.
    Invoices, purchase orders, contracts, ledger and EFOS data enrich vendor
    node features; they are not expanded into leaf nodes in this first model.
    """

    vendors = estate.table_df("vendors")
    employees = estate.table_df("employees")
    bank_txns = estate.table_df("bank_txns")
    invoices = estate.table_df("invoices")
    ledger = estate.table_df("ledger")
    purchase_orders = estate.table_df("purchase_orders")
    contracts = estate.table_df("contracts")
    efos = estate.table_df("efos_list")

    bank_rows = _valid_bank_rows(bank_txns)
    vendor_ids = _vendor_ids(vendors, invoices, purchase_orders, contracts)
    employee_ids = _ids(employees, "emp_id")
    account_ids = _account_ids(vendors, employees, bank_rows)

    node_ids = {
        "vendor": vendor_ids,
        "employee": employee_ids,
        "account": account_ids,
    }
    node_index = {
        node_type: {entity_id: index for index, entity_id in enumerate(ids)}
        for node_type, ids in node_ids.items()
    }

    feature_names = {
        "vendor": VENDOR_FEATURES,
        "employee": EMPLOYEE_FEATURES,
        "account": ACCOUNT_FEATURES,
    }
    assert_low_level_feature_contract(feature_names)

    raw_features = {
        "vendor": _vendor_rows(
            vendor_ids,
            vendors,
            invoices,
            ledger,
            purchase_orders,
            contracts,
            efos,
            bank_rows,
        ),
        "employee": _employee_rows(employee_ids, employees, vendors, bank_rows),
        "account": _account_rows(account_ids, vendors, employees, bank_rows),
    }

    data = HeteroData()
    for node_type in ("vendor", "employee", "account"):
        data[node_type].x = rows_to_tensor(
            raw_features[node_type],
            feature_names[node_type],
            binary_features=BINARY_FEATURES[node_type],
        )
        data[node_type].num_nodes = len(node_ids[node_type])

    vendor_pairs, vendor_prov = _ownership_edges(
        vendors,
        "rfc",
        node_index["vendor"],
        node_index["account"],
        "vendors",
    )
    employee_pairs, employee_prov = _ownership_edges(
        employees,
        "emp_id",
        node_index["employee"],
        node_index["account"],
        "employees",
    )

    transfer_pairs, transfer_attrs, transfer_prov = _transfer_edges(
        bank_rows,
        node_index["account"],
    )

    data[VENDOR_OWNS_EDGE].edge_index = _edge_index(vendor_pairs)
    data[ACCOUNT_VENDOR_EDGE].edge_index = _edge_index(
        [(target, source) for source, target in vendor_pairs]
    )
    data[EMPLOYEE_OWNS_EDGE].edge_index = _edge_index(employee_pairs)
    data[ACCOUNT_EMPLOYEE_EDGE].edge_index = _edge_index(
        [(target, source) for source, target in employee_pairs]
    )
    data[TRANSFER_EDGE].edge_index = _edge_index(transfer_pairs)

    if transfer_attrs:
        raw_edge_attr = torch.tensor(transfer_attrs, dtype=torch.float32)
    else:
        raw_edge_attr = torch.empty((0, len(TRANSFER_EDGE_FEATURES)), dtype=torch.float32)
    data[TRANSFER_EDGE].edge_attr_raw = raw_edge_attr
    data[TRANSFER_EDGE].edge_attr = rows_to_tensor(
        (
            {name: value for name, value in zip(TRANSFER_EDGE_FEATURES, row)}
            for row in transfer_attrs
        ),
        TRANSFER_EDGE_FEATURES,
    )

    edge_feature_names = {TRANSFER_EDGE: TRANSFER_EDGE_FEATURES}
    edge_provenance = {
        VENDOR_OWNS_EDGE: tuple(vendor_prov),
        ACCOUNT_VENDOR_EDGE: tuple(vendor_prov),
        EMPLOYEE_OWNS_EDGE: tuple(employee_prov),
        ACCOUNT_EMPLOYEE_EDGE: tuple(employee_prov),
        TRANSFER_EDGE: tuple(transfer_prov),
    }

    return GNNGraphBundle(
        data=data,
        node_ids=node_ids,
        node_index=node_index,
        feature_names=feature_names,
        raw_features=raw_features,
        edge_feature_names=edge_feature_names,
        edge_provenance=edge_provenance,
    )
