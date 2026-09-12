"""
Módulo de Detección Tabular y Conciliación Determinista
Rama 3: Análisis Tabular / Probabilístico
Ubicación: src/detectors/tabulares.py
"""

from typing import Optional
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
# 2. Función de Reconciliación Determinista Triple (Invoices ↔ Payments ↔ Ledger)
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
        # Factura y Pago cuadran perfecto en el banco, pero el libro contable difiere
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

        # CASO 2: DISCREPANCIA ENTRE FACTURA Y PAGO (con o sin reflejo en ledger)
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
# 3. Mocks de Datos y Ejecución
# ==============================================================================
# 1) Invoices (Facturas)
invoices_df = pd.DataFrame([
    {
        "invoice_id": "INV-001",
        "issuer_rfc": "PROV850101AAA",
        "total_amount": 10000.00,
        "issue_date": "2024-01-15"
    },
    {
        "invoice_id": "INV-002",
        "issuer_rfc": "SERV920312BBB",
        "total_amount": 25000.00,
        "issue_date": "2024-01-20"
    },
    {
        "invoice_id": "INV-003",
        "issuer_rfc": "CONS880905CCC",
        "total_amount": 18500.00,
        "issue_date": "2024-02-01"
    },
    # Caso malicioso: Factura de 30,000
    {
        "invoice_id": "INV-004",
        "issuer_rfc": "OPER990714DDD",
        "total_amount": 30000.00,
        "issue_date": "2024-02-10"
    }
])

# 2) Payments (Pagos bancarios / tesorería)
payments_df = pd.DataFrame([
    {
        "payment_id": "PAY-001",
        "invoice_id": "INV-001",
        "payment_amount": 10000.00,  # Cuadre perfecto
        "payment_date": "2024-01-18"
    },
    {
        "payment_id": "PAY-002",
        "invoice_id": "INV-002",
        "payment_amount": 30000.00,  # Sobrepago (+5,000.00)
        "payment_date": "2024-01-25"
    },
    {
        "payment_id": "PAY-003",
        "invoice_id": "INV-003",
        "payment_amount": 12000.00,  # Pago parcial (-6,500.00)
        "payment_date": "2024-02-10"
    },
    # Caso malicioso: Pago bancario cuadra EXACTO con la factura ($30,000.00)
    {
        "payment_id": "PAY-004",
        "invoice_id": "INV-004",
        "payment_amount": 30000.00,
        "payment_date": "2024-02-12"
    }
])

# 3) Ledger (Libro mayor contable)
ledger_df = pd.DataFrame([
    {
        "transaction_id": "TX-001",
        "invoice_id": "INV-001",
        "recorded_amount": 10000.00  # Cuadre total
    },
    {
        "transaction_id": "TX-002",
        "invoice_id": "INV-002",
        "recorded_amount": 30000.00  # Refleja el egreso real del banco
    },
    {
        "transaction_id": "TX-003",
        "invoice_id": "INV-003",
        "recorded_amount": 12000.00  # Refleja el abono real del banco
    },
    # Caso malicioso: Pagaron $30,000 pero anotaron intencionalmente $25,000 en contabilidad
    {
        "transaction_id": "TX-004",
        "invoice_id": "INV-004",
        "recorded_amount": 25000.00  # Discrepancia contable: faltan $5,000.00 en libros
    }
])

if __name__ == "__main__":
    # Ejecución del cruce triple
    results = check_invoice_payment_mismatch(invoices_df, payments_df, ledger_df)

    # Impresión en formato JSON
    json_output = json.dumps([obs.model_dump() for obs in results], indent=2, ensure_ascii=False)
    print(json_output)
