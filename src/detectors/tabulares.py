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
# 2. Función de Reconciliación Determinista
# ==============================================================================
def check_invoice_payment_mismatch(
    invoices: pd.DataFrame,
    payments: pd.DataFrame,
    tolerance: float = 0.01
) -> list[TabularObservation]:
    """
    Cruza facturas y pagos por invoice_id, calcula determinísticamente la
    diferencia de montos y retorna una lista de TabularObservation por cada descuadre.
    """
    observations: list[TabularObservation] = []

    # Agregación determinista de pagos por invoice_id (permite abonos/parcialidades múltiples)
    payments_agg = (
        payments.groupby("invoice_id", as_index=False)
        .agg(
            total_paid=("payment_amount", "sum"),
            payment_ids=("payment_id", list)
        )
    )

    # Cruce de datos por invoice_id
    merged = pd.merge(
        invoices,
        payments_agg,
        on="invoice_id",
        how="outer"
    )

    # Relleno de nulos para aritmética exacta
    merged["total_amount"] = merged["total_amount"].fillna(0.0)
    merged["total_paid"] = merged["total_paid"].fillna(0.0)

    # Diferencia exacta: Pagado - Facturado
    merged["difference"] = (merged["total_paid"] - merged["total_amount"]).round(2)

    for _, row in merged.iterrows():
        inv_id = str(row["invoice_id"])
        invoiced = float(row["total_amount"])
        paid = float(row["total_paid"])
        diff = float(row["difference"])
        pay_ids = row["payment_ids"] if isinstance(row["payment_ids"], list) else []

        # Omitir si cuadra perfectamente
        if abs(diff) <= tolerance:
            continue

        facts = {
            "invoice_id": inv_id,
            "invoiced_amount": invoiced,
            "paid_amount": paid,
            "difference": diff,
            "currency": "MXN"
        }
        provenance: list[str] = []
        hypotheses: list[str] = []
        anomaly_score: float = 0.0

        if invoiced == 0.0 and paid > 0.0:
            facts["mismatch_type"] = "ORPHAN_PAYMENT"
            provenance = [f"payment:{pid}" for pid in pay_ids]
            anomaly_score = 0.85
            hypotheses = [
                "Pago anticipado previo a la emisión del CFDI.",
                "Referencia o identificador de factura erróneo o truncado en tesorería.",
                "Gasto operativo o comisión clasificado erróneamente con formato de factura."
            ]
        elif paid == 0.0 and invoiced > 0.0:
            facts["mismatch_type"] = "UNPAID_INVOICE"
            provenance = [f"invoice:{inv_id}"]
            anomaly_score = 0.40
            hypotheses = [
                "Factura vigente pendiente de liquidación según crédito comercial.",
                "Cancelación de factura no sincronizada con el libro contable."
            ]
        elif diff > 0.0:
            facts["mismatch_type"] = "OVERPAYMENT"
            facts["overpaid_amount"] = diff
            provenance = [f"invoice:{inv_id}"] + [f"payment:{pid}" for pid in pay_ids]
            ratio = min(diff / invoiced, 1.0) if invoiced > 0 else 1.0
            anomaly_score = round(0.5 + (0.5 * ratio), 2)
            hypotheses = [
                "Posible nota de crédito, descuento o devolución posterior pendiente de conciliar.",
                "Pago aplicado cubriendo saldos acumulados de facturas anteriores.",
                "Error operativo de doble dispersión bancaria."
            ]
        else:
            facts["mismatch_type"] = "UNDERPAYMENT"
            facts["pending_balance"] = abs(diff)
            provenance = [f"invoice:{inv_id}"] + [f"payment:{pid}" for pid in pay_ids]
            ratio = min(abs(diff) / invoiced, 1.0) if invoiced > 0 else 1.0
            anomaly_score = round(0.2 + (0.3 * ratio), 2)
            hypotheses = [
                "Esquema de pago en parcialidades con complemento de pago diferido pendiente.",
                "Retención de garantías o penalizaciones comerciales aplicadas al proveedor.",
                "Retención fiscal aplicable (IVA/ISR) no desglosada en el registro de pago."
            ]

        observation = TabularObservation(
            rule_or_model="check_invoice_payment_mismatch",
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
# Mock de facturas
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
    }
])

# Mock de pagos: pago exacto (INV-001), sobrepago (INV-002), pago parcial (INV-003)
payments_df = pd.DataFrame([
    {
        "payment_id": "PAY-001",
        "invoice_id": "INV-001",
        "payment_amount": 10000.00,
        "payment_date": "2024-01-18"
    },
    {
        "payment_id": "PAY-002",
        "invoice_id": "INV-002",
        "payment_amount": 30000.00,  # Sobrepago: +$5,000.00
        "payment_date": "2024-01-25"
    },
    {
        "payment_id": "PAY-003",
        "invoice_id": "INV-003",
        "payment_amount": 12000.00,  # Pago parcial: -$6,500.00
        "payment_date": "2024-02-10"
    }
])

if __name__ == "__main__":
    # Ejecución de la función
    results = check_invoice_payment_mismatch(invoices_df, payments_df)

    # Impresión en formato JSON
    json_output = json.dumps([obs.model_dump() for obs in results], indent=2, ensure_ascii=False)
    print(json_output)
