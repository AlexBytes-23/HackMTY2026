"""
Módulo de Detección Tabular y Conciliación Determinista
Rama 3: Análisis Tabular / Probabilístico
Ubicación: src/detectors/tabulares.py
"""

from typing import Optional
import math
import json
import pandas as pd
from pydantic import BaseModel


# ==============================================================================
# 1. Contrato Pydantic: TabularObservation
# ==============================================================================
class TabularObservation(BaseModel):
    rule_or_model: str
    facts: dict
    anomaly_score_optional: Optional[float] = None
    hypotheses_optional: Optional[list[str]] = None
    provenance: list[str]


# ==============================================================================
# 2. Detector 1: Reconciliación Determinista Triple (Invoices ↔ Payments ↔ Ledger)
# ==============================================================================
def check_invoice_payment_mismatch(
    invoices: pd.DataFrame,
    payments: pd.DataFrame,
    ledger: Optional[pd.DataFrame] = None,
    tolerance: float = 0.01
) -> list[TabularObservation]:
    """
    Cruza determinísticamente facturas, pagos y libro mayor contable (ledger) por invoice_id.
    Calcula las diferencias exactas y retorna una lista de TabularObservation por cada
    descuadre identificado (sobrepagos, pagos parciales, o alteraciones contables en el ledger).
    """
    observations: list[TabularObservation] = []

    # 1. Agregación determinista de pagos por invoice_id
    payments_agg = (
        payments.groupby("invoice_id", as_index=False)
        .agg(
            total_paid=("payment_amount", "sum"),
            payment_ids=("payment_id", list)
        )
    )

    # 2. Cruce inicial: Invoices ↔ Payments
    merged = pd.merge(
        invoices,
        payments_agg,
        on="invoice_id",
        how="outer"
    )

    # 3. Cruce con Ledger si se proporciona (Cruce Triple)
    if ledger is not None:
        ledger_agg = (
            ledger.groupby("invoice_id", as_index=False)
            .agg(
                total_ledger=("recorded_amount", "sum"),
                ledger_tx_ids=("transaction_id", list)
            )
        )
        merged = pd.merge(
            merged,
            ledger_agg,
            on="invoice_id",
            how="outer"
        )
        merged["total_ledger"] = merged["total_ledger"].fillna(0.0)
    else:
        merged["total_ledger"] = 0.0
        merged["ledger_tx_ids"] = [[] for _ in range(len(merged))]

    # Relleno de nulos para aritmética exacta
    merged["total_amount"] = merged["total_amount"].fillna(0.0)
    merged["total_paid"] = merged["total_paid"].fillna(0.0)

    # 4. Cálculo de diferencias deterministas
    merged["diff_payment_invoice"] = (merged["total_paid"] - merged["total_amount"]).round(2)
    merged["diff_ledger_payment"] = (merged["total_ledger"] - merged["total_paid"]).round(2)
    merged["diff_ledger_invoice"] = (merged["total_ledger"] - merged["total_amount"]).round(2)

    for _, row in merged.iterrows():
        inv_id = str(row["invoice_id"])
        invoiced = float(row["total_amount"])
        paid = float(row["total_paid"])
        ledger_amt = float(row["total_ledger"])
        diff_pi = float(row["diff_payment_invoice"])
        diff_lp = float(row["diff_ledger_payment"])
        diff_li = float(row["diff_ledger_invoice"])

        pay_ids = row["payment_ids"] if isinstance(row["payment_ids"], list) else []
        tx_ids = row["ledger_tx_ids"] if isinstance(row["ledger_tx_ids"], list) else []

        has_pi_mismatch = abs(diff_pi) > tolerance
        has_ledger_mismatch = (ledger is not None) and (abs(diff_lp) > tolerance or abs(diff_li) > tolerance)

        # Si no hay ninguna discrepancia matemática, ignorar (cuadre perfecto)
        if not has_pi_mismatch and not has_ledger_mismatch:
            continue

        facts = {
            "invoice_id": inv_id,
            "invoiced_amount": invoiced,
            "paid_amount": paid,
            "ledger_amount": ledger_amt,
            "diff_payment_invoice": diff_pi,
            "diff_ledger_payment": diff_lp,
            "diff_ledger_invoice": diff_li,
            "currency": "MXN"
        }

        # Construcción de provenance estricta con prefijos de entidad
        provenance = []
        if invoiced > 0.0:
            provenance.append(f"invoice:{inv_id}")
        for pid in pay_ids:
            provenance.append(f"payment:{pid}")
        for txid in tx_ids:
            provenance.append(f"ledger:{txid}")

        # CASO 1: ESCENARIO MALICIOSO / DISCREPANCIA CONTABLE
        if not has_pi_mismatch and has_ledger_mismatch:
            if diff_lp < 0.0:
                facts["mismatch_type"] = "LEDGER_UNDER_RECORDING"
                facts["hidden_amount"] = abs(diff_lp)
                anomaly_score = 0.90  # Alta criticidad forense
                hypotheses = [
                    "Sub-registro intencional en libro mayor contable respecto al egreso bancario real (posible alteración contable / desvío de fondos).",
                    "Error material de captura u omisión de póliza contable complementaria en el ERP.",
                    "Registro contable neto omitiendo impuestos retenidos o cargos bancarios adicionales.",
                    "Póliza contable registrada en una cuenta contable secundaria o pendiente de conciliación mensual."
                ]
            else:
                facts["mismatch_type"] = "LEDGER_OVER_RECORDING"
                facts["ledger_excess"] = diff_lp
                anomaly_score = 0.80
                hypotheses = [
                    "Sobre-registro contable por encima del flujo bancario real (posible inflación artificial de gastos / deducciones).",
                    "Duplicidad de asiento contable en el libro mayor para una misma póliza de egreso.",
                    "Registro de pasivo contable pendiente de pago no conciliado con el banco."
                ]

        # CASO 2: DISCREPANCIA ENTRE FACTURA Y PAGO
        elif invoiced == 0.0 and paid > 0.0:
            facts["mismatch_type"] = "ORPHAN_PAYMENT"
            anomaly_score = 0.85
            hypotheses = [
                "Pago anticipado efectuado con anterioridad a la emisión formal del CFDI.",
                "Referencia o identificador de factura erróneo o truncado en tesorería.",
                "Gasto operativo o comisión clasificado erróneamente con formato de factura."
            ]
        elif paid == 0.0 and invoiced > 0.0:
            facts["mismatch_type"] = "UNPAID_INVOICE"
            anomaly_score = 0.40
            hypotheses = [
                "Factura vigente pendiente de liquidación según crédito comercial.",
                "Cancelación de factura no sincronizada con el libro contable."
            ]
        elif diff_pi > 0.0:
            facts["mismatch_type"] = "OVERPAYMENT"
            facts["overpaid_amount"] = diff_pi
            ratio = min(diff_pi / invoiced, 1.0) if invoiced > 0 else 1.0
            anomaly_score = round(0.5 + (0.5 * ratio), 2)
            hypotheses = [
                "Posible nota de crédito, descuento o devolución posterior pendiente de conciliar.",
                "Pago aplicado cubriendo saldos acumulados de facturas anteriores.",
                "Error operativo de doble dispersión bancaria."
            ]
        else:
            facts["mismatch_type"] = "UNDERPAYMENT"
            facts["pending_balance"] = abs(diff_pi)
            ratio = min(abs(diff_pi) / invoiced, 1.0) if invoiced > 0 else 1.0
            anomaly_score = round(0.2 + (0.3 * ratio), 2)
            hypotheses = [
                "Esquema de pago en parcialidades con complemento de pago diferido pendiente.",
                "Retención de garantías o penalizaciones comerciales aplicadas al proveedor.",
                "Retención fiscal aplicable (IVA/ISR) no desglosada en el registro de pago."
            ]

        observation = TabularObservation(
            rule_or_model="triple_reconciliation_invoice_payment_ledger",
            facts=facts,
            anomaly_score_optional=anomaly_score,
            hypotheses_optional=hypotheses,
            provenance=provenance
        )
        observations.append(observation)

    return observations


# ==============================================================================
# 3. Detector 2: Filtro de Entropía de Shannon (Fase 1)
# ==============================================================================
def check_shannon_entropy_anomaly(
    df: pd.DataFrame,
    column_name: str,
    group_by_column: str,
    id_column: str = "transaction_id",
    entropy_threshold: float = 0.35,
    min_samples: int = 3
) -> list[TabularObservation]:
    """
    Calcula la Entropía de Shannon H(X) sobre una columna cuantitativa agrupada por un identificador.
    Normaliza la entropía H_norm = H(X) / log2(N).
    Detecta colapsos anormales de entropía (H_norm < entropy_threshold), característicos de
    patrones estructurados / fraccionados (smurfing, montos fijos idénticos repetitivos).
    """
    observations: list[TabularObservation] = []

    for group_val, group_df in df.groupby(group_by_column):
        n_samples = len(group_df)
        if n_samples < min_samples:
            continue

        # Distribución de frecuencias empíricas P(x)
        counts = group_df[column_name].value_counts()
        probabilities = counts / n_samples

        # Cálculo de Entropía de Shannon: H(X) = -sum(P(x) * log2(P(x)))
        raw_h = -sum(p * math.log2(p) for p in probabilities if p > 0.0)
        shannon_h = abs(raw_h) if abs(raw_h) > 1e-12 else 0.0

        # Máxima entropía teórica para N observaciones: H_max = log2(N)
        max_h = math.log2(n_samples) if n_samples > 1 else 1.0

        # Entropía normalizada entre 0.0 (colapso total) y 1.0 (máxima variabilidad)
        norm_h = (shannon_h / max_h) if max_h > 0 else 1.0
        norm_h = round(max(0.0, min(1.0, norm_h)), 4)

        # Detección: Colapso de variabilidad por debajo del umbral
        if norm_h < entropy_threshold:
            dominant_val = counts.index[0]
            dominant_count = int(counts.iloc[0])
            dominant_ratio = round(dominant_count / n_samples, 4)

            # Extraer identificadores exactos para trazabilidad forense
            if id_column in group_df.columns:
                provenance = [f"{id_column}:{tid}" for tid in group_df[id_column]]
            else:
                provenance = [f"row_index:{idx}" for idx in group_df.index]

            facts = {
                "group_by_column": group_by_column,
                "group_by_value": str(group_val),
                "evaluated_column": column_name,
                "sample_size": n_samples,
                "unique_values_count": int(len(counts)),
                "shannon_entropy": round(shannon_h, 4),
                "max_possible_entropy": round(max_h, 4),
                "normalized_entropy": round(norm_h, 4),
                "entropy_threshold": entropy_threshold,
                "dominant_value": float(dominant_val) if isinstance(dominant_val, (int, float)) else str(dominant_val),
                "dominant_value_count": dominant_count,
                "dominant_value_ratio": dominant_ratio,
                "mismatch_type": "ENTROPY_COLLAPSE_STRUCTURED_PATTERN"
            }

            # Score de anomalía inversamente proporcional a la entropía normalizada
            anomaly_score = round(1.0 - norm_h, 2)

            hypotheses = [
                f"Posible estructuración o fraccionamiento deliberado (smurfing) mediante {dominant_count} transacciones idénticas de ${dominant_val:,.2f} para evadir umbrales regulatorios de control o comités de aprobación.",
                "Dispersión recurrente automatizada mediante scripts programados con parámetros fijos en tesorería.",
                "Facturación legítima de iguala periódica, suscripción o tarifa fija de servicios contratados.",
                "Fraccionamiento artificial de contratos marco para eludir licitaciones o procesos competitivos de compras."
            ]

            obs = TabularObservation(
                rule_or_model="check_shannon_entropy_anomaly",
                facts=facts,
                anomaly_score_optional=anomaly_score,
                hypotheses_optional=hypotheses,
                provenance=provenance
            )
            observations.append(obs)

    return observations


# ==============================================================================
# 4. Mocks de Datos
# ==============================================================================
# --- Mock de Reconciliación Triple ---
invoices_df = pd.DataFrame([
    {"invoice_id": "INV-001", "issuer_rfc": "PROV850101AAA", "total_amount": 10000.00, "issue_date": "2024-01-15"},
    {"invoice_id": "INV-002", "issuer_rfc": "SERV920312BBB", "total_amount": 25000.00, "issue_date": "2024-01-20"},
    {"invoice_id": "INV-003", "issuer_rfc": "CONS880905CCC", "total_amount": 18500.00, "issue_date": "2024-02-01"},
    {"invoice_id": "INV-004", "issuer_rfc": "OPER990714DDD", "total_amount": 30000.00, "issue_date": "2024-02-10"}
])

payments_df = pd.DataFrame([
    {"payment_id": "PAY-001", "invoice_id": "INV-001", "payment_amount": 10000.00, "payment_date": "2024-01-18"},
    {"payment_id": "PAY-002", "invoice_id": "INV-002", "payment_amount": 30000.00, "payment_date": "2024-01-25"},
    {"payment_id": "PAY-003", "invoice_id": "INV-003", "payment_amount": 12000.00, "payment_date": "2024-02-10"},
    {"payment_id": "PAY-004", "invoice_id": "INV-004", "payment_amount": 30000.00, "payment_date": "2024-02-12"}
])

ledger_df = pd.DataFrame([
    {"transaction_id": "TX-001", "invoice_id": "INV-001", "recorded_amount": 10000.00},
    {"transaction_id": "TX-002", "invoice_id": "INV-002", "recorded_amount": 30000.00},
    {"transaction_id": "TX-003", "invoice_id": "INV-003", "recorded_amount": 12000.00},
    {"transaction_id": "TX-004", "invoice_id": "INV-004", "recorded_amount": 25000.00}
])

# --- Mock de Entropía: Transacciones por Proveedor ---
vendor_transactions_df = pd.DataFrame([
    # Proveedor A: Montos variables normales (alta entropía de dispersión)
    {"transaction_id": "TX-A01", "vendor_id": "PROV-A-LEGIT", "amount": 14250.00, "date": "2024-01-10"},
    {"transaction_id": "TX-A02", "vendor_id": "PROV-A-LEGIT", "amount": 8300.00,  "date": "2024-01-17"},
    {"transaction_id": "TX-A03", "vendor_id": "PROV-A-LEGIT", "amount": 21500.00, "date": "2024-01-24"},
    {"transaction_id": "TX-A04", "vendor_id": "PROV-A-LEGIT", "amount": 4900.00,  "date": "2024-02-02"},
    {"transaction_id": "TX-A05", "vendor_id": "PROV-A-LEGIT", "amount": 17800.00, "date": "2024-02-11"},
    {"transaction_id": "TX-A06", "vendor_id": "PROV-A-LEGIT", "amount": 11650.00, "date": "2024-02-18"},

    # Proveedor B: Montos idénticos coordinados de $9,999.00 (colapso de entropía / estructuración)
    {"transaction_id": "TX-B01", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "date": "2024-01-12"},
    {"transaction_id": "TX-B02", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "date": "2024-01-15"},
    {"transaction_id": "TX-B03", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "date": "2024-01-19"},
    {"transaction_id": "TX-B04", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "date": "2024-01-23"},
    {"transaction_id": "TX-B05", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "date": "2024-01-28"},
    {"transaction_id": "TX-B06", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "date": "2024-02-03"}
])


# ==============================================================================
# 5. Ejecución de Ambos Detectores
# ==============================================================================
if __name__ == "__main__":
    # Ejecutar Detector 1: Reconciliación Determinista
    recon_obs = check_invoice_payment_mismatch(invoices_df, payments_df, ledger_df)

    # Ejecutar Detector 2: Filtro de Entropía de Shannon
    entropy_obs = check_shannon_entropy_anomaly(
        df=vendor_transactions_df,
        column_name="amount",
        group_by_column="vendor_id",
        id_column="transaction_id"
    )

    # Consolidar todas las observaciones tabulares
    all_observations = recon_obs + entropy_obs

    # Imprimir en formato JSON estricto
    json_output = json.dumps([obs.model_dump() for obs in all_observations], indent=2, ensure_ascii=False)
    print(json_output)
