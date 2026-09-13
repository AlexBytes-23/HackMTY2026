"""Exact bank-transfer graph built from the official estate schema.

This module represents recorded transfer facts.  It deliberately does not
interpret a path or cycle as proof that the same funds moved through every
step, nor does it infer ownership for CLABEs that are absent from entity
tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import networkx as nx
import pandas as pd


BANK_TXN_COLUMNS = {
    "txn_id",
    "date",
    "from_clabe",
    "to_clabe",
    "amount",
    "reference",
    "channel",
}


def _clean_text(value: Any) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class BankCycle:
    """One deterministic representative transfer for each arc in a cycle.

    ``accounts`` contains each account once; the implied final step returns
    from accounts[-1] to accounts[0].  ``transaction_ids`` is aligned with
    those directed steps.
    """

    accounts: tuple[str, ...]
    transaction_ids: tuple[str, ...]
    dates: tuple[str | None, ...]
    amounts: tuple[float, ...]
    date_span_days: int | None

    @property
    def closed_account_path(self) -> tuple[str, ...]:
        return self.accounts + (self.accounts[0],)


def build_bank_multidigraph(bank_txns: pd.DataFrame) -> nx.MultiDiGraph:
    """Build the exact directed multigraph for valid official bank records.

    Nodes are CLABEs, including CLABEs whose owner is unknown in the supplied
    estate.  Each valid transaction is a distinct directed edge keyed by its
    ``txn_id`` so parallel transfers are never silently collapsed.
    """

    missing = BANK_TXN_COLUMNS - set(bank_txns.columns)
    if missing:
        raise ValueError(
            "bank_txns is missing official columns: " + ", ".join(sorted(missing))
        )

    rows = bank_txns[list(sorted(BANK_TXN_COLUMNS))].copy()
    rows["txn_id"] = rows["txn_id"].map(_clean_text)
    rows["from_clabe"] = rows["from_clabe"].map(_clean_text)
    rows["to_clabe"] = rows["to_clabe"].map(_clean_text)
    rows["date"] = rows["date"].map(_clean_text)
    rows["reference"] = rows["reference"].map(_clean_text)
    rows["channel"] = rows["channel"].map(_clean_text)
    rows["amount"] = pd.to_numeric(rows["amount"], errors="coerce")

    rows = rows.dropna(subset=["txn_id", "from_clabe", "to_clabe", "amount"])

    duplicate_ids = sorted(
        rows.loc[rows["txn_id"].duplicated(keep=False), "txn_id"].unique().tolist()
    )
    if duplicate_ids:
        raise ValueError(
            "bank_txns contains duplicate txn_id values; provenance would be ambiguous: "
            + ", ".join(duplicate_ids)
        )

    graph = nx.MultiDiGraph()

    # Sorting makes node/edge insertion deterministic without assigning any
    # semantic meaning to the transaction id.
    for row in rows.sort_values(by=["txn_id"]).to_dict(orient="records"):
        txn_id = str(row["txn_id"])
        from_clabe = str(row["from_clabe"])
        to_clabe = str(row["to_clabe"])
        amount = float(row["amount"])

        graph.add_edge(
            from_clabe,
            to_clabe,
            key=txn_id,
            txn_id=txn_id,
            date=row["date"],
            amount=amount,
            reference=row["reference"],
            channel=row["channel"],
            source_table="bank_txns",
            record_id=txn_id,
        )

    return graph


def _canonical_directed_cycle(nodes: list[str]) -> tuple[str, ...]:
    """Canonicalize rotations while preserving directed orientation."""

    if not nodes:
        raise ValueError("A cycle must contain at least one node.")
    rotations = [tuple(nodes[i:] + nodes[:i]) for i in range(len(nodes))]
    return min(rotations)


def _representative_edge(
    graph: nx.MultiDiGraph,
    source: str,
    target: str,
) -> dict[str, Any]:
    edge_map = graph.get_edge_data(source, target)
    if not edge_map:
        raise ValueError(f"Missing expected cycle edge {source} -> {target}.")

    # A structural cycle only needs one recorded edge for each arc.  Choose a
    # deterministic representative but keep every parallel transfer in the
    # exact graph for later investigation.
    candidates = list(edge_map.values())
    candidates.sort(
        key=lambda attrs: (
            attrs.get("date") or "",
            attrs.get("txn_id") or "",
        )
    )
    return candidates[0]


def _date_span_days(date_values: list[str | None]) -> int | None:
    parsed: list[date] = []
    for value in date_values:
        if not value:
            return None
        timestamp = pd.to_datetime(value, errors="coerce")
        if pd.isna(timestamp):
            return None
        parsed.append(timestamp.date())

    if not parsed:
        return None
    return (max(parsed) - min(parsed)).days


def find_directed_bank_cycles(
    graph: nx.MultiDiGraph,
    *,
    max_cycle_length: int = 4,
    max_cycles: int = 50,
) -> list[BankCycle]:
    """Find bounded structural directed cycles without claiming fund identity.

    The search is performed on a simple directed projection to avoid parallel
    edges multiplying the topology search.  The exact multigraph remains the
    source of transaction provenance.  Self-loops are excluded because they do
    not represent a multi-account round-trip structure.
    """

    if max_cycle_length < 2:
        raise ValueError("max_cycle_length must be at least 2.")
    if max_cycles < 1:
        raise ValueError("max_cycles must be positive.")

    simple = nx.DiGraph()
    simple.add_nodes_from(graph.nodes)
    simple.add_edges_from((u, v) for u, v in graph.edges() if u != v)

    found: list[BankCycle] = []
    seen: set[tuple[str, ...]] = set()

    for raw_cycle in nx.simple_cycles(simple, length_bound=max_cycle_length):
        if len(raw_cycle) < 2:
            continue
        canonical = _canonical_directed_cycle([str(node) for node in raw_cycle])
        if canonical in seen:
            continue
        seen.add(canonical)

        txn_ids: list[str] = []
        dates: list[str | None] = []
        amounts: list[float] = []

        for index, source in enumerate(canonical):
            target = canonical[(index + 1) % len(canonical)]
            edge = _representative_edge(graph, source, target)
            txn_ids.append(str(edge["txn_id"]))
            dates.append(edge.get("date"))
            amounts.append(float(edge["amount"]))

        found.append(
            BankCycle(
                accounts=canonical,
                transaction_ids=tuple(txn_ids),
                dates=tuple(dates),
                amounts=tuple(amounts),
                date_span_days=_date_span_days(dates),
            )
        )

        if len(found) >= max_cycles:
            break

    found.sort(key=lambda cycle: (len(cycle.accounts), cycle.accounts, cycle.transaction_ids))
    return found
