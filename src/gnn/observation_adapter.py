"""Convert stable GNN discovery candidates into shared Observation objects.

Neural scores remain discovery signals. EvidenceRefs point only to concrete
estate records; local feature context is descriptive and must not be presented
as a causal neural explanation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.core.estate import EstateRepository
from src.core.models import EvidenceRef, Observation
from src.gnn.contracts import GNNGraphBundle
from src.gnn.discovery import GNNEnsembleResult, StableGNNNodeScore


@dataclass(frozen=True)
class GNNObservationPolicy:
    """Transparent discovery policy, never a legal or evidentiary standard."""

    min_median_relative_score: float = 0.85
    max_score_iqr: float = 0.25
    min_peer_count: int = 5
    max_per_node_type: int = 3
    max_evidence_refs: int = 12
    top_feature_context: int = 5

    def validate(self) -> None:
        if not 0.0 <= self.min_median_relative_score <= 1.0:
            raise ValueError("min_median_relative_score must be in [0, 1]")
        if not 0.0 <= self.max_score_iqr <= 1.0:
            raise ValueError("max_score_iqr must be in [0, 1]")
        if self.min_peer_count < 2:
            raise ValueError("min_peer_count must be >= 2")
        if self.max_per_node_type < 1:
            raise ValueError("max_per_node_type must be >= 1")
        if self.max_evidence_refs < 1:
            raise ValueError("max_evidence_refs must be >= 1")
        if self.top_feature_context < 1:
            raise ValueError("top_feature_context must be >= 1")


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _numeric(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if np.isfinite(number) else 0.0


def _observation_id(row: StableGNNNodeScore, seeds: tuple[int, ...]) -> str:
    raw = f"{row.node_type}|{row.entity_id}|{','.join(map(str, seeds))}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()
    return f"OBS-GNN-{digest}"


def _entity_token(node_type: str, entity_id: str) -> str:
    if node_type == "vendor":
        return f"RFC:{entity_id}"
    if node_type == "employee":
        return f"EMP:{entity_id}"
    if node_type == "account":
        return f"CLABE:{entity_id}"
    raise ValueError(f"Unsupported GNN node type: {node_type}")


def select_observation_candidates(
    bundle: GNNGraphBundle,
    ensemble: GNNEnsembleResult,
    policy: GNNObservationPolicy | None = None,
) -> tuple[StableGNNNodeScore, ...]:
    """Select conservative discovery candidates with explicit heuristics.

    These thresholds control investigative workload only. Passing them is not
    evidence of fraud; failing them does not establish that an entity is normal.
    """

    policy = policy or GNNObservationPolicy()
    policy.validate()
    by_type: dict[str, list[StableGNNNodeScore]] = {}
    for row in ensemble.node_scores:
        peer_count = len(bundle.node_ids.get(row.node_type, ()))
        if peer_count < policy.min_peer_count:
            continue
        if row.median_relative_score < policy.min_median_relative_score:
            continue
        if row.score_iqr > policy.max_score_iqr:
            continue
        by_type.setdefault(row.node_type, []).append(row)

    selected: list[StableGNNNodeScore] = []
    for node_type in sorted(by_type):
        rows = sorted(
            by_type[node_type],
            key=lambda row: (-row.median_relative_score, row.score_iqr, row.entity_id),
        )
        selected.extend(rows[: policy.max_per_node_type])
    selected.sort(
        key=lambda row: (-row.median_relative_score, row.score_iqr, row.node_type, row.entity_id)
    )
    return tuple(selected)


def _sort_records(
    frame: pd.DataFrame,
    *,
    id_column: str,
    amount_column: str | None = None,
) -> pd.DataFrame:
    if frame.empty or id_column not in frame.columns:
        return frame.iloc[0:0]
    work = frame.copy()
    work["_id"] = work[id_column].map(_text)
    work = work[work["_id"] != ""]
    if amount_column and amount_column in work.columns:
        work["_salience"] = work[amount_column].map(lambda value: abs(_numeric(value)))
        return work.sort_values(["_salience", "_id"], ascending=[False, True])
    return work.sort_values("_id")


def _refs_from_frame(
    frame: pd.DataFrame,
    *,
    source_table: str,
    id_column: str,
    amount_column: str | None = None,
) -> list[EvidenceRef]:
    ordered = _sort_records(frame, id_column=id_column, amount_column=amount_column)
    return [
        EvidenceRef(source_table=source_table, record_id=_text(record_id))
        for record_id in ordered[id_column].tolist()
        if _text(record_id)
    ]


def _candidate_evidence_groups(
    frames: dict[str, pd.DataFrame],
    row: StableGNNNodeScore,
) -> list[list[EvidenceRef]]:
    """Return source-diverse local record groups associated with one entity."""

    groups: list[list[EvidenceRef]] = []
    if row.node_type == "vendor":
        rfc = row.entity_id
        vendors = frames["vendors"]
        vendor_rows = vendors[vendors["rfc"].map(_text) == rfc]
        groups.append(_refs_from_frame(vendor_rows, source_table="vendors", id_column="rfc"))

        efos = frames["efos_list"]
        groups.append(
            _refs_from_frame(
                efos[efos["rfc"].map(_text) == rfc],
                source_table="efos_list",
                id_column="rfc",
            )
        )

        invoices = frames["invoices"]
        inv_rows = invoices[invoices["issuer_rfc"].map(_text) == rfc]
        groups.append(
            _refs_from_frame(
                inv_rows,
                source_table="invoices",
                id_column="uuid",
                amount_column="total",
            )
        )

        pos = frames["purchase_orders"]
        groups.append(
            _refs_from_frame(
                pos[pos["vendor_rfc"].map(_text) == rfc],
                source_table="purchase_orders",
                id_column="po_id",
                amount_column="amount",
            )
        )

        contracts = frames["contracts"]
        groups.append(
            _refs_from_frame(
                contracts[contracts["vendor_rfc"].map(_text) == rfc],
                source_table="contracts",
                id_column="contract_id",
                amount_column="value",
            )
        )

        invoice_ids = {
            _text(value)
            for value in inv_rows.get("uuid", pd.Series(dtype=object)).tolist()
            if _text(value)
        }
        ledger = frames["ledger"]
        ledger_rows = ledger[ledger["invoice_uuid"].map(_text).isin(invoice_ids)] if invoice_ids else ledger.iloc[0:0]
        groups.append(
            _refs_from_frame(
                ledger_rows,
                source_table="ledger",
                id_column="entry_id",
            )
        )

        clabes = {
            _text(value)
            for value in vendor_rows.get("bank_clabe", pd.Series(dtype=object)).tolist()
            if _text(value)
        }
        bank = frames["bank_txns"]
        bank_rows = (
            bank[
                bank["from_clabe"].map(_text).isin(clabes)
                | bank["to_clabe"].map(_text).isin(clabes)
            ]
            if clabes
            else bank.iloc[0:0]
        )
        groups.append(
            _refs_from_frame(
                bank_rows,
                source_table="bank_txns",
                id_column="txn_id",
                amount_column="amount",
            )
        )

    elif row.node_type == "employee":
        emp_id = row.entity_id
        employees = frames["employees"]
        emp_rows = employees[employees["emp_id"].map(_text) == emp_id]
        groups.append(
            _refs_from_frame(emp_rows, source_table="employees", id_column="emp_id")
        )
        clabes = {
            _text(value)
            for value in emp_rows.get("bank_clabe", pd.Series(dtype=object)).tolist()
            if _text(value)
        }
        bank = frames["bank_txns"]
        bank_rows = (
            bank[
                bank["from_clabe"].map(_text).isin(clabes)
                | bank["to_clabe"].map(_text).isin(clabes)
            ]
            if clabes
            else bank.iloc[0:0]
        )
        groups.append(
            _refs_from_frame(
                bank_rows,
                source_table="bank_txns",
                id_column="txn_id",
                amount_column="amount",
            )
        )

    elif row.node_type == "account":
        clabe = row.entity_id
        vendors = frames["vendors"]
        groups.append(
            _refs_from_frame(
                vendors[vendors["bank_clabe"].map(_text) == clabe],
                source_table="vendors",
                id_column="rfc",
            )
        )
        employees = frames["employees"]
        groups.append(
            _refs_from_frame(
                employees[employees["bank_clabe"].map(_text) == clabe],
                source_table="employees",
                id_column="emp_id",
            )
        )
        bank = frames["bank_txns"]
        bank_rows = bank[
            (bank["from_clabe"].map(_text) == clabe)
            | (bank["to_clabe"].map(_text) == clabe)
        ]
        groups.append(
            _refs_from_frame(
                bank_rows,
                source_table="bank_txns",
                id_column="txn_id",
                amount_column="amount",
            )
        )
    else:
        raise ValueError(f"Unsupported GNN node type: {row.node_type}")

    return [group for group in groups if group]


def _unique_refs(groups: list[list[EvidenceRef]]) -> list[EvidenceRef]:
    unique: list[EvidenceRef] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for ref in group:
            key = (ref.source_table, ref.record_id)
            if key in seen:
                continue
            seen.add(key)
            unique.append(ref)
    return unique


def _round_robin_refs(groups: list[list[EvidenceRef]], limit: int) -> list[EvidenceRef]:
    """Cap context while avoiding one high-volume table crowding out all others."""

    selected: list[EvidenceRef] = []
    seen: set[tuple[str, str]] = set()
    position = 0
    while len(selected) < limit:
        added = False
        for group in groups:
            if position >= len(group):
                continue
            ref = group[position]
            key = (ref.source_table, ref.record_id)
            if key not in seen:
                seen.add(key)
                selected.append(ref)
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        position += 1
    return selected


def _feature_context(
    bundle: GNNGraphBundle,
    row: StableGNNNodeScore,
    *,
    top_n: int,
) -> tuple[dict[str, float], list[dict[str, float | str]]]:
    index = bundle.entity_index(row.node_type, row.entity_id)
    raw = dict(bundle.raw_features[row.node_type][index])
    normalized = bundle.data[row.node_type].x[index].detach().cpu().numpy().astype(float)
    names = bundle.feature_names[row.node_type]
    ordered = sorted(
        range(len(names)),
        key=lambda position: (-abs(float(normalized[position])), names[position]),
    )[:top_n]
    peer_context = [
        {
            "feature": names[position],
            "normalized_peer_value": float(normalized[position]),
            "raw_value": float(raw.get(names[position], 0.0)),
        }
        for position in ordered
    ]
    return raw, peer_context


def _recommended_checks(node_type: str) -> list[str]:
    if node_type == "vendor":
        return [
            "Compare the vendor's invoices, purchase orders, contracts and bank activity using deterministic queries.",
            "Inspect exact bank transaction paths and ownership records before proposing any scheme hypothesis.",
        ]
    if node_type == "employee":
        return [
            "Verify the employee-account association and inspect exact counterparties and transaction records.",
            "Compare transfer timing with vendor documents and the employee's business role before proposing a hypothesis.",
        ]
    return [
        "Verify account ownership and inspect exact bank transactions and bounded graph paths.",
        "Use deterministic records to distinguish unusual topology from legitimate treasury or payment behavior.",
    ]


def build_gnn_observations(
    estate: EstateRepository,
    bundle: GNNGraphBundle,
    ensemble: GNNEnsembleResult,
    *,
    policy: GNNObservationPolicy | None = None,
) -> list[Observation]:
    policy = policy or GNNObservationPolicy()
    policy.validate()
    candidates = select_observation_candidates(bundle, ensemble, policy)
    frames = {
        table: estate.table_df(table)
        for table in (
            "vendors",
            "employees",
            "bank_txns",
            "invoices",
            "ledger",
            "purchase_orders",
            "contracts",
            "efos_list",
        )
    }

    observations: list[Observation] = []
    for row in candidates:
        peer_count = len(bundle.node_ids[row.node_type])
        groups = _candidate_evidence_groups(frames, row)
        all_refs = _unique_refs(groups)
        # A discovery signal without any concrete estate provenance must not open
        # an investigation merely because the neural model produced a score.
        if not all_refs:
            continue
        refs = _round_robin_refs(groups, policy.max_evidence_refs)
        raw_features, peer_context = _feature_context(
            bundle, row, top_n=policy.top_feature_context
        )
        entity = _entity_token(row.node_type, row.entity_id)
        observations.append(
            Observation(
                observation_id=_observation_id(row, ensemble.seeds),
                detector_name="gnn_discovery",
                signal_type="gnn_relational_anomaly",
                entities=[entity],
                statement=(
                    f"The unsupervised heterogeneous graph model ranked {entity} "
                    f"as relatively unusual among {row.node_type} peers across "
                    f"{len(ensemble.seeds)} deterministic training seeds."
                ),
                evidence=refs,
                facts={
                    "node_type": row.node_type,
                    "entity_id": row.entity_id,
                    "peer_count": peer_count,
                    "training_seeds": list(ensemble.seeds),
                    "per_seed_relative_scores": list(row.per_seed_relative_scores),
                    "median_relative_score": row.median_relative_score,
                    "score_iqr": row.score_iqr,
                    "score_stddev": row.score_stddev,
                    "score_min": row.score_min,
                    "score_max": row.score_max,
                    "median_feature_percentile": row.median_feature_percentile,
                    "median_edge_percentile": row.median_edge_percentile,
                    "raw_feature_snapshot": raw_features,
                    "largest_peer_scaled_feature_deviations": peer_context,
                    "evidence_ref_count_total": len(all_refs),
                    "evidence_ref_count_included": len(refs),
                    "evidence_selection_semantics": (
                        "Associated local source records are selected deterministically "
                        "and round-robin across source groups; amount-bearing records "
                        "are ordered by absolute amount. This is context, not neural attribution."
                    ),
                    "discovery_policy": {
                        "min_median_relative_score": policy.min_median_relative_score,
                        "max_score_iqr": policy.max_score_iqr,
                        "min_peer_count": policy.min_peer_count,
                        "max_per_node_type": policy.max_per_node_type,
                    },
                },
                score=row.median_relative_score,
                score_semantics=row.score_semantics,
                limitations=[
                    "The score is relative to peers of the same node type in this estate and is not a calibrated fraud probability.",
                    "Agreement across training seeds measures optimization stability, not out-of-sample or real-world validity.",
                    "Local feature deviations and EvidenceRefs are factual context associated with the entity, not causal neural attribution.",
                    "The discovery policy thresholds are workload heuristics, not legal, policy, or evidentiary standards.",
                    "This observation does not establish a scheme type, intent, or fraud.",
                ],
                legitimate_alternatives=[
                    "Legitimate business scale or role differences may make the entity structurally unusual.",
                    "Incomplete or stale ownership/document data may distort the graph neighborhood.",
                    "A rare but legitimate payment or procurement process may produce the same relative pattern.",
                ],
                recommended_checks=_recommended_checks(row.node_type),
            )
        )

    return observations
