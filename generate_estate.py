"""
Generador de Base de Datos Sintética (Estate)
Proyecto: Forensic Auditor - HackMTY 2026
Fases 1, 2 y 3: Configuración, Motor Transaccional, Inyección de Anomalías y Exportación SQLite
Autor: Daniel (Data Engineering / Rama 3)
"""

import argparse
import json
import os
import random
import sqlite3
import string
import uuid
from datetime import date, timedelta
from typing import List, Dict, Any, Tuple
from faker import Faker


# ==============================================================================
# Esquema Canónico Exacto (8 Tablas Obligatorias)
# ==============================================================================
SCHEMA = """
-- 1. Catálogo de Proveedores
CREATE TABLE IF NOT EXISTS vendors (
    vendor_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    rfc TEXT UNIQUE NOT NULL,
    clabe TEXT NOT NULL,
    address TEXT,
    created_at TEXT NOT NULL
);

-- 2. Empleados de la Empresa
CREATE TABLE IF NOT EXISTS employees (
    employee_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    position TEXT,
    clabe TEXT UNIQUE NOT NULL,
    hire_date TEXT NOT NULL
);

-- 3. Contratos Marco y Adquisiciones
CREATE TABLE IF NOT EXISTS contracts (
    contract_id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    title TEXT,
    amount REAL NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id)
);

-- 4. Órdenes de Compra
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    contract_id TEXT,
    amount REAL NOT NULL,
    issue_date TEXT NOT NULL,
    status TEXT NOT NULL,
    department TEXT,
    FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id),
    FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
);

-- 5. Facturas y CFDI emitidos
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    po_id TEXT,
    uuid TEXT,
    issuer_rfc TEXT NOT NULL,
    receiver_rfc TEXT NOT NULL,
    amount REAL NOT NULL,
    tax REAL NOT NULL,
    total_amount REAL NOT NULL,
    issue_date TEXT NOT NULL,
    due_date TEXT,
    status TEXT NOT NULL,
    description TEXT,
    FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id),
    FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id)
);

-- 6. Pólizas y Libro Mayor Contable (General Ledger)
CREATE TABLE IF NOT EXISTS ledger (
    entry_id TEXT PRIMARY KEY,
    transaction_id TEXT,
    reference_id TEXT,
    account_code TEXT NOT NULL,
    debit REAL DEFAULT 0.0,
    credit REAL DEFAULT 0.0,
    amount REAL NOT NULL,
    date TEXT NOT NULL,
    description TEXT
);

-- 7. Transacciones y Dispersiones Bancarias
CREATE TABLE IF NOT EXISTS bank_txns (
    txn_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    origin_account TEXT NOT NULL,
    destination_account TEXT NOT NULL,
    reference TEXT,
    description TEXT
);

-- 8. Lista Negra del SAT (Artículo 69-B del CFF)
CREATE TABLE IF NOT EXISTS efos_list (
    rfc TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    situation TEXT NOT NULL,
    publication_date TEXT NOT NULL
);
"""


# ==============================================================================
# Funciones Auxiliares de Generación Sintética
# ==============================================================================
def _generate_synthetic_rfc(is_moral: bool = True) -> str:
    """Genera un RFC sintético mexicano válido (12 chars Morales, 13 chars Físicas)."""
    letters_count = 3 if is_moral else 4
    letters = "".join(random.choices(string.ascii_uppercase, k=letters_count))
    year = random.randint(80, 99) if random.random() < 0.4 else random.randint(0, 23)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    date_part = f"{year:02d}{month:02d}{day:02d}"
    homoclave = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))
    return f"{letters}{date_part}{homoclave}"


def _generate_synthetic_clabe(used_clabes: set) -> str:
    """Genera una CLABE bancaria mexicana de exactamente 18 dígitos como texto."""
    bank_codes = ["002", "012", "014", "021", "072", "044", "127", "036", "058", "062"]
    while True:
        bank = random.choice(bank_codes)
        plaza = f"{random.randint(10, 999):03d}"
        account = f"{random.randint(10000000000, 99999999999):011d}"
        control_digit = str(random.randint(0, 9))
        clabe = f"{bank}{plaza}{account}{control_digit}"
        if len(clabe) == 18 and clabe not in used_clabes:
            used_clabes.add(clabe)
            return clabe


# ==============================================================================
# FASE 1: Entidades Base en Memoria
# ==============================================================================
def generate_vendors(n: int = 40, fake: Faker = None, used_clabes: set = None) -> List[Dict[str, Any]]:
    if fake is None:
        fake = Faker("es_MX")
    if used_clabes is None:
        used_clabes = set()

    vendors = []
    used_rfcs = set()

    for i in range(1, n + 1):
        is_moral = (random.random() < 0.75)
        while True:
            rfc = _generate_synthetic_rfc(is_moral=is_moral)
            if rfc not in used_rfcs:
                used_rfcs.add(rfc)
                break

        name = fake.company() if is_moral else fake.name()
        clabe = _generate_synthetic_clabe(used_clabes)
        address = fake.address().replace("\n", ", ")
        created_days_ago = random.randint(100, 1400)
        created_at = (date.today() - timedelta(days=created_days_ago)).isoformat()

        vendors.append({
            "vendor_id": f"VEND-{i:04d}",
            "name": name,
            "rfc": rfc,
            "clabe": clabe,
            "address": address,
            "created_at": created_at
        })
    return vendors


def generate_employees(n: int = 15, fake: Faker = None, used_clabes: set = None) -> List[Dict[str, Any]]:
    if fake is None:
        fake = Faker("es_MX")
    if used_clabes is None:
        used_clabes = set()

    departments = [
        "Compras y Adquisiciones", "Tesorería y Finanzas", "Contabilidad",
        "Operaciones y Logística", "Recursos Humanos", "Tecnologías de Información",
        "Auditoría Interna y Cumplimiento"
    ]
    positions = {
        "Compras y Adquisiciones": ["Director de Compras", "Gerente de Abastecimiento", "Comprador Senior", "Analista de Proveedores"],
        "Tesorería y Finanzas": ["Director de Finanzas (CFO)", "Gerente de Tesorería", "Especialista en Dispersión Bancaria", "Analista de Pagos"],
        "Contabilidad": ["Contador General", "Supervisor de Cuentas por Pagar", "Auxiliar Contable", "Especialista Fiscal"],
        "Operaciones y Logística": ["Gerente de Operaciones", "Coordinador de Almacén", "Supervisor de Logística"],
        "Recursos Humanos": ["Gerente de RH", "Especialista de Nómina"],
        "Tecnologías de Información": ["Líder de Sistemas ERP", "Administrador de Bases de Datos"],
        "Auditoría Interna y Cumplimiento": ["Oficial de Cumplimiento", "Auditor Forense Interno"]
    }

    employees = []
    for i in range(1, n + 1):
        dept = random.choice(departments)
        pos = random.choice(positions[dept])
        clabe = _generate_synthetic_clabe(used_clabes)
        hire_days_ago = random.randint(200, 2000)
        hire_date = (date.today() - timedelta(days=hire_days_ago)).isoformat()

        employees.append({
            "employee_id": f"EMP-{i:03d}",
            "name": fake.name(),
            "department": dept,
            "position": pos,
            "clabe": clabe,
            "hire_date": hire_date
        })
    return employees


def generate_contracts(vendors: List[Dict[str, Any]], n: int = 20, fake: Faker = None) -> List[Dict[str, Any]]:
    contract_titles = [
        "Suministro de Materiales Operativos y Consumibles",
        "Servicios Profesionales de Asesoría Estratégica",
        "Mantenimiento Preventivo y Correctivo de Infraestructura",
        "Licenciamiento y Soporte Técnico de Software Empresarial",
        "Arrendamiento de Flotilla y Equipo de Transporte",
        "Servicio de Seguridad Privada y Vigilancia Perimetral",
        "Consultoría en Ciberseguridad y Auditoría TI",
        "Servicios de Limpieza Integral y Sanitización"
    ]
    contracts = []
    for i in range(1, n + 1):
        vendor = random.choice(vendors)
        title = random.choice(contract_titles)
        amount = round(random.uniform(150_000.0, 5_000_000.0), 2)
        start_days_ago = random.randint(120, 700)
        start_date = date.today() - timedelta(days=start_days_ago)
        duration_days = random.choice([180, 365, 730])
        end_date = start_date + timedelta(days=duration_days)
        status = "ACTIVO" if end_date >= date.today() else "CERRADO"

        contracts.append({
            "contract_id": f"CTR-{i:04d}",
            "vendor_id": vendor["vendor_id"],
            "title": title,
            "amount": amount,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "status": status
        })
    return contracts


def generate_efos(vendors: List[Dict[str, Any]], n: int = 3) -> List[Dict[str, Any]]:
    selected_vendors = random.sample(vendors, n)
    efos = []
    for v in selected_vendors:
        pub_days_ago = random.randint(30, 365)
        pub_date = (date.today() - timedelta(days=pub_days_ago)).isoformat()
        situation = random.choice(["Definitivo", "Definitivo", "Presunto"])

        efos.append({
            "rfc": v["rfc"],
            "name": v["name"],
            "situation": situation,
            "publication_date": pub_date
        })
    return efos


# ==============================================================================
# FASE 2: Motor Transaccional y Contabilidad (Operación Normal)
# ==============================================================================
def generate_purchase_orders(
    vendors: List[Dict[str, Any]],
    n: int = 200,
    fake: Faker = None
) -> List[Dict[str, Any]]:
    departments = [
        "Operaciones", "Tecnologías de Información", "Mantenimiento e Instalaciones",
        "Logística y Cadena de Suministro", "Administración Central", "Marketing"
    ]
    statuses = ["APROBADA", "CUMPLIDA", "EN_PROCESO"]

    purchase_orders = []
    for i in range(1, n + 1):
        vendor = random.choice(vendors)
        amount = round(random.uniform(5_000.0, 450_000.0), 2)
        days_ago = random.randint(60, 720)
        issue_date = (date.today() - timedelta(days=days_ago)).isoformat()

        purchase_orders.append({
            "po_id": f"PO-{i:05d}",
            "vendor_id": vendor["vendor_id"],
            "vendor_rfc": vendor["rfc"],
            "contract_id": None,
            "amount": amount,
            "issue_date": issue_date,
            "status": random.choice(statuses),
            "department": random.choice(departments)
        })
    return purchase_orders


def generate_invoices(
    vendors: List[Dict[str, Any]],
    pos: List[Dict[str, Any]],
    n: int = 350,
    receiver_rfc: str = "AUD201010XYZ"
) -> List[Dict[str, Any]]:
    vendor_by_rfc = {v["rfc"]: v for v in vendors}
    vendor_by_id = {v["vendor_id"]: v for v in vendors}

    invoices = []
    for i in range(1, n + 1):
        has_po = (random.random() < 0.70 and len(pos) > 0)
        po = random.choice(pos) if has_po else None

        if po:
            vendor = vendor_by_id.get(po["vendor_id"]) or vendor_by_rfc[po["vendor_rfc"]]
            po_id = po["po_id"]
            po_date = date.fromisoformat(po["issue_date"])
            issue_date = po_date + timedelta(days=random.randint(1, 15))
        else:
            vendor = random.choice(vendors)
            po_id = None
            days_ago = random.randint(30, 600)
            issue_date = date.today() - timedelta(days=days_ago)

        due_date = issue_date + timedelta(days=random.choice([30, 45, 60]))

        # REGLA MATEMÁTICA: subtotal + iva (16%) = total
        subtotal = round(random.uniform(3_500.0, 350_000.0), 2)
        iva = round(subtotal * 0.16, 2)
        total = round(subtotal + iva, 2)

        invoice_uuid = str(uuid.UUID(int=random.getrandbits(128), version=4))

        invoices.append({
            "invoice_id": f"INV-{i:05d}",
            "uuid": invoice_uuid,
            "invoice_uuid": invoice_uuid,
            "vendor_id": vendor["vendor_id"],
            "issuer_rfc": vendor["rfc"],
            "receiver_rfc": receiver_rfc,
            "po_id": po_id,
            "subtotal": subtotal,
            "iva": iva,
            "total": total,
            "amount": subtotal,
            "tax": iva,
            "total_amount": total,
            "issue_date": issue_date.isoformat(),
            "due_date": due_date.isoformat(),
            "status": "VIGENTE",
            "description": f"Factura de suministros/servicios emitida por {vendor['name']}"
        })
    return invoices


def generate_ledger(invoices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ledger_entries = []
    for idx, inv in enumerate(invoices, start=1):
        inv_uuid = inv.get("uuid") or inv["invoice_uuid"]
        inv_id = inv["invoice_id"]
        total_amount = inv["total_amount"]
        entry_date = inv["issue_date"]
        txn_poliza_id = f"POL-{idx:05d}"

        # Cargo (Débito a Gastos)
        ledger_entries.append({
            "entry_id": f"LEDG-{idx:05d}-D",
            "transaction_id": txn_poliza_id,
            "invoice_uuid": inv_uuid,
            "reference_id": inv_uuid,
            "account_code": "601-01",
            "debit": total_amount,
            "credit": 0.0,
            "amount": total_amount,
            "date": entry_date,
            "description": f"Provisión factura {inv_id} - Cargo Gastos Operativos"
        })

        # Abono (Crédito a Proveedores)
        ledger_entries.append({
            "entry_id": f"LEDG-{idx:05d}-C",
            "transaction_id": txn_poliza_id,
            "invoice_uuid": inv_uuid,
            "reference_id": inv_uuid,
            "account_code": "201-01",
            "debit": 0.0,
            "credit": total_amount,
            "amount": total_amount,
            "date": entry_date,
            "description": f"Provisión factura {inv_id} - Abono Cuentas por Pagar"
        })
    return ledger_entries


def generate_bank_txns(
    vendors: List[Dict[str, Any]],
    invoices: List[Dict[str, Any]],
    origin_corporate_clabe: str = "012180001122334455"
) -> List[Dict[str, Any]]:
    vendor_by_rfc = {v["rfc"]: v for v in vendors}
    vendor_by_id = {v["vendor_id"]: v for v in vendors}

    bank_transactions = []
    for pay_idx, inv in enumerate(invoices, start=1):
        vendor = vendor_by_rfc.get(inv["issuer_rfc"]) or vendor_by_id[inv["vendor_id"]]
        to_clabe = vendor["clabe"]
        amount = inv["total_amount"]

        issue_date = date.fromisoformat(inv["issue_date"])
        pay_date = issue_date + timedelta(days=random.randint(2, 25))

        bank_transactions.append({
            "txn_id": f"TXN-{pay_idx:05d}",
            "origin_account": origin_corporate_clabe,
            "destination_account": to_clabe,
            "from_clabe": origin_corporate_clabe,
            "to_clabe": to_clabe,
            "bank_clabe": to_clabe,
            "amount": amount,
            "date": pay_date.isoformat(),
            "reference": f"SPEI-{pay_idx:07d}",
            "invoice_id": inv["invoice_id"],
            "invoice_uuid": inv.get("uuid") or inv.get("invoice_uuid"),
            "description": f"Liquidación SPEI Factura {inv['invoice_id']} a favor de {vendor['name']}"
        })
    return bank_transactions


# ==============================================================================
# FASE 3: Inyección de Esquemas, Señuelos y Exportación SQLite
# ==============================================================================
def inject_anomalies(data: Dict[str, List[Dict[str, Any]]]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Inyecta fraudes estructurados y señuelos legítimos modificando las estructuras en memoria:
    1. Phantom Vendor (EFOS): 3 facturas pagadas sin orden de compra ni contrato.
    2. Threshold Splitting: 6 facturas emitidas la misma semana de $89,500 c/u.
    3. Kickback: Segundo pago donde origen es la CLABE del vendor y destino es la CLABE de un empleado.
    4. Decoy (Señuelo Legítimo): 12 facturas idénticas pero justificadas con contrato de tarifa mensual fija.
    
    Retorna la tupla (data_dict_modificado, answer_key_dict).
    """
    answer_key: Dict[str, Any] = {
        "metadata": {
            "title": "Ground Truth Answer Key - Forensic Auditor",
            "generated_at": date.today().isoformat(),
            "note": "Documento forense secreto. No debe incluirse dentro de la base de datos estate.db"
        },
        "schemes": []
    }

    # --------------------------------------------------------------------------
    # 1. PHANTOM VENDOR (Proveedor Fantasma / EFOS)
    # --------------------------------------------------------------------------
    efos_rfcs = [e["rfc"] for e in data["efos_list"]]
    phantom_rfc = efos_rfcs[0]
    phantom_vendor = next(v for v in data["vendors"] if v["rfc"] == phantom_rfc)
    phantom_vid = phantom_vendor["vendor_id"]

    # Eliminar/evitar que tenga registros en purchase_orders o contracts
    data["purchase_orders"] = [po for po in data["purchase_orders"] if po["vendor_id"] != phantom_vid]
    data["contracts"] = [c for c in data["contracts"] if c["vendor_id"] != phantom_vid]

    # Limpiar cualquier factura previa que pudiera haberse generado aleatoriamente para el EFOS
    existing_phantom_invs = [inv for inv in data["invoices"] if inv["issuer_rfc"] == phantom_rfc]
    existing_inv_uuids = {inv["uuid"] for inv in existing_phantom_invs}
    data["invoices"] = [inv for inv in data["invoices"] if inv["issuer_rfc"] != phantom_rfc]
    data["ledger"] = [entry for entry in data["ledger"] if entry["reference_id"] not in existing_inv_uuids]
    data["bank_txns"] = [txn for txn in data["bank_txns"] if txn.get("invoice_uuid") not in existing_inv_uuids]

    # Contadores seguros para garantizar IDs únicos
    inv_counter = max([int(inv["invoice_id"].split("-")[1]) for inv in data["invoices"]]) + 1 if data["invoices"] else 1
    txn_counter = max([int(t["txn_id"].split("-")[1]) for t in data["bank_txns"]]) + 1 if data["bank_txns"] else 1
    pol_counter = max([int(l["transaction_id"].split("-")[1]) for l in data["ledger"]]) + 1 if data["ledger"] else 1
    ctr_counter = max([int(c["contract_id"].split("-")[1]) for c in data["contracts"]]) + 1 if data["contracts"] else 1

    # Generar exactamente 3 facturas pagadas en bank_txns y ledger para el Phantom Vendor
    phantom_invoices = []
    phantom_ledger = []
    phantom_bank_txns = []
    phantom_total_exposure = 0.0

    for k in range(1, 4):
        curr_inv_id = f"INV-{inv_counter:05d}"
        curr_pol_id = f"POL-{pol_counter:05d}"
        curr_txn_id = f"TXN-{txn_counter:05d}"

        subtotal = round(random.uniform(220_000.0, 480_000.0), 2)
        iva = round(subtotal * 0.16, 2)
        total = round(subtotal + iva, 2)
        phantom_total_exposure += total

        inv_uuid = str(uuid.UUID(int=random.getrandbits(128), version=4))
        inv_date = date.today() - timedelta(days=random.randint(60, 240))
        pay_date = inv_date + timedelta(days=random.randint(2, 5))

        inv = {
            "invoice_id": curr_inv_id,
            "uuid": inv_uuid,
            "invoice_uuid": inv_uuid,
            "vendor_id": phantom_vid,
            "issuer_rfc": phantom_rfc,
            "receiver_rfc": "AUD201010XYZ",
            "po_id": None,  # Ausencia de soporte documental
            "subtotal": subtotal,
            "iva": iva,
            "total": total,
            "amount": subtotal,
            "tax": iva,
            "total_amount": total,
            "issue_date": inv_date.isoformat(),
            "due_date": (inv_date + timedelta(days=30)).isoformat(),
            "status": "VIGENTE",
            "description": f"Servicios de asesoría estratégica integral lote {k}"
        }
        phantom_invoices.append(inv)

        # Partida doble en ledger
        phantom_ledger.append({
            "entry_id": f"LEDG-{inv_counter:05d}-D",
            "transaction_id": curr_pol_id,
            "invoice_uuid": inv_uuid,
            "reference_id": inv_uuid,
            "account_code": "601-01",
            "debit": total,
            "credit": 0.0,
            "amount": total,
            "date": inv_date.isoformat(),
            "description": f"Provisión factura {curr_inv_id} - Asesoría externa"
        })
        phantom_ledger.append({
            "entry_id": f"LEDG-{inv_counter:05d}-C",
            "transaction_id": curr_pol_id,
            "invoice_uuid": inv_uuid,
            "reference_id": inv_uuid,
            "account_code": "201-01",
            "debit": 0.0,
            "credit": total,
            "amount": total,
            "date": inv_date.isoformat(),
            "description": f"Provisión factura {curr_inv_id} - Abono Proveedor"
        })

        # Pago bancario liquidado
        phantom_bank_txns.append({
            "txn_id": curr_txn_id,
            "origin_account": "012180001122334455",
            "destination_account": phantom_vendor["clabe"],
            "from_clabe": "012180001122334455",
            "to_clabe": phantom_vendor["clabe"],
            "bank_clabe": phantom_vendor["clabe"],
            "amount": total,
            "date": pay_date.isoformat(),
            "reference": f"SPEI-{txn_counter:07d}",
            "invoice_id": curr_inv_id,
            "invoice_uuid": inv_uuid,
            "description": f"Liquidación SPEI Factura {curr_inv_id} a favor de {phantom_vendor['name']}"
        })

        inv_counter += 1
        pol_counter += 1
        txn_counter += 1

    data["invoices"].extend(phantom_invoices)
    data["ledger"].extend(phantom_ledger)
    data["bank_txns"].extend(phantom_bank_txns)

    answer_key["schemes"].append({
        "scheme_type": "PHANTOM_VENDOR_EFOS",
        "is_fraud": True,
        "description": "Proveedor listado en Art. 69-B del SAT cobró 3 facturas sin orden de compra ni contrato.",
        "vendor_id": phantom_vid,
        "vendor_rfc": phantom_rfc,
        "vendor_name": phantom_vendor["name"],
        "invoice_ids": [inv["invoice_id"] for inv in phantom_invoices],
        "invoice_uuids": [inv["uuid"] for inv in phantom_invoices],
        "bank_txn_ids": [txn["txn_id"] for txn in phantom_bank_txns],
        "total_exposure": round(phantom_total_exposure, 2)
    })

    # --------------------------------------------------------------------------
    # 2. THRESHOLD SPLITTING (Pitufeo / Evasión de Umbral de Aprobación)
    # --------------------------------------------------------------------------
    eligible_vendors = [v for v in data["vendors"] if v["rfc"] not in efos_rfcs]
    split_vendor = eligible_vendors[0]
    split_vid = split_vendor["vendor_id"]
    split_rfc = split_vendor["rfc"]

    subtotal_split = 89500.0
    iva_split = round(subtotal_split * 0.16, 2)       # 14,320.0
    total_split = round(subtotal_split + iva_split, 2)  # 103,820.0

    start_week = date(2024, 4, 8)  # Semana del 8 de abril de 2024
    split_invoices = []
    split_ledger = []
    split_bank_txns = []

    for s_idx in range(6):
        curr_inv_id = f"INV-{inv_counter:05d}"
        curr_pol_id = f"POL-{pol_counter:05d}"
        curr_txn_id = f"TXN-{txn_counter:05d}"

        inv_date = start_week + timedelta(days=s_idx)
        pay_date = inv_date + timedelta(days=random.randint(1, 3))
        inv_uuid = str(uuid.UUID(int=random.getrandbits(128), version=4))

        inv = {
            "invoice_id": curr_inv_id,
            "uuid": inv_uuid,
            "invoice_uuid": inv_uuid,
            "vendor_id": split_vid,
            "issuer_rfc": split_rfc,
            "receiver_rfc": "AUD201010XYZ",
            "po_id": None,
            "subtotal": subtotal_split,
            "iva": iva_split,
            "total": total_split,
            "amount": subtotal_split,
            "tax": iva_split,
            "total_amount": total_split,
            "issue_date": inv_date.isoformat(),
            "due_date": (inv_date + timedelta(days=30)).isoformat(),
            "status": "VIGENTE",
            "description": f"Suministro de componentes industriales entrega fraccionada {s_idx + 1}"
        }
        split_invoices.append(inv)

        split_ledger.append({
            "entry_id": f"LEDG-{inv_counter:05d}-D",
            "transaction_id": curr_pol_id,
            "invoice_uuid": inv_uuid,
            "reference_id": inv_uuid,
            "account_code": "601-01",
            "debit": total_split,
            "credit": 0.0,
            "amount": total_split,
            "date": inv_date.isoformat(),
            "description": f"Provisión factura {curr_inv_id} - Gasto operativo"
        })
        split_ledger.append({
            "entry_id": f"LEDG-{inv_counter:05d}-C",
            "transaction_id": curr_pol_id,
            "invoice_uuid": inv_uuid,
            "reference_id": inv_uuid,
            "account_code": "201-01",
            "debit": 0.0,
            "credit": total_split,
            "amount": total_split,
            "date": inv_date.isoformat(),
            "description": f"Provisión factura {curr_inv_id} - Pasivo proveedor"
        })

        split_bank_txns.append({
            "txn_id": curr_txn_id,
            "origin_account": "012180001122334455",
            "destination_account": split_vendor["clabe"],
            "from_clabe": "012180001122334455",
            "to_clabe": split_vendor["clabe"],
            "bank_clabe": split_vendor["clabe"],
            "amount": total_split,
            "date": pay_date.isoformat(),
            "reference": f"SPEI-{txn_counter:07d}",
            "invoice_id": curr_inv_id,
            "invoice_uuid": inv_uuid,
            "description": f"Liquidación SPEI Factura {curr_inv_id} a favor de {split_vendor['name']}"
        })

        inv_counter += 1
        pol_counter += 1
        txn_counter += 1

    data["invoices"].extend(split_invoices)
    data["ledger"].extend(split_ledger)
    data["bank_txns"].extend(split_bank_txns)

    answer_key["schemes"].append({
        "scheme_type": "THRESHOLD_SPLITTING",
        "is_fraud": True,
        "description": "6 facturas emitidas en la misma semana por subtotal idéntico de $89,500 para evadir controles.",
        "vendor_id": split_vid,
        "vendor_rfc": split_rfc,
        "vendor_name": split_vendor["name"],
        "subtotal_per_invoice": subtotal_split,
        "total_per_invoice": total_split,
        "invoice_ids": [inv["invoice_id"] for inv in split_invoices],
        "invoice_uuids": [inv["uuid"] for inv in split_invoices],
        "bank_txn_ids": [txn["txn_id"] for txn in split_bank_txns],
        "total_exposure": round(total_split * 6, 2)
    })

    # --------------------------------------------------------------------------
    # 3. KICKBACK (Moche / Colusión Proveedor - Empleado)
    # --------------------------------------------------------------------------
    candidate_txns = [
        t for t in data["bank_txns"]
        if t["amount"] >= 50000.0 and t["destination_account"] != phantom_vendor["clabe"]
    ]
    orig_txn = candidate_txns[0] if candidate_txns else data["bank_txns"][0]

    vendor_kb = next(v for v in data["vendors"] if v["clabe"] == orig_txn["destination_account"])
    employee_kb = data["employees"][0]

    kickback_amount = round(orig_txn["amount"] * 0.10, 2)
    orig_date = date.fromisoformat(orig_txn["date"])
    kickback_date = orig_date + timedelta(days=1)

    kickback_txn_id = f"TXN-{txn_counter:05d}"
    kickback_txn = {
        "txn_id": kickback_txn_id,
        "origin_account": vendor_kb["clabe"],
        "destination_account": employee_kb["clabe"],
        "from_clabe": vendor_kb["clabe"],
        "to_clabe": employee_kb["clabe"],
        "bank_clabe": employee_kb["clabe"],
        "amount": kickback_amount,
        "date": kickback_date.isoformat(),
        "reference": f"SPEI-{txn_counter:07d}",
        "description": "Traspaso SPEI por servicios de consultoría y gestión"
    }
    txn_counter += 1
    data["bank_txns"].append(kickback_txn)

    answer_key["schemes"].append({
        "scheme_type": "KICKBACK",
        "is_fraud": True,
        "description": "Retorno de fondos del 10% desde la cuenta del proveedor hacia la cuenta bancaria de un empleado.",
        "vendor_id": vendor_kb["vendor_id"],
        "vendor_name": vendor_kb["name"],
        "vendor_clabe": vendor_kb["clabe"],
        "employee_id": employee_kb["employee_id"],
        "employee_name": employee_kb["name"],
        "employee_clabe": employee_kb["clabe"],
        "original_txn_id": orig_txn["txn_id"],
        "original_payment_amount": orig_txn["amount"],
        "kickback_txn_id": kickback_txn["txn_id"],
        "kickback_amount": kickback_amount
    })

    # --------------------------------------------------------------------------
    # 4. DECOY (Señuelo Legítimo: Facturación Periódica Justificada)
    # --------------------------------------------------------------------------
    decoy_vendor = eligible_vendors[1]
    decoy_vid = decoy_vendor["vendor_id"]
    decoy_rfc = decoy_vendor["rfc"]

    monthly_subtotal = 38000.0
    monthly_iva = round(monthly_subtotal * 0.16, 2)
    monthly_total = round(monthly_subtotal + monthly_iva, 2)

    decoy_contract_id = f"CTR-{ctr_counter:04d}"
    ctr_counter += 1
    decoy_contract = {
        "contract_id": decoy_contract_id,
        "vendor_id": decoy_vid,
        "title": "Arrendamiento Mensual de Servidores Dedicados y Soporte Técnico (Tarifa Fija)",
        "amount": round(monthly_total * 12, 2),
        "start_date": "2023-01-01",
        "end_date": "2023-12-31",
        "status": "CERRADO"
    }
    data["contracts"].append(decoy_contract)

    decoy_invoices = []
    decoy_ledger = []
    decoy_bank_txns = []

    for m in range(1, 13):
        curr_inv_id = f"INV-{inv_counter:05d}"
        curr_pol_id = f"POL-{pol_counter:05d}"
        curr_txn_id = f"TXN-{txn_counter:05d}"

        inv_date = date(2023, m, 5)
        pay_date = inv_date + timedelta(days=10)
        inv_uuid = str(uuid.UUID(int=random.getrandbits(128), version=4))

        inv = {
            "invoice_id": curr_inv_id,
            "uuid": inv_uuid,
            "invoice_uuid": inv_uuid,
            "vendor_id": decoy_vid,
            "issuer_rfc": decoy_rfc,
            "receiver_rfc": "AUD201010XYZ",
            "po_id": None,
            "subtotal": monthly_subtotal,
            "iva": monthly_iva,
            "total": monthly_total,
            "amount": monthly_subtotal,
            "tax": monthly_iva,
            "total_amount": monthly_total,
            "issue_date": inv_date.isoformat(),
            "due_date": (inv_date + timedelta(days=30)).isoformat(),
            "status": "VIGENTE",
            "description": f"Iguala mensual servidores dedicados periodo {2023}-{m:02d}"
        }
        decoy_invoices.append(inv)

        decoy_ledger.append({
            "entry_id": f"LEDG-{inv_counter:05d}-D",
            "transaction_id": curr_pol_id,
            "invoice_uuid": inv_uuid,
            "reference_id": inv_uuid,
            "account_code": "601-01",
            "debit": monthly_total,
            "credit": 0.0,
            "amount": monthly_total,
            "date": inv_date.isoformat(),
            "description": f"Provisión factura {curr_inv_id} - Renta servidores"
        })
        decoy_ledger.append({
            "entry_id": f"LEDG-{inv_counter:05d}-C",
            "transaction_id": curr_pol_id,
            "invoice_uuid": inv_uuid,
            "reference_id": inv_uuid,
            "account_code": "201-01",
            "debit": 0.0,
            "credit": monthly_total,
            "amount": monthly_total,
            "date": inv_date.isoformat(),
            "description": f"Provisión factura {curr_inv_id} - Abono proveedor"
        })

        decoy_bank_txns.append({
            "txn_id": curr_txn_id,
            "origin_account": "012180001122334455",
            "destination_account": decoy_vendor["clabe"],
            "from_clabe": "012180001122334455",
            "to_clabe": decoy_vendor["clabe"],
            "bank_clabe": decoy_vendor["clabe"],
            "amount": monthly_total,
            "date": pay_date.isoformat(),
            "reference": f"SPEI-{txn_counter:07d}",
            "invoice_id": curr_inv_id,
            "invoice_uuid": inv_uuid,
            "description": f"Liquidación SPEI Factura {curr_inv_id} a favor de {decoy_vendor['name']}"
        })

        inv_counter += 1
        pol_counter += 1
        txn_counter += 1

    answer_key["schemes"].append({
        "scheme_type": "DECOY_LEGITIMATE_RETAINER",
        "is_fraud": False,
        "description": "Señuelo legítimo: 12 facturas mensuales idénticas de $38,000 justificadas plenamente por contrato CTR marco.",
        "vendor_id": decoy_vid,
        "vendor_rfc": decoy_rfc,
        "vendor_name": decoy_vendor["name"],
        "contract_id": decoy_contract["contract_id"],
        "monthly_subtotal": monthly_subtotal,
        "monthly_total": monthly_total,
        "invoice_ids": [inv["invoice_id"] for inv in decoy_invoices],
        "total_amount_year": round(monthly_total * 12, 2)
    })

    return data, answer_key


# ==============================================================================
# Exportación a Base de Datos SQLite (8 Tablas Canónicas)
# ==============================================================================
def export_to_sqlite(data_dict: Dict[str, List[Dict[str, Any]]], db_path: str) -> None:
    """
    Crea las 8 tablas oficiales e inserta masivamente todos los registros en memoria.
    No agrega pistas estructurales ni columnas artificiales como is_fraud.
    """
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.executescript(SCHEMA)

    cursor.executemany(
        """
        INSERT INTO vendors (vendor_id, name, rfc, clabe, address, created_at)
        VALUES (:vendor_id, :name, :rfc, :clabe, :address, :created_at)
        """,
        data_dict["vendors"]
    )

    cursor.executemany(
        """
        INSERT INTO employees (employee_id, name, department, position, clabe, hire_date)
        VALUES (:employee_id, :name, :department, :position, :clabe, :hire_date)
        """,
        data_dict["employees"]
    )

    cursor.executemany(
        """
        INSERT INTO contracts (contract_id, vendor_id, title, amount, start_date, end_date, status)
        VALUES (:contract_id, :vendor_id, :title, :amount, :start_date, :end_date, :status)
        """,
        data_dict["contracts"]
    )

    cursor.executemany(
        """
        INSERT INTO purchase_orders (po_id, vendor_id, contract_id, amount, issue_date, status, department)
        VALUES (:po_id, :vendor_id, :contract_id, :amount, :issue_date, :status, :department)
        """,
        data_dict["purchase_orders"]
    )

    cursor.executemany(
        """
        INSERT INTO invoices (invoice_id, vendor_id, po_id, uuid, issuer_rfc, receiver_rfc, amount, tax, total_amount, issue_date, due_date, status, description)
        VALUES (:invoice_id, :vendor_id, :po_id, :uuid, :issuer_rfc, :receiver_rfc, :amount, :tax, :total_amount, :issue_date, :due_date, :status, :description)
        """,
        data_dict["invoices"]
    )

    cursor.executemany(
        """
        INSERT INTO ledger (entry_id, transaction_id, reference_id, account_code, debit, credit, amount, date, description)
        VALUES (:entry_id, :transaction_id, :reference_id, :account_code, :debit, :credit, :amount, :date, :description)
        """,
        data_dict["ledger"]
    )

    cursor.executemany(
        """
        INSERT INTO bank_txns (txn_id, date, amount, origin_account, destination_account, reference, description)
        VALUES (:txn_id, :date, :amount, :origin_account, :destination_account, :reference, :description)
        """,
        data_dict["bank_txns"]
    )

    cursor.executemany(
        """
        INSERT INTO efos_list (rfc, name, situation, publication_date)
        VALUES (:rfc, :name, :situation, :publication_date)
        """,
        data_dict["efos_list"]
    )

    conn.commit()
    conn.close()


# ==============================================================================
# Orquestación y CLI Entrypoint
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generador de Estate Sintético para Forensic Auditor (Fases 1, 2 y 3)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Semilla determinista para random y Faker (default: 42)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/dev_estate.db",
        help="Ruta del archivo de base de datos SQLite (default: data/dev_estate.db)"
    )

    args = parser.parse_args()

    # 1. Semillas para reproducibilidad estricta
    random.seed(args.seed)
    Faker.seed(args.seed)
    fake = Faker("es_MX")

    # 2. Generación Fase 1 (Entidades Base)
    used_clabes = set()
    vendors = generate_vendors(n=40, fake=fake, used_clabes=used_clabes)
    employees = generate_employees(n=15, fake=fake, used_clabes=used_clabes)
    contracts = generate_contracts(vendors, n=20, fake=fake)
    efos = generate_efos(vendors, n=3)

    # 3. Generación Fase 2 (Motor Transaccional)
    pos = generate_purchase_orders(vendors, n=200, fake=fake)
    invoices = generate_invoices(vendors, pos, n=350)
    ledger = generate_ledger(invoices)
    bank_txns = generate_bank_txns(vendors, invoices)

    data_dict = {
        "vendors": vendors,
        "employees": employees,
        "contracts": contracts,
        "efos_list": efos,
        "purchase_orders": pos,
        "invoices": invoices,
        "ledger": ledger,
        "bank_txns": bank_txns
    }

    # 4. Generación Fase 3 (Inyección de Anomalías y Señuelos)
    data_dict, answer_key = inject_anomalies(data_dict)

    # 5. Exportar Ground Truth Answer Key fuera de la base de datos
    answer_key_path = "dev_answer_key.json"
    with open(answer_key_path, "w", encoding="utf-8") as f:
        json.dump(answer_key, f, indent=2, ensure_ascii=False)
    print(f"[+] Ground Truth exportado exitosamente a: {answer_key_path}")

    # 6. Exportar a SQLite
    export_to_sqlite(data_dict, args.output)
    print(f"[+] Base de datos SQLite generada en: {args.output}")

    # 7. Verificación final con SELECT COUNT(*) de cada tabla
    print("\n" + "=" * 60)
    print(f"VERIFICACIÓN DE CONTEO EN BASE DE DATOS: {args.output}")
    print("=" * 60)
    conn = sqlite3.connect(args.output)
    cursor = conn.cursor()
    tables = [
        "vendors", "employees", "contracts", "purchase_orders",
        "invoices", "ledger", "bank_txns", "efos_list"
    ]
    for tbl in tables:
        count = cursor.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  - {tbl:<18}: {count:>5} filas")
    conn.close()
    print("=" * 60)
