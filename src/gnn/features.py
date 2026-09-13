"""Deterministic low-level features for the heterogeneous discovery graph.

These features describe activity and relationships.  They intentionally avoid
forensic conclusions such as ``is_kickback`` or ``is_round_trip`` so a future
GNN cannot merely relearn labels that deterministic detectors already decided.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd
import torch


VENDOR_FEATURES = (
    "registered_vendor",
    "invoice_count",
    "invoice_total",
    "invoice_mean",
    "invoice_std",
    "invoice_active_span_days",
    "po_count",
    "po_total",
    "contract_count",
    "contract_total",
    "ledger_entry_count",
    "ledger_debit_total",
    "ledger_credit_total",
    "efos_match",
    "bank_total_in",
    "bank_total_out",
    "bank_txn_count_in",
    "bank_txn_count_out",
    "bank_unique_counterparties",
)

EMPLOYEE_FEATURES = (
    "bank_total_in",
    "bank_total_out",
    "bank_txn_count_in",
    "bank_txn_count_out",
    "bank_unique_counterparties",
    "vendor_owned_counterparties",
)

ACCOUNT_FEATURES = (
    "total_in",
    "total_out",
    "count_in",
    "count_out",
    "unique_senders",
    "unique_receivers",
    "mean_amount_in",
    "mean_amount_out",
    "std_amount",
    "max_abs_amount",
    "vendor_owner_count",
    "employee_owner_count",
)

BINARY_FEATURES = {
    "vendor": {"registered_vendor", "efos_match"},
    "employee": set(),
    "account": set(),
}


FORBIDDEN_FORENSIC_TOKENS = (
    "fraud",
    "kickback",
    "round_trip",
    "roundtripping",
    "threshold_splitting",
    "phantom_vendor",
    "revenue_inflation",
    "suspicious",
)


def _safe_float(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if np.isfinite(number) else 0.0


def rows_to_tensor(
    rows: Iterable[dict[str, float]],
    feature_names: tuple[str, ...],
    *,
    binary_features: set[str] | None = None,
) -> torch.Tensor:
    """Create deterministic, finite, peer-scaled features.

    Non-binary features receive a signed ``log1p`` transform followed by a
    robust median/IQR scaling *within the same node type*.  Constant columns
    safely map to zero instead of producing NaN/Inf.  Binary flags remain 0/1.
    """

    row_list = list(rows)
    binary = binary_features or set()

    if not row_list:
        return torch.empty((0, len(feature_names)), dtype=torch.float32)

    matrix = np.asarray(
        [[_safe_float(row.get(name, 0.0)) for name in feature_names] for row in row_list],
        dtype=np.float64,
    )

    for column, name in enumerate(feature_names):
        values = matrix[:, column]
        if name in binary:
            matrix[:, column] = np.where(values > 0.0, 1.0, 0.0)
            continue

        transformed = np.sign(values) * np.log1p(np.abs(values))
        median = float(np.median(transformed))
        q25, q75 = np.percentile(transformed, [25.0, 75.0])
        scale = float(q75 - q25)
        if not np.isfinite(scale) or scale <= 1e-12:
            scale = 1.0
        matrix[:, column] = (transformed - median) / scale

    matrix[~np.isfinite(matrix)] = 0.0
    return torch.tensor(matrix, dtype=torch.float32)


def assert_low_level_feature_contract(feature_names: dict[str, tuple[str, ...]]) -> None:
    for node_type, names in feature_names.items():
        for name in names:
            lowered = name.lower()
            if any(token in lowered for token in FORBIDDEN_FORENSIC_TOKENS):
                raise ValueError(
                    f"Forensic conclusion leaked into {node_type} model features: {name}"
                )
