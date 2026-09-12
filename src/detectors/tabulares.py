"""
Módulo de Detección Tabular y Conciliación Determinista
Rama 3: Análisis Tabular / Probabilístico
Ubicación: src/detectors/tabulares.py
"""

from typing import Optional, List
import math
import json
import pandas as pd
from pydantic import BaseModel
from sklearn.ensemble import IsolationForest


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

    payments_agg = (
        payments.groupby("invoice_id", as_index=False)
        .agg(
            total_paid=("payment_amount", "sum"),
            payment_ids=("payment_id", list)
        )
    )

    merged = pd.merge(
        invoices,
        payments_agg,
        on="invoice_id",
        how="outer"
    )

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

    merged["total_amount"] = merged["total_amount"].fillna(0.0)
    merged["total_paid"] = merged["total_paid"].fillna(0.0)

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

        provenance = []
        if invoiced > 0.0:
            provenance.append(f"invoice:{inv_id}")
        for pid in pay_ids:
            provenance.append(f"payment:{pid}")
        for txid in tx_ids:
            provenance.append(f"ledger:{txid}")

        if not has_pi_mismatch and has_ledger_mismatch:
            if diff_lp < 0.0:
                facts["mismatch_type"] = "LEDGER_UNDER_RECORDING"
                facts["hidden_amount"] = abs(diff_lp)
                anomaly_score = 0.90
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
    Detecta colapsos anormales de entropía (H_norm < entropy_threshold).
    """
    observations: list[TabularObservation] = []

    for group_val, group_df in df.groupby(group_by_column):
        n_samples = len(group_df)
        if n_samples < min_samples:
            continue

        counts = group_df[column_name].value_counts()
        probabilities = counts / n_samples

        raw_h = -sum(p * math.log2(p) for p in probabilities if p > 0.0)
        shannon_h = abs(raw_h) if abs(raw_h) > 1e-12 else 0.0
        max_h = math.log2(n_samples) if n_samples > 1 else 1.0
        norm_h = (shannon_h / max_h) if max_h > 0 else 1.0
        norm_h = round(max(0.0, min(1.0, norm_h)), 4)

        if norm_h < entropy_threshold:
            dominant_val = counts.index[0]
            dominant_count = int(counts.iloc[0])
            dominant_ratio = round(dominant_count / n_samples, 4)

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
# 4. Detector 3: Filtro de Anomalías Temporales (Fase 2)
# ==============================================================================
def check_temporal_anomalies(
    df: pd.DataFrame,
    timestamp_col: str,
    vendor_col: str,
    id_column: str = "transaction_id",
    night_start_hour: int = 23,
    night_end_hour: int = 5,
    burst_window_minutes: float = 5.0,
    burst_min_tx_count: int = 3
) -> list[TabularObservation]:
    """
    Detecta anomalías temporales en transacciones:
    1. Filtro de Horario (OFF_HOURS_EXECUTION): Horario nocturno (23:00 - 05:00) o fines de semana.
    2. Filtro de Ráfagas / Velocity Checks (HIGH_VELOCITY_BURST): Más de 3 transacciones de un mismo
       vendor en una ventana menor a 5 minutos.
    """
    observations: list[TabularObservation] = []

    df_temp = df.copy()
    df_temp[timestamp_col] = pd.to_datetime(df_temp[timestamp_col])

    # 4.1. Filtro de Horario (OFF_HOURS_EXECUTION)
    for idx, row in df_temp.iterrows():
        ts: pd.Timestamp = row[timestamp_col]
        vendor_id = str(row[vendor_col])
        tx_id = str(row[id_column]) if id_column in row else str(idx)
        amount = float(row["amount"]) if "amount" in row else None

        is_night = (ts.hour >= night_start_hour) or (ts.hour < night_end_hour)
        is_weekend = ts.dayofweek >= 5

        if is_night or is_weekend:
            reasons = []
            if is_night:
                reasons.append(f"Horario nocturno fuera de oficina ({ts.strftime('%H:%M:%S')} hrs)")
            if is_weekend:
                reasons.append(f"Fin de semana ({ts.day_name()})")

            anomaly_score = 0.80 if (is_night and is_weekend) else (0.70 if is_night else 0.60)

            facts = {
                "transaction_id": tx_id,
                "vendor_id": vendor_id,
                "timestamp": ts.isoformat(),
                "hour": int(ts.hour),
                "day_name": ts.day_name(),
                "is_night": bool(is_night),
                "is_weekend": bool(is_weekend),
                "amount": amount,
                "reasons": reasons,
                "currency": "MXN",
                "mismatch_type": "OFF_HOURS_EXECUTION"
            }

            hypotheses = [
                "Ejecución de dispersión no supervisada en horario inhábil para evadir controles operativos y monitoreo en tiempo real.",
                "Proceso batch nocturno o tarea cron programada legítima de liquidación automática contable.",
                "Adquisición de suministros o servicios de emergencia operativa autorizada fuera de horario habitual."
            ]

            obs = TabularObservation(
                rule_or_model="check_temporal_anomalies_off_hours",
                facts=facts,
                anomaly_score_optional=anomaly_score,
                hypotheses_optional=hypotheses,
                provenance=[f"{id_column}:{tx_id}"]
            )
            observations.append(obs)

    # 4.2. Filtro de Ráfagas / Velocity Checks (HIGH_VELOCITY_BURST)
    df_sorted = df_temp.sort_values(by=[vendor_col, timestamp_col])

    for vendor_id, group in df_sorted.groupby(vendor_col):
        n = len(group)
        if n <= burst_min_tx_count:
            continue

        timestamps = group[timestamp_col].tolist()
        tx_ids = group[id_column].tolist() if id_column in group.columns else list(group.index)
        amounts = group["amount"].tolist() if "amount" in group.columns else []

        i = 0
        while i < n:
            j = i
            while j < n and (timestamps[j] - timestamps[i]) <= pd.Timedelta(minutes=burst_window_minutes):
                j += 1

            burst_len = j - i
            if burst_len > burst_min_tx_count:
                burst_txs = tx_ids[i:j]
                burst_times = timestamps[i:j]
                duration_sec = (burst_times[-1] - burst_times[0]).total_seconds()
                burst_amounts = amounts[i:j] if amounts else []
                total_burst_amt = round(sum(burst_amounts), 2) if burst_amounts else 0.0

                facts = {
                    "vendor_id": str(vendor_id),
                    "transaction_count": burst_len,
                    "burst_threshold_count": burst_min_tx_count,
                    "window_start": burst_times[0].isoformat(),
                    "window_end": burst_times[-1].isoformat(),
                    "duration_seconds": round(duration_sec, 2),
                    "window_limit_minutes": burst_window_minutes,
                    "burst_amounts": [float(a) for a in burst_amounts] if burst_amounts else [],
                    "total_burst_amount": total_burst_amt,
                    "currency": "MXN",
                    "mismatch_type": "HIGH_VELOCITY_BURST"
                }

                anomaly_score = 0.85
                hypotheses = [
                    f"Dispersión acelerada mediante scripts automatizados o bots ({burst_len} transacciones en {duration_sec:.1f} segundos) para extracción rápida de fondos (velocity abuse).",
                    "Falla técnica o reintento concurrente descontrolado (retry storm / idempotency failure) en la pasarela bancaria.",
                    "Procesamiento masivo simultáneo de facturación acumulada cargada por lote en tesorería."
                ]

                obs = TabularObservation(
                    rule_or_model="check_temporal_anomalies_velocity_burst",
                    facts=facts,
                    anomaly_score_optional=anomaly_score,
                    hypotheses_optional=hypotheses,
                    provenance=[f"{id_column}:{tid}" for tid in burst_txs]
                )
                observations.append(obs)
                i = j
            else:
                i += 1

    return observations


# ==============================================================================
# 5. Detector 4: Isolation Forest Multidimensional (Fase 3)
# ==============================================================================
def check_isolation_forest_anomalies(
    df: pd.DataFrame,
    feature_cols: Optional[List[str]] = None,
    id_column: str = "transaction_id",
    contamination: float = 0.1,
    random_state: int = 42
) -> List[TabularObservation]:
    """
    Fase 3: Detector Multidimensional no lineal basado en Isolation Forest.
    Aprende la frontera sobre combinaciones numéricas (ej. amount, hour).
    Identifica registros anómalos multivariados (predicción == -1) y deriva un anomaly score
    normalizado a partir de decision_function.
    """
    observations: List[TabularObservation] = []

    if len(df) == 0:
        return observations

    df_features = df.copy()

    # Si timestamp está presente y se requiere 'hour', derivarla
    if "timestamp" in df_features.columns:
        df_features["hour"] = pd.to_datetime(df_features["timestamp"]).dt.hour

    # Seleccionar características numéricas a evaluar
    if feature_cols is None:
        feature_cols = [c for c in ["amount", "hour"] if c in df_features.columns]
        if not feature_cols:
            feature_cols = df_features.select_dtypes(include=["number"]).columns.tolist()

    if not feature_cols:
        return observations

    X = df_features[feature_cols].fillna(0.0)

    # Entrenar IsolationForest determinísticamente con semilla fija
    iso_forest = IsolationForest(
        contamination=contamination,
        random_state=random_state
    )
    predictions = iso_forest.fit_predict(X)
    raw_scores = iso_forest.decision_function(X)  # En scikit-learn, más negativo = más anómalo

    # Normalización del score de anomalía a rango [0.0, 1.0]
    min_s, max_s = float(raw_scores.min()), float(raw_scores.max())
    score_range = (max_s - min_s) if max_s > min_s else 1.0
    normalized_scores = 1.0 - ((raw_scores - min_s) / score_range)

    for idx, (pred, raw_s, norm_s) in enumerate(zip(predictions, raw_scores, normalized_scores)):
        if pred == -1:
            row = df_features.iloc[idx]
            tx_id = str(row[id_column]) if id_column in row else str(idx)

            facts = {
                "transaction_id": tx_id,
                "vendor_id": str(row["vendor_id"]) if "vendor_id" in row else None,
                "features_evaluated": feature_cols,
                "feature_values": {col: float(row[col]) for col in feature_cols},
                "decision_function_score": round(float(raw_s), 4),
                "contamination": contamination,
                "currency": "MXN",
                "mismatch_type": "MULTIDIMENSIONAL_ISOLATION_ANOMALY"
            }
            if "timestamp" in row:
                facts["timestamp"] = str(row["timestamp"])

            hypotheses = [
                "Transacción atípica multivariada en el espacio de características (combinación inusual de importe y horario respecto a la población de datos).",
                "Operación extraordinaria o fuera de catálogo que no se ajusta a los patrones de dispersión habituales.",
                "Posible error humano de captura o prueba de límites en el sistema bancario/ERP."
            ]

            obs = TabularObservation(
                rule_or_model="check_isolation_forest_anomalies",
                facts=facts,
                anomaly_score_optional=round(float(norm_s), 2),
                hypotheses_optional=hypotheses,
                provenance=[f"{id_column}:{tx_id}"]
            )
            observations.append(obs)

    return observations


# ==============================================================================
# 6. Punto de Entrada Unificado (Pipeline Tabular Completo)
# ==============================================================================
def run_all_tabular_detectors(
    invoices_df: pd.DataFrame,
    payments_df: pd.DataFrame,
    ledger_df: pd.DataFrame,
    transactions_df: pd.DataFrame
) -> List[TabularObservation]:
    """
    Punto de entrada unificado para la Rama 3: Análisis Tabular y Probabilístico.
    Ejecuta secuencialmente los 4 detectores de auditoría forense:
      1. Reconciliación Determinista Triple (Invoices ↔ Payments ↔ Ledger)
      2. Filtro de Entropía de Shannon (Colapso de variabilidad / Smurfing)
      3. Filtro de Anomalías Temporales (Horarios nocturnos, fin de semana y ráfagas)
      4. Isolation Forest (Anomalías multidimensionales no lineales)
    Retorna la lista consolidada de todas las observaciones bajo el contrato TabularObservation.
    """
    all_observations: List[TabularObservation] = []

    # 1. Reconciliación Determinista Triple
    recon_obs = check_invoice_payment_mismatch(invoices_df, payments_df, ledger_df)
    all_observations.extend(recon_obs)

    # 2. Filtro de Entropía de Shannon
    entropy_obs = check_shannon_entropy_anomaly(
        df=transactions_df,
        column_name="amount",
        group_by_column="vendor_id",
        id_column="transaction_id"
    )
    all_observations.extend(entropy_obs)

    # 3. Filtro de Anomalías Temporales (Horarios y Ráfagas)
    temporal_obs = check_temporal_anomalies(
        df=transactions_df,
        timestamp_col="timestamp",
        vendor_col="vendor_id",
        id_column="transaction_id"
    )
    all_observations.extend(temporal_obs)

    # 4. Isolation Forest Multidimensional
    iso_obs = check_isolation_forest_anomalies(
        df=transactions_df,
        feature_cols=["amount", "hour"],
        id_column="transaction_id",
        contamination=0.1,
        random_state=42
    )
    all_observations.extend(iso_obs)

    return all_observations


# ==============================================================================
# 7. Mocks de Datos
# ==============================================================================
# --- Mock 1: Reconciliación Triple (Invoices ↔ Payments ↔ Ledger) ---
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

# --- Mock 2: Transacciones con Marcas Temporales ---
vendor_transactions_df = pd.DataFrame([
    # Proveedor A: Montos variables en días y horarios laborales normales (control negativo)
    {"transaction_id": "TX-A01", "vendor_id": "PROV-A-LEGIT", "amount": 14250.00, "timestamp": "2024-01-10 10:15:00"},
    {"transaction_id": "TX-A02", "vendor_id": "PROV-A-LEGIT", "amount": 8300.00,  "timestamp": "2024-01-17 11:30:00"},
    {"transaction_id": "TX-A03", "vendor_id": "PROV-A-LEGIT", "amount": 21500.00, "timestamp": "2024-01-24 14:45:00"},
    {"transaction_id": "TX-A04", "vendor_id": "PROV-A-LEGIT", "amount": 4900.00,  "timestamp": "2024-02-02 09:20:00"},
    {"transaction_id": "TX-A05", "vendor_id": "PROV-A-LEGIT", "amount": 17800.00, "timestamp": "2024-02-12 16:10:00"},
    {"transaction_id": "TX-A06", "vendor_id": "PROV-A-LEGIT", "amount": 11650.00, "timestamp": "2024-02-20 12:05:00"},

    # Proveedor B: Montos idénticos coordinados de $9,999.00 en días hábiles (colapso de entropía)
    {"transaction_id": "TX-B01", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "timestamp": "2024-01-12 10:00:00"},
    {"transaction_id": "TX-B02", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "timestamp": "2024-01-15 11:15:00"},
    {"transaction_id": "TX-B03", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "timestamp": "2024-01-19 14:30:00"},
    {"transaction_id": "TX-B04", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "timestamp": "2024-01-23 09:45:00"},
    {"transaction_id": "TX-B05", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "timestamp": "2024-01-29 15:20:00"},
    {"transaction_id": "TX-B06", "vendor_id": "PROV-B-STRUCT", "amount": 9999.00, "timestamp": "2024-02-05 13:10:00"},

    # Proveedor C: Ejecuciones nocturnas en fin de semana (Domingo 02:00 AM) + Ráfaga de 4 transacciones en < 5 min
    {"transaction_id": "TX-C01", "vendor_id": "PROV-C-NIGHT", "amount": 8500.00,  "timestamp": "2024-02-18 02:00:15"},
    {"transaction_id": "TX-C02", "vendor_id": "PROV-C-NIGHT", "amount": 14200.00, "timestamp": "2024-02-18 02:01:20"},
    {"transaction_id": "TX-C03", "vendor_id": "PROV-C-NIGHT", "amount": 6800.00,  "timestamp": "2024-02-18 02:02:45"},
    {"transaction_id": "TX-C04", "vendor_id": "PROV-C-NIGHT", "amount": 19300.00, "timestamp": "2024-02-18 02:03:50"}
])


# ==============================================================================
# 8. Ejecución Principal Unificada
# ==============================================================================
if __name__ == "__main__":
    results = run_all_tabular_detectors(
        invoices_df=invoices_df,
        payments_df=payments_df,
        ledger_df=ledger_df,
        transactions_df=vendor_transactions_df
    )

    json_output = json.dumps([obs.model_dump() for obs in results], indent=2, ensure_ascii=False)
    print(json_output)
