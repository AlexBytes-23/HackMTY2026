"""
Verifier Module - Forensic Auditor (Tabular Verification Domain)
Author: Daniel (Rama 3)

Provides deterministic, step-by-step verification functions that recalculate 
and validate claims made by LLMs or Detection Engines regarding tabular anomalies.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import pandas as pd
import math


@dataclass
class Claim:
    claim_id: str
    type: str  # e.g., 'invoice_reconciliation', 'entropy_collapse', 'bayesian_posterior'
    invoice_id: Optional[str] = None
    payment_ids: List[str] = field(default_factory=list)
    ledger_ids: List[str] = field(default_factory=list)
    entity_id: Optional[str] = None
    claimed_exposure: float = 0.0
    evidence_vector: Optional[Dict[str, bool]] = None
    expected_posterior: Optional[float] = None


@dataclass
class VerifiedFact:
    claim_id: str
    verified: bool
    type: str
    recalculated_values: Dict[str, Any]
    evidence_refs: List[str]
    errors: List[str] = field(default_factory=list)


def verify_invoice_reconciliation(
    claim: Claim,
    invoices_df: pd.DataFrame,
    payments_df: pd.DataFrame,
    ledger_df: pd.DataFrame
) -> VerifiedFact:
    """Verifies 3-way / 4-way match reconciliation claims deterministically."""
    errors = []
    evidence_refs = []
    
    # 1. Fetch Invoice
    inv_rows = invoices_df[invoices_df['invoice_id'] == claim.invoice_id]
    if inv_rows.empty:
        errors.append(f"Invoice ID {claim.invoice_id} not found in database.")
        inv_amount = 0.0
    else:
        inv_amount = float(inv_rows.iloc[0]['amount'])
        evidence_refs.append(f"invoice:{claim.invoice_id}")
        
    # 2. Fetch Payments
    pay_rows = payments_df[payments_df['invoice_id'] == claim.invoice_id]
    if claim.payment_ids:
        pay_rows = pay_rows[pay_rows['payment_id'].isin(claim.payment_ids)]
    
    total_payments = float(pay_rows['amount'].sum()) if not pay_rows.empty else 0.0
    for p_id in pay_rows['payment_id'].tolist():
        evidence_refs.append(f"payment:{p_id}")
        
    # 3. Fetch Ledger
    ledg_rows = ledger_df[ledger_df['reference_id'] == claim.invoice_id]
    if claim.ledger_ids:
        ledg_rows = ledg_rows[ledg_rows['transaction_id'].isin(claim.ledger_ids)]
        
    total_ledger = float(ledg_rows['amount'].sum()) if not ledg_rows.empty else 0.0
    for l_id in ledg_rows['transaction_id'].tolist():
        evidence_refs.append(f"ledger:{l_id}")
        
    # 4. Recalculate Discrepancies
    overpayment_amount = max(0.0, total_payments - inv_amount)
    underpayment_amount = max(0.0, inv_amount - total_payments)
    ledger_discrepancy = total_payments - total_ledger
    
    recalculated = {
        "invoice_amount": inv_amount,
        "total_payments": total_payments,
        "total_ledger": total_ledger,
        "calculated_exposure": overpayment_amount if "overpayment" in claim.type.lower() else underpayment_amount,
        "ledger_discrepancy": ledger_discrepancy
    }
    
    # Validation Check
    if abs(recalculated["calculated_exposure"] - claim.claimed_exposure) > 0.01:
        errors.append(
            f"Exposure mismatch: Claimed ${claim.claimed_exposure:,.2f}, "
            f"but recalculated as ${recalculated['calculated_exposure']:,.2f}."
        )
        
    return VerifiedFact(
        claim_id=claim.claim_id,
        verified=(len(errors) == 0),
        type=claim.type,
        recalculated_values=recalculated,
        evidence_refs=evidence_refs,
        errors=errors
    )


def verify_entropy_collapse(
    claim: Claim,
    transactions_df: pd.DataFrame
) -> VerifiedFact:
    """Verifies Shannon Entropy collapse claims for structured transactions (smurfing)."""
    errors = []
    evidence_refs = []
    
    tx_sub = transactions_df[transactions_df['entity_id'] == claim.entity_id]
    if tx_sub.empty:
        errors.append(f"Entity ID {claim.entity_id} has no recorded transactions.")
        return VerifiedFact(claim.claim_id, False, claim.type, {}, [], errors)
        
    for tx_id in tx_sub['transaction_id'].tolist():
        evidence_refs.append(f"transaction_id:{tx_id}")
        
    amounts = tx_sub['amount'].tolist()
    total_tx = len(amounts)
    
    # Calculate Shannon Entropy
    freqs = pd.Series(amounts).value_counts(normalize=True)
    entropy = -sum(p * math.log2(p) for p in freqs)
    max_entropy = math.log2(total_tx) if total_tx > 1 else 1.0
    normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0.0
    
    recalculated = {
        "entity_id": claim.entity_id,
        "transaction_count": total_tx,
        "shannon_entropy": round(entropy, 4),
        "normalized_entropy": round(normalized_entropy, 4),
        "is_collapsed": normalized_entropy < 0.25
    }
    
    verified = recalculated["is_collapsed"]
    if not verified:
        errors.append(f"Entropy ({normalized_entropy:.2f}) does not indicate structured collapse (< 0.25).")
        
    return VerifiedFact(claim.claim_id, verified, claim.type, recalculated, evidence_refs, errors)


if __name__ == "__main__":
    # Test Verifier Execution
    print("=====================================================================")
    print("TABULAR VERIFIER - DEMO & SELF-TEST")
    print("=====================================================================")
    
    # Mock Data
    inv_df = pd.DataFrame([{"invoice_id": "F1", "amount": 100000.0}])
    pay_df = pd.DataFrame([
        {"payment_id": "P1", "invoice_id": "F1", "amount": 100000.0},
        {"payment_id": "P2", "invoice_id": "F1", "amount": 100000.0}
    ])
    ledg_df = pd.DataFrame([{"transaction_id": "L1", "reference_id": "F1", "amount": 200000.0}])
    
    claim = Claim(
        claim_id="CLM-001",
        type="invoice_overpayment",
        invoice_id="F1",
        payment_ids=["P1", "P2"],
        claimed_exposure=100000.0
    )
    
    fact = verify_invoice_reconciliation(claim, inv_df, pay_df, ledg_df)
    print(fact)