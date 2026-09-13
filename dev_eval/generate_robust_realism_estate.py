"""Generate the realism-enhanced adversarial evaluation estate.

DEV / EVAL ONLY.  Nothing under ``src/`` may import this module, and nothing
under ``src/`` may read any file this module writes except the estate database
itself (which carries no answers).

Outputs
-------
dev_eval/robust_realism_estate.db
    The estate.  Exactly the eight official tables of ``estate_schema.sql``.
    No answer key, no scheme labels, no ``is_fraud`` column.  This is the file
    the production auditor is pointed at.

dev_eval/robust_realism_estate_answers.db
    The same eight tables PLUS four ``answer_key_*`` tables.  Evaluation only.

dev_eval/robust_realism_estate_csv.zip
    The CSV form of the clean estate (the judges hand both forms).

dev_eval/robust_realism_ground_truth.json
    Answer key in the official ``ground_truth_schema.json`` shape.

dev_eval/robust_realism_manifest.md
    Human-readable scenario manifest.

dev_eval/evidence_dependency_map.json
    Which records feed which analytics, for Method Critic testing.

Design rules
------------
* Fixed seed.  No ``date.today()``.  No unseeded randomness.  Same seed, same
  bytes.
* Human-readable ids (BNK-, PO-, CTR-, ledger entry_id) are assigned in a final
  chronological pass, AFTER all generation.  If planted scheme rows kept the ids
  they were allocated during generation they would all land at the end of the
  sequence, and id ordinality alone would leak the answer key.
* Public Mexican data shaped the formats and vocabularies (see
  ``dev_eval/source_provenance.md``).  Every entity, RFC, CLABE, name and
  transaction is synthetic.  No invented misconduct is attached to any real
  taxpayer, company or person.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import random
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import date, timedelta
from pathlib import Path

# --------------------------------------------------------------------------
# Fixed parameters
# --------------------------------------------------------------------------

SEED = 20260913

PERIOD_START = date(2024, 1, 1)
PERIOD_END = date(2025, 12, 31)

COMPANY_RFC = "IMX210415K72"
COMPANY_NAME = "Industrias Meridiano de Mexico, S.A. de C.V."

# The internal approval ladder.  This is a COMPANY POLICY modelled in the data,
# not a statute.  It is never written anywhere in the estate: it only shows up
# as the empirical relationship between purchase_orders.amount and
# purchase_orders.approver, which is exactly what an auditor has to infer.
APPROVAL_L1 = 100_000.0   # below this: a Coordinador may approve
APPROVAL_L2 = 500_000.0   # below this: a Gerente may approve; above: a Director

UUID_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

HERE = Path(__file__).resolve().parent
REF = HERE / "external_reference"

OFFICIAL_TABLES = (
    "vendors", "invoices", "ledger", "bank_txns",
    "purchase_orders", "contracts", "employees", "efos_list",
)


# --------------------------------------------------------------------------
# Deterministic primitives
# --------------------------------------------------------------------------

def load_reference(name: str) -> dict:
    return json.loads((REF / name).read_text(encoding="utf-8"))


def iso(d: date) -> str:
    return d.isoformat()


def clabe(bank_code: str, plaza: str, account: str) -> str:
    """Build an 18-digit CLABE with a valid control digit."""
    body = f"{bank_code}{plaza}{account}"
    if len(body) != 17 or not body.isdigit():
        raise ValueError(f"CLABE body must be 17 digits, got {body!r}")
    weights = (3, 7, 1)
    total = sum((int(c) * weights[i % 3]) % 10 for i, c in enumerate(body))
    control = (10 - (total % 10)) % 10
    return f"{body}{control}"


def clabe_is_valid(value: str) -> bool:
    if len(value) != 18 or not value.isdigit():
        return False
    weights = (3, 7, 1)
    total = sum((int(c) * weights[i % 3]) % 10 for i, c in enumerate(value[:17]))
    return (10 - (total % 10)) % 10 == int(value[17])


HOMOCLAVE_ALPHABET = "0123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"


def rfc_moral(rng: random.Random, stem: str, constituted: date) -> str:
    """12-character RFC for a persona moral: 3 letters + YYMMDD + 3 homoclave."""
    letters = "".join(ch for ch in stem.upper() if ch.isalpha())[:3].ljust(3, "X")
    hc = "".join(rng.choice(HOMOCLAVE_ALPHABET) for _ in range(3))
    return f"{letters}{constituted.strftime('%y%m%d')}{hc}"


def rfc_fisica(rng: random.Random, stem: str, born: date) -> str:
    """13-character RFC for a persona fisica: 4 letters + YYMMDD + 3 homoclave."""
    letters = "".join(ch for ch in stem.upper() if ch.isalpha())[:4].ljust(4, "X")
    hc = "".join(rng.choice(HOMOCLAVE_ALPHABET) for _ in range(3))
    return f"{letters}{born.strftime('%y%m%d')}{hc}"


# Invented syllable pool.  Company cores are built by composition so that the
# resulting names are demonstrably manufactured rather than lifted from a real
# registry.
_SYL_A = ["Al", "Ber", "Cor", "Dan", "El", "Fra", "Gal", "Hur", "Ith", "Jal",
          "Kel", "Lum", "Mar", "Nur", "Orb", "Pal", "Quin", "Ras", "Sal",
          "Tor", "Urb", "Val", "Xen", "Yur", "Zan", "Bre", "Cit", "Dov",
          "Erm", "Fel", "Gri", "Hal", "Ind", "Jor", "Lan", "Mel"]
_SYL_B = ["mara", "dena", "tiva", "sola", "renco", "valta", "miro", "nexa",
          "tura", "beda", "risco", "landa", "zeta", "quila", "mendi", "garza",
          "tela", "orza", "vinca", "sante", "dova", "perga", "listo", "nuvia",
          "treza", "bania", "certa", "folia", "gesta", "hondo"]

_LEGAL_FORM = ["S.A. de C.V.", "S. de R.L. de C.V.", "S.A.P.I. de C.V.",
               "S.C.", "S.A. de C.V."]

_PREFIX = ["Grupo", "Corporativo", "Industrias", "Comercializadora",
           "Distribuidora", "Servicios", "Constructora", "Soluciones",
           "Suministros", "Proveedora", "Transportes", "Consultores",
           "Tecnologia", "Ingenieria", "Manufacturas", "Logistica",
           "Operadora", "Desarrollos", "Insumos", "Proyectos"]

_STREETS = ["Av. Lazaro Cardenas", "Av. Constitucion", "Blvd. Diaz Ordaz",
            "Calle Morelos", "Av. Universidad", "Av. Revolucion",
            "Blvd. Bernardo Quintana", "Av. Chapultepec", "Calle Hidalgo",
            "Av. Eugenio Garza Sada", "Carretera a Laredo km 14",
            "Av. Vallarta", "Av. Insurgentes Sur", "Calle Zaragoza",
            "Blvd. Antonio L. Rodriguez", "Av. Gonzalitos",
            "Av. Manuel J. Cloutier", "Prol. Ruiz Cortines"]

# (municipio, estado, border_region)
_PLACES = [
    ("Monterrey", "Nuevo Leon", False),
    ("San Nicolas de los Garza", "Nuevo Leon", False),
    ("Apodaca", "Nuevo Leon", False),
    ("Santa Catarina", "Nuevo Leon", False),
    ("San Pedro Garza Garcia", "Nuevo Leon", False),
    ("Guadalupe", "Nuevo Leon", False),
    ("Saltillo", "Coahuila", False),
    ("Ramos Arizpe", "Coahuila", False),
    ("Ciudad de Mexico", "Ciudad de Mexico", False),
    ("Naucalpan de Juarez", "Estado de Mexico", False),
    ("Tlalnepantla de Baz", "Estado de Mexico", False),
    ("Zapopan", "Jalisco", False),
    ("Guadalajara", "Jalisco", False),
    ("El Marques", "Queretaro", False),
    ("Santiago de Queretaro", "Queretaro", False),
    ("Nuevo Laredo", "Tamaulipas", True),
    ("Reynosa", "Tamaulipas", True),
    ("Ciudad Juarez", "Chihuahua", True),
    ("Tijuana", "Baja California", True),
    ("Mexicali", "Baja California", True),
]


ACCOUNTS = {
    "gasto_admin": ("5100", "Gastos de administracion"),
    "gasto_venta": ("5200", "Gastos de venta"),
    "costo_prod": ("5300", "Costo de produccion"),
    "mantenimiento": ("5400", "Mantenimiento y conservacion"),
    "capex": ("1240", "Maquinaria y equipo"),
    "iva_acreditable": ("1190", "IVA acreditable pagado"),
    "proveedores": ("2110", "Proveedores nacionales"),
    "bancos": ("1120", "Bancos"),
    "clientes": ("1150", "Clientes nacionales"),
    "ingresos": ("4100", "Ingresos por ventas"),
    "iva_trasladado": ("2160", "IVA trasladado cobrado"),
}

COST_CENTERS = [
    "CC-100 Produccion", "CC-200 Mantenimiento", "CC-300 Administracion",
    "CC-400 Logistica", "CC-500 Comercial", "CC-600 Proyectos",
]

CATEGORY_ACCOUNT = {
    "Servicios profesionales": "gasto_admin",
    "Consultoria": "gasto_admin",
    "Servicios de TI": "gasto_admin",
    "Construccion": "capex",
    "Mantenimiento industrial": "mantenimiento",
    "Logistica y transporte": "gasto_venta",
    "Papeleria y consumibles": "gasto_admin",
    "Manufactura y maquinados": "costo_prod",
    "Materiales de construccion": "costo_prod",
    "Seguridad privada": "gasto_admin",
    "Limpieza y facilities": "gasto_admin",
    "Capacitacion": "gasto_admin",
    "Publicidad y medios": "gasto_venta",
    "Alimentos y comedor industrial": "gasto_admin",
    "Equipo de computo": "capex",
    "Refacciones industriales": "mantenimiento",
    "Laboratorio y metrologia": "costo_prod",
    "Empaque y embalaje": "costo_prod",
    "Servicios legales": "gasto_admin",
    "Arrendamiento de equipo": "gasto_admin",
}


class EstateBuilder:
    """Accumulates estate rows.  Human-readable ids are assigned at the end."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.vendors: list[dict] = []
        self.employees: list[dict] = []
        self.efos: list[dict] = []
        self.contracts: list[dict] = []
        self.pos: list[dict] = []
        self.invoices: list[dict] = []
        self.ledger: list[dict] = []
        self.bank: list[dict] = []
        self._uuid_counter = 0
        self._account_counter = 0
        self._clabe_seen: set[str] = set()

    # -- ids ---------------------------------------------------------------

    def next_uuid(self) -> str:
        self._uuid_counter += 1
        return str(uuid.uuid5(UUID_NS, f"cfdi|{SEED}|{self._uuid_counter}"))

    def new_clabe(self, bank_code: str, plaza: str) -> str:
        """Allocate a fresh, structurally valid CLABE."""
        while True:
            self._account_counter += 1
            account = f"{self._account_counter:011d}"
            value = clabe(bank_code, plaza, account)
            if value not in self._clabe_seen:
                self._clabe_seen.add(value)
                return value

    # -- master data -------------------------------------------------------

    def add_vendor(self, rfc, legal_name, registered_date, address,
                   bank_clabe, category, contact_email) -> dict:
        row = {
            "rfc": rfc,
            "legal_name": legal_name,
            "registered_date": iso(registered_date),
            "address": address,
            "bank_clabe": bank_clabe,
            "category": category,
            "contact_email": contact_email,
        }
        self.vendors.append(row)
        return row

    def add_employee(self, emp_id, name, role, bank_clabe, hire_date) -> dict:
        row = {
            "emp_id": emp_id,
            "name": name,
            "role": role,
            "bank_clabe": bank_clabe,
            "hire_date": iso(hire_date),
        }
        self.employees.append(row)
        return row

    def add_efos(self, rfc, legal_name, status, publication_date) -> dict:
        row = {
            "rfc": rfc,
            "legal_name": legal_name,
            "status": status,
            "publication_date": iso(publication_date),
        }
        self.efos.append(row)
        return row

    def add_contract(self, vendor_rfc, start_date, value, scope_text) -> dict:
        row = {
            "contract_id": None,
            "vendor_rfc": vendor_rfc,
            "start_date": iso(start_date),
            "value": round(float(value), 2),
            "scope_text": scope_text,
            "_sort_date": start_date,
        }
        self.contracts.append(row)
        return row

    def add_po(self, vendor_rfc, po_date, amount, requester, approver,
               description) -> dict:
        row = {
            "po_id": None,
            "vendor_rfc": vendor_rfc,
            "date": iso(po_date),
            "amount": round(float(amount), 2),
            "requester": requester,
            "approver": approver,
            "description": description,
            "_sort_date": po_date,
        }
        self.pos.append(row)
        return row

    def add_invoice(self, issuer_rfc, receiver_rfc, issue_date, subtotal,
                    iva_rate, concepto_text, uso_cfdi, forma_pago,
                    metodo_pago, status) -> dict:
        subtotal = round(float(subtotal), 2)
        iva = round(subtotal * iva_rate, 2)
        total = round(subtotal + iva, 2)
        row = {
            "uuid": self.next_uuid(),
            "issuer_rfc": issuer_rfc,
            "receiver_rfc": receiver_rfc,
            "issue_date": iso(issue_date),
            "subtotal": subtotal,
            "iva": iva,
            "total": total,
            "concepto_text": concepto_text,
            "uso_cfdi": uso_cfdi,
            "forma_pago": forma_pago,
            "metodo_pago": metodo_pago,
            "status": status,
            "_sort_date": issue_date,
            "_iva_rate": iva_rate,
        }
        self.invoices.append(row)
        return row

    def add_ledger(self, entry_date, account_key, debit, credit, description,
                   invoice_uuid, cost_center, approver) -> dict:
        code, name = ACCOUNTS[account_key]
        row = {
            "entry_id": None,
            "date": iso(entry_date),
            "account_code": code,
            "account_name": name,
            "debit": round(float(debit), 2),
            "credit": round(float(credit), 2),
            "description": description,
            "invoice_uuid": invoice_uuid,
            "cost_center": cost_center,
            "approver": approver,
            "_sort_date": entry_date,
        }
        self.ledger.append(row)
        return row

    def add_bank(self, txn_date, from_clabe, to_clabe, amount, reference,
                 channel) -> dict:
        row = {
            "txn_id": None,
            "date": iso(txn_date),
            "from_clabe": from_clabe,
            "to_clabe": to_clabe,
            "amount": round(float(amount), 2),
            "reference": reference,
            "channel": channel,
            "_sort_date": txn_date,
        }
        self.bank.append(row)
        return row

    # -- composite bookkeeping --------------------------------------------

    def book_purchase(self, inv, account_key, cost_center, approver,
                      description) -> None:
        """Three-line accrual: expense + creditable VAT against payables."""
        d = date.fromisoformat(inv["issue_date"])
        self.add_ledger(d, account_key, inv["subtotal"], 0.0, description,
                        inv["uuid"], cost_center, approver)
        if inv["iva"] > 0:
            self.add_ledger(d, "iva_acreditable", inv["iva"], 0.0, description,
                            inv["uuid"], cost_center, approver)
        self.add_ledger(d, "proveedores", 0.0, inv["total"], description,
                        inv["uuid"], cost_center, approver)

    def settle_purchase(self, inv, pay_date, from_clabe, to_clabe, cost_center,
                        approver, channel="SPEI", amount=None):
        """Settle a payable.  A payment falling after the cut-off is simply not
        made inside the estate: the payable stays open, which is what a real
        period-end extract looks like."""
        if pay_date > PERIOD_END:
            return None
        amount = inv["total"] if amount is None else round(float(amount), 2)
        ref = "Pago CFDI " + inv["uuid"][:8]
        txn = self.add_bank(pay_date, from_clabe, to_clabe, amount, ref, channel)
        self.add_ledger(pay_date, "proveedores", amount, 0.0,
                        "Pago a proveedor " + inv["issuer_rfc"], inv["uuid"],
                        cost_center, approver)
        self.add_ledger(pay_date, "bancos", 0.0, amount,
                        "Pago a proveedor " + inv["issuer_rfc"], inv["uuid"],
                        cost_center, approver)
        return txn

    def book_sale(self, inv, cost_center, approver, description) -> None:
        d = date.fromisoformat(inv["issue_date"])
        self.add_ledger(d, "clientes", inv["total"], 0.0, description,
                        inv["uuid"], cost_center, approver)
        self.add_ledger(d, "ingresos", 0.0, inv["subtotal"], description,
                        inv["uuid"], cost_center, approver)
        if inv["iva"] > 0:
            self.add_ledger(d, "iva_trasladado", 0.0, inv["iva"], description,
                            inv["uuid"], cost_center, approver)

    def collect_sale(self, inv, pay_date, from_clabe, to_clabe, cost_center,
                     approver, channel="SPEI", amount=None):
        """Collect a receivable.  A collection after the cut-off is left open."""
        if pay_date > PERIOD_END:
            return None
        amount = inv["total"] if amount is None else round(float(amount), 2)
        ref = "Cobro CFDI " + inv["uuid"][:8]
        txn = self.add_bank(pay_date, from_clabe, to_clabe, amount, ref, channel)
        self.add_ledger(pay_date, "bancos", amount, 0.0,
                        "Cobro a cliente " + inv["receiver_rfc"], inv["uuid"],
                        cost_center, approver)
        self.add_ledger(pay_date, "clientes", 0.0, amount,
                        "Cobro a cliente " + inv["receiver_rfc"], inv["uuid"],
                        cost_center, approver)
        return txn

    # -- final id assignment ----------------------------------------------

    def finalize_ids(self) -> None:
        """Assign human-readable ids in chronological order.

        Generation order would otherwise encode the answer key: every planted
        row is created after the ordinary traffic, so a monotonic id would put
        all of the fraud at the end of the id sequence and a detector could
        cheat on ordinality alone.
        """
        by_date = lambda r: r["_sort_date"]
        for i, row in enumerate(sorted(self.contracts, key=by_date), start=1):
            row["contract_id"] = "CTR-%d-%04d" % (row["_sort_date"].year, i)
        for i, row in enumerate(sorted(self.pos, key=by_date), start=1):
            row["po_id"] = "PO-%d-%05d" % (row["_sort_date"].year, i)
        for i, row in enumerate(sorted(self.bank, key=by_date), start=1):
            row["txn_id"] = "BNK-%06d" % i
        for i, row in enumerate(sorted(self.ledger, key=by_date), start=1):
            row["entry_id"] = i


# --------------------------------------------------------------------------
# Fixed CLABEs for the company's own accounts
# --------------------------------------------------------------------------

def fixed_clabe(b: "EstateBuilder", bank: str, plaza: str, account: str) -> str:
    value = clabe(bank, plaza, account)
    if value in b._clabe_seen:
        raise ValueError("fixed CLABE collision: " + value)
    b._clabe_seen.add(value)
    return value


# --------------------------------------------------------------------------
# Amount model
# --------------------------------------------------------------------------

TIERS = {
    "small": (9.6, 0.75, 900.0, 350_000.0),
    "mid": (10.9, 0.80, 3_000.0, 1_400_000.0),
    "large": (12.2, 0.90, 20_000.0, 6_500_000.0),
}

CATEGORY_TIER = {
    "Papeleria y consumibles": "small",
    "Limpieza y facilities": "small",
    "Alimentos y comedor industrial": "small",
    "Seguridad privada": "small",
    "Capacitacion": "small",
    "Servicios profesionales": "mid",
    "Consultoria": "mid",
    "Servicios de TI": "mid",
    "Mantenimiento industrial": "mid",
    "Logistica y transporte": "mid",
    "Publicidad y medios": "mid",
    "Servicios legales": "mid",
    "Arrendamiento de equipo": "mid",
    "Laboratorio y metrologia": "mid",
    "Empaque y embalaje": "mid",
    "Refacciones industriales": "mid",
    "Construccion": "large",
    "Manufactura y maquinados": "large",
    "Materiales de construccion": "large",
    "Equipo de computo": "large",
}


def draw_amount(rng: random.Random, tier: str) -> float:
    """Lognormal draw: many small transactions, a thin tail of large ones."""
    mu, sigma, lo, hi = TIERS[tier]
    value = math.exp(rng.gauss(mu, sigma))
    value = min(max(value, lo), hi)
    roll = rng.random()
    if roll < 0.40:
        value = round(value / 100.0) * 100.0
    elif roll < 0.65:
        value = round(value / 50.0) * 50.0
    else:
        value = round(value, 2)
    return max(value, lo)


def business_day(d: date) -> date:
    """Nudge weekend dates onto the following Monday."""
    if d.weekday() == 5:
        return d + timedelta(days=2)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def clamp_period(d: date) -> date:
    if d < PERIOD_START:
        return PERIOD_START
    if d > PERIOD_END:
        return PERIOD_END
    return d


def add_months(d: date, n: int) -> date:
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
                      else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


# --------------------------------------------------------------------------
# Name / RFC / address construction
# --------------------------------------------------------------------------

GIVEN_NAMES = [
    "Ana", "Luis", "Carmen", "Jorge", "Patricia", "Ricardo", "Gabriela",
    "Fernando", "Alejandra", "Miguel", "Claudia", "Roberto", "Veronica",
    "Hector", "Mariana", "Sergio", "Adriana", "Eduardo", "Leticia", "Raul",
    "Monica", "Javier", "Silvia", "Arturo", "Rocio", "Oscar", "Elena",
    "Guillermo", "Norma", "Ernesto", "Sofia", "Andres", "Julieta", "Pablo",
    "Teresa", "Rodrigo", "Beatriz", "Ignacio", "Lorena", "Emilio",
]

SURNAMES = [
    "Trevino", "Cantu", "Garza", "Villarreal", "Elizondo", "Salinas",
    "Montemayor", "Gutierrez", "Ramos", "Ibarra", "Zamora", "Escamilla",
    "Alanis", "Guajardo", "Leal", "Marroquin", "Saldivar", "Quiroga",
    "Berlanga", "Sepulveda", "Ochoa", "Carrillo", "Peralta", "Mercado",
    "Nieto", "Barragan", "Fuentes", "Olvera", "Rendon", "Zepeda",
]


def person_name(rng: random.Random) -> str:
    return "%s %s %s" % (rng.choice(GIVEN_NAMES), rng.choice(SURNAMES),
                         rng.choice(SURNAMES))


def company_core(rng: random.Random, used: set) -> str:
    for _ in range(500):
        core = rng.choice(_SYL_A) + rng.choice(_SYL_B)
        if core not in used:
            used.add(core)
            return core
    raise RuntimeError("exhausted company core pool")


def slug(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def make_address(rng: random.Random, place=None) -> tuple[str, bool]:
    if place is None:
        place = rng.choice(_PLACES)
    municipio, estado, border = place
    street = rng.choice(_STREETS)
    number = rng.randint(100, 4800)
    if rng.random() < 0.35:
        suffix = ", Int. %d" % rng.randint(1, 40)
    else:
        suffix = ""
    return "%s %d%s, %s, %s" % (street, number, suffix, municipio, estado), border


# --------------------------------------------------------------------------
# World construction
# --------------------------------------------------------------------------

class World:
    """Everything the scheme and decoy planters need to reach."""

    def __init__(self, b: EstateBuilder, refs: dict):
        self.b = b
        self.refs = refs
        self.banks = sorted(refs["bank"]["curated_commercial_bank_subset"].keys())
        self.plazas = sorted(refs["bank"]["plaza_codes"].keys() - {"_note"})
        self.categories = refs["denue"]["vendor_categories_derived"]
        self.po_vocab = refs["procurement"]["description_vocabulary"]
        self.milestones = refs["procurement"]["milestone_vocabulary"]
        self.uso_codes = sorted(refs["cfdi"]["c_UsoCFDI"].keys())
        self.used_cores: set = set()
        self.used_rfcs: set = set()
        self.employees_by_id: dict = {}
        self.vendor_rows: dict = {}
        self.customers: dict = {}

    # -- helpers ----------------------------------------------------------

    def bank_plaza(self):
        rng = self.b.rng
        return rng.choice(self.banks), rng.choice(self.plazas)

    def fresh_rfc_moral(self, stem: str, constituted: date) -> str:
        for _ in range(200):
            value = rfc_moral(self.b.rng, stem, constituted)
            if value not in self.used_rfcs:
                self.used_rfcs.add(value)
                return value
        raise RuntimeError("exhausted RFC space")

    def fresh_rfc_fisica(self, stem: str, born: date) -> str:
        for _ in range(200):
            value = rfc_fisica(self.b.rng, stem, born)
            if value not in self.used_rfcs:
                self.used_rfcs.add(value)
                return value
        raise RuntimeError("exhausted RFC space")

    def new_vendor(self, category=None, place=None, registered=None,
                   core=None, prefix=None, email_domain=None,
                   legal_name=None, rfc=None, bank_clabe=None):
        rng = self.b.rng
        if core is None:
            core = company_core(rng, self.used_cores)
        if prefix is None:
            prefix = rng.choice(_PREFIX)
        if category is None:
            category = rng.choice(self.categories)
        if registered is None:
            registered = date(rng.randint(2009, 2023), rng.randint(1, 12),
                              rng.randint(1, 28))
        if legal_name is None:
            legal_name = "%s %s, %s" % (prefix, core, rng.choice(_LEGAL_FORM))
        if rfc is None:
            rfc = self.fresh_rfc_moral(core, registered)
        address, border = make_address(rng, place)
        if bank_clabe is None:
            bank, plaza = self.bank_plaza()
            bank_clabe = self.b.new_clabe(bank, plaza)
        if email_domain is None:
            email_domain = slug(core) + ".mx"
        email = "%s@%s" % (rng.choice(["contacto", "facturacion", "ventas",
                                       "administracion", "cobranza"]), email_domain)
        row = self.b.add_vendor(rfc, legal_name, registered, address,
                                bank_clabe, category, email)
        row["_border"] = border
        # Zero-rated supplies (certain foodstuffs) exist alongside the 16% and
        # the 8% border rate, so no single VAT identity holds estate-wide.
        row["_exempt"] = (category == "Alimentos y comedor industrial"
                          and rng.random() < 0.6)
        row["_tier"] = CATEGORY_TIER[category]
        self.vendor_rows[rfc] = row
        return row

    def iva_rate_for(self, vendor_row) -> float:
        """16% general; 8% for border-region vendors; 0% for a few exempt ones."""
        rng = self.b.rng
        if vendor_row.get("_exempt"):
            return 0.0
        if vendor_row.get("_border") and rng.random() < 0.55:
            return 0.08
        return 0.16


# --------------------------------------------------------------------------
# Employees
# --------------------------------------------------------------------------

ROLE_PLAN = [
    ("Director General", 1),
    ("Director de Finanzas", 1),
    ("Director de Operaciones", 1),
    ("Gerente de Compras", 1),
    ("Gerente de Planta", 1),
    ("Gerente de Finanzas", 1),
    ("Gerente de Mantenimiento", 1),
    ("Coordinador de Compras", 3),
    ("Analista de Cuentas por Pagar", 3),
    ("Contador General", 1),
    ("Auxiliar Contable", 2),
    ("Supervisor de Mantenimiento", 2),
    ("Jefe de Almacen", 2),
    ("Ingeniero de Proyectos", 3),
    ("Coordinador de Logistica", 2),
    ("Analista de Compras", 3),
    ("Supervisor de Produccion", 4),
    ("Coordinador de Calidad", 2),
    ("Especialista de Recursos Humanos", 2),
    ("Ejecutivo de Ventas", 4),
    ("Coordinador de Seguridad e Higiene", 1),
    ("Analista de Sistemas", 2),
    ("Coordinador de Capacitacion", 1),
    ("Tecnico de Metrologia", 2),
]

DIRECTOR_ROLES = {"Director General", "Director de Finanzas",
                  "Director de Operaciones"}
GERENTE_ROLES = {"Gerente de Compras", "Gerente de Planta",
                 "Gerente de Finanzas", "Gerente de Mantenimiento"}
COORDINATOR_ROLES = {"Coordinador de Compras"}
REQUESTER_ROLES = {"Supervisor de Mantenimiento", "Jefe de Almacen",
                   "Ingeniero de Proyectos", "Coordinador de Logistica",
                   "Analista de Compras", "Supervisor de Produccion",
                   "Coordinador de Calidad", "Coordinador de Capacitacion",
                   "Analista de Sistemas", "Coordinador de Seguridad e Higiene",
                   "Tecnico de Metrologia"}
ACCOUNTING_ROLES = {"Contador General", "Auxiliar Contable",
                    "Analista de Cuentas por Pagar"}


def build_employees(w: World) -> None:
    b, rng = w.b, w.b.rng
    used_names: set = set()
    idx = 0
    for role, count in ROLE_PLAN:
        for _ in range(count):
            idx += 1
            for _try in range(400):
                name = person_name(rng)
                if name not in used_names:
                    used_names.add(name)
                    break
            else:
                raise RuntimeError("exhausted employee name pool")
            hire = date(rng.randint(2011, 2023), rng.randint(1, 12),
                        rng.randint(1, 28))
            bank, plaza = w.bank_plaza()
            emp = b.add_employee("EMP:%04d" % idx, name, role,
                                 b.new_clabe(bank, plaza), hire)
            w.employees_by_id[emp["emp_id"]] = emp

    w.directors = [e for e in b.employees if e["role"] in DIRECTOR_ROLES]
    w.gerentes = [e for e in b.employees if e["role"] in GERENTE_ROLES]
    w.coordinators = [e for e in b.employees if e["role"] in COORDINATOR_ROLES]
    w.requesters = [e for e in b.employees if e["role"] in REQUESTER_ROLES]
    w.accounting = [e for e in b.employees if e["role"] in ACCOUNTING_ROLES]


def approver_for(w: World, amount: float) -> str:
    """The internal approval ladder, with a realistic amount of leakage.

    Roughly one PO in twenty is signed one level above where the ladder would
    put it (holidays, delegation, a director who happened to be in the room).
    That leakage is deliberate: an auditor that infers a perfectly crisp
    threshold from this estate has over-fitted.
    """
    rng = w.b.rng
    if amount < APPROVAL_L1:
        pool = w.coordinators if rng.random() > 0.06 else w.gerentes
    elif amount < APPROVAL_L2:
        pool = w.gerentes if rng.random() > 0.05 else w.directors
    else:
        pool = w.directors
    return rng.choice(pool)["name"]


def requester_for(w: World) -> str:
    return w.b.rng.choice(w.requesters)["name"]


def booking_approver(w: World) -> str:
    """Who signed the GL entry.  Sometimes nobody did."""
    rng = w.b.rng
    if rng.random() < 0.05:
        return ""
    return rng.choice(w.accounting)["name"]


# --------------------------------------------------------------------------
# Ordinary purchase traffic
# --------------------------------------------------------------------------

def payment_terms(w: World):
    """Return (metodo_pago, forma_pago, settlement delay in days)."""
    rng = w.b.rng
    if rng.random() < 0.70:
        forma = rng.choices(["03", "02", "04", "28", "01"],
                            weights=[80, 8, 6, 4, 2], k=1)[0]
        return "PUE", forma, rng.randint(8, 35)
    return "PPD", "99", rng.randint(40, 95)


def emit_purchase(w: World, vendor, issue: date, subtotal: float,
                  concepto: str, cost_center: str, *, with_po: bool,
                  po_description: str | None = None, uso: str | None = None,
                  status: str = "vigente", pay: bool = True,
                  partial: bool = False):
    """One ordinary supplier invoice end to end: PO, CFDI, GL, settlement."""
    b, rng = w.b, w.b.rng
    metodo, forma, delay = payment_terms(w)
    rate = w.iva_rate_for(vendor)
    if uso is None:
        account_key = CATEGORY_ACCOUNT[vendor["category"]]
        if account_key == "capex":
            uso = rng.choices(["I08", "I04", "I02", "I01"],
                              weights=[70, 16, 8, 6], k=1)[0]
        elif account_key == "costo_prod":
            uso = "G01"
        else:
            # S01 and P01 are valid codes that turn up rarely on real supplier
            # invoices.  A detector that treats a rare uso_cfdi as suspicious
            # will produce a false positive here.
            uso = rng.choices(["G03", "G01", "I06", "S01", "P01"],
                              weights=[86, 6, 4, 2, 2], k=1)[0]
    inv = b.add_invoice(vendor["rfc"], COMPANY_RFC, issue, subtotal, rate,
                        concepto, uso, forma, metodo, status)

    po = None
    if with_po:
        po_date = business_day(issue - timedelta(days=rng.randint(2, 25)))
        po_date = clamp_period(po_date)
        po = b.add_po(vendor["rfc"], po_date, inv["total"], requester_for(w),
                      approver_for(w, inv["total"]),
                      po_description or concepto)

    if status == "cancelado":
        return inv, po, []

    account = CATEGORY_ACCOUNT[vendor["category"]]
    b.book_purchase(inv, account, cost_center, booking_approver(w), concepto)

    txns = []
    if pay:
        pay_date = business_day(issue + timedelta(days=delay))
        if pay_date <= PERIOD_END:
            approver = booking_approver(w)
            if partial:
                first = round(inv["total"] * 0.4, 2)
                txns.append(b.settle_purchase(
                    inv, pay_date, COMPANY_OPERATING, vendor["bank_clabe"],
                    cost_center, approver, amount=first))
                second_date = business_day(pay_date + timedelta(days=rng.randint(20, 45)))
                if second_date <= PERIOD_END:
                    txns.append(b.settle_purchase(
                        inv, second_date, COMPANY_OPERATING,
                        vendor["bank_clabe"], cost_center, approver,
                        amount=round(inv["total"] - first, 2)))
            else:
                channel = "cheque" if rng.random() < 0.04 else "SPEI"
                txns.append(b.settle_purchase(
                    inv, pay_date, COMPANY_OPERATING, vendor["bank_clabe"],
                    cost_center, approver, channel=channel))
    return inv, po, [t for t in txns if t is not None]


def build_ordinary_vendor(w: World, vendor) -> None:
    b, rng = w.b, w.b.rng
    tier = vendor["_tier"]
    cost_center = rng.choice(COST_CENTERS)
    shape = rng.choices(["recurrente", "proyecto", "transaccional"],
                        weights=[30, 20, 50], k=1)[0]

    if shape == "recurrente":
        fee = draw_amount(rng, tier)
        months = rng.randint(8, 24)
        start = clamp_period(date(rng.choice([2024, 2024, 2025]),
                                  rng.randint(1, 12), rng.randint(1, 5)))
        scope = "%s. Contrato marco, cuota mensual fija por %d meses." % (
            rng.choice(w.po_vocab), months)
        b.add_contract(vendor["rfc"], start, round(fee * 1.16 * months, 2), scope)
        concepto = rng.choice(w.po_vocab)
        for m in range(months):
            issue = clamp_period(business_day(add_months(start, m) + timedelta(days=rng.randint(0, 4))))
            if issue > PERIOD_END:
                break
            emit_purchase(w, vendor, issue, fee, concepto + " - mensualidad",
                          cost_center, with_po=rng.random() < 0.55,
                          po_description=concepto + " - orden mensual")

    elif shape == "proyecto":
        total = draw_amount(rng, tier) * rng.randint(3, 6)
        start = clamp_period(date(rng.choice([2024, 2024, 2025]),
                                  rng.randint(1, 10), rng.randint(1, 28)))
        scope = "%s. Contrato por proyecto pagado contra entregables." % rng.choice(w.po_vocab)
        b.add_contract(vendor["rfc"], start, round(total * 1.16, 2), scope)
        n = rng.randint(3, 6)
        share = [rng.uniform(0.6, 1.4) for _ in range(n)]
        norm = sum(share)
        for i in range(n):
            issue = clamp_period(business_day(start + timedelta(days=30 * (i + 1) + rng.randint(0, 12))))
            if issue > PERIOD_END:
                break
            emit_purchase(w, vendor, issue, round(total * share[i] / norm, 2),
                          "%s - %s" % (rng.choice(w.po_vocab), w.milestones[i % len(w.milestones)]),
                          cost_center, with_po=True)

    else:
        n = rng.randint(2, 14)
        has_contract = rng.random() < 0.45
        if has_contract:
            start = clamp_period(date(rng.choice([2023, 2024, 2025]),
                                      rng.randint(1, 12), rng.randint(1, 28)))
            b.add_contract(vendor["rfc"], start,
                           round(draw_amount(rng, tier) * rng.randint(4, 12), 2),
                           "%s. Contrato abierto, ordenes segun demanda." % rng.choice(w.po_vocab))
        for _ in range(n):
            offset = rng.randint(0, (PERIOD_END - PERIOD_START).days)
            issue = business_day(PERIOD_START + timedelta(days=offset))
            if issue > PERIOD_END:
                continue
            status = "cancelado" if rng.random() < 0.03 else "vigente"
            emit_purchase(w, vendor, issue, draw_amount(rng, tier),
                          rng.choice(w.po_vocab), cost_center,
                          with_po=rng.random() < 0.70, status=status,
                          pay=rng.random() > 0.06,
                          partial=rng.random() < 0.08)


# --------------------------------------------------------------------------
# Ordinary sales traffic (the company as CFDI issuer)
# --------------------------------------------------------------------------

# Month weights: a manufacturer's ordinary seasonality.  November and December
# are genuinely heavier, which is why a year-end invoice concentration is not
# by itself evidence of anything.
MONTH_WEIGHT = [0.6, 0.8, 1.0, 1.0, 1.05, 1.1, 0.9, 0.95, 1.1, 1.15, 1.35, 1.4]

SALES_CONCEPTS = [
    "Venta de componentes maquinados",
    "Venta de subensambles metalmecanicos",
    "Servicio de maquilado industrial",
    "Venta de refacciones de linea",
    "Servicio de tratamiento termico",
    "Venta de herramentales",
    "Servicio de acabado superficial",
]


def build_customers(w: World, n: int = 22) -> None:
    b, rng = w.b, w.b.rng
    for i in range(n):
        core = company_core(rng, w.used_cores)
        constituted = date(rng.randint(2006, 2021), rng.randint(1, 12),
                           rng.randint(1, 28))
        rfc = w.fresh_rfc_moral(core, constituted)
        bank, plaza = w.bank_plaza()
        w.customers[rfc] = {
            "rfc": rfc,
            "legal_name": "%s %s, %s" % (rng.choice(_PREFIX), core,
                                         rng.choice(_LEGAL_FORM)),
            "bank_clabe": b.new_clabe(bank, plaza),
        }


def build_ordinary_sales(w: World) -> None:
    b, rng = w.b, w.b.rng
    customer_rfcs = sorted(w.customers)
    for rfc in customer_rfcs:
        cust = w.customers[rfc]
        n = rng.randint(6, 20)
        for _ in range(n):
            year = rng.choice([2024, 2025])
            month = rng.choices(range(1, 13), weights=MONTH_WEIGHT, k=1)[0]
            day = rng.randint(1, 28)
            issue = business_day(date(year, month, day))
            if issue > PERIOD_END:
                continue
            subtotal = draw_amount(rng, "mid")
            metodo, forma, delay = payment_terms(w)
            status = "cancelado" if rng.random() < 0.025 else "vigente"
            concepto = rng.choice(SALES_CONCEPTS)
            inv = b.add_invoice(COMPANY_RFC, rfc, issue, subtotal, 0.16,
                                concepto, "G01", forma, metodo, status)
            if status == "cancelado":
                continue
            b.book_sale(inv, "CC-500 Comercial", booking_approver(w), concepto)
            if rng.random() < 0.93:
                pay_date = business_day(issue + timedelta(days=delay))
                if pay_date <= PERIOD_END:
                    b.collect_sale(inv, pay_date, cust["bank_clabe"],
                                   COMPANY_TREASURY, "CC-500 Comercial",
                                   booking_approver(w))


# --------------------------------------------------------------------------
# EFOS background
# --------------------------------------------------------------------------

# The SAT publishes 69-B listings in the DOF in batches, so publication dates
# cluster on a handful of days per year instead of spreading uniformly.
DOF_PUBLICATION_DATES = [
    date(2023, 2, 17), date(2023, 6, 9), date(2023, 9, 15), date(2023, 11, 24),
    date(2024, 3, 8), date(2024, 5, 31), date(2024, 8, 23), date(2024, 12, 6),
    date(2025, 3, 14), date(2025, 7, 4), date(2025, 10, 3), date(2025, 12, 12),
]


def build_efos_background(w: World, n: int = 55) -> None:
    """Synthetic 69-B-shaped entities with no relationship to the estate.

    These are the overwhelming majority of the list, exactly as in reality: a
    national listing is mostly taxpayers a given company has never traded with.
    """
    b, rng = w.b, w.b.rng
    for _ in range(n):
        core = company_core(rng, w.used_cores)
        constituted = date(rng.randint(2014, 2023), rng.randint(1, 12),
                           rng.randint(1, 28))
        rfc = w.fresh_rfc_moral(core, constituted)
        pub = rng.choice(DOF_PUBLICATION_DATES)
        status = "definitivo" if rng.random() < 0.62 else "presunto"
        b.add_efos(rfc, "%s %s, %s" % (rng.choice(_PREFIX), core,
                                       rng.choice(_LEGAL_FORM)),
                   status, pub)


# --------------------------------------------------------------------------
# Answer key accumulator (never written into the clean estate)
# --------------------------------------------------------------------------

class GroundTruth:
    def __init__(self):
        self.schemes: list[dict] = []
        self.decoys: list[dict] = []

    def scheme(self, **kw) -> dict:
        self.schemes.append(kw)
        return kw

    def decoy(self, **kw) -> dict:
        self.decoys.append(kw)
        return kw


# --------------------------------------------------------------------------
# S1 - phantom_vendor (the strong, reportable case)
# --------------------------------------------------------------------------

SHARED_TOWER = ("Av. Lazaro Cardenas 2400, Piso 12, San Pedro Garza Garcia, "
                "Nuevo Leon")


def plant_phantom_vendor(w: World, gt: GroundTruth) -> dict:
    b, rng = w.b, w.b.rng
    registered = date(2024, 7, 12)
    rfc = w.fresh_rfc_moral("BER", registered)
    vendor = b.add_vendor(
        rfc,
        "Corporativo Berlanda Servicios Integrales, S.A. de C.V.",
        registered,
        SHARED_TOWER,
        b.new_clabe("127", "580"),
        "Consultoria",
        "facturacion.berlanda2024@mail-corporativo.mx",
    )
    vendor["_border"] = False
    vendor["_tier"] = "mid"
    w.vendor_rows[rfc] = vendor

    publication = date(2025, 3, 14)
    b.add_efos(rfc, "Corporativo Berlanda Servicios Integrales, S.A. de C.V.",
               "definitivo", publication)

    schedule = [
        (date(2025, 4, 9), 312_500.00, "Servicios profesionales de consultoria estrategica"),
        (date(2025, 4, 28), 268_000.00, "Servicios profesionales de consultoria estrategica"),
        (date(2025, 5, 21), 415_000.00, "Asesoria en reingenieria de procesos"),
        (date(2025, 6, 17), 289_500.00, "Servicios profesionales de consultoria estrategica"),
        (date(2025, 7, 15), 372_000.00, "Asesoria en reingenieria de procesos"),
        (date(2025, 8, 19), 305_000.00, "Servicios profesionales de consultoria estrategica"),
        (date(2025, 9, 18), 448_000.00, "Diagnostico organizacional y plan de implementacion"),
    ]
    invoices, txns, pos = [], [], []
    for i, (issue, subtotal, concepto) in enumerate(schedule):
        inv = b.add_invoice(rfc, COMPANY_RFC, issue, subtotal, 0.16, concepto,
                            "G03", "03", "PUE", "vigente")
        invoices.append(inv)
        b.book_purchase(inv, "gasto_admin", "CC-300 Administracion",
                        booking_approver(w), concepto)
        # Settled unusually fast for a consulting invoice of this size.
        pay_date = business_day(issue + timedelta(days=[6, 5, 7, 6, 9, 5, 6][i]))
        txns.append(b.settle_purchase(inv, pay_date, COMPANY_OPERATING,
                                      vendor["bank_clabe"],
                                      "CC-300 Administracion",
                                      booking_approver(w)))
        # Only three of the seven carry a purchase order, and each of those was
        # raised on the same day as the invoice it is supposed to authorise.
        if i in (1, 3, 5):
            pos.append(b.add_po(rfc, issue, inv["total"],
                                w.requesters[0]["name"],
                                w.gerentes[0]["name"],
                                concepto))

    peso = round(sum(inv["total"] for inv in invoices), 2)
    return gt.scheme(
        scheme_id="S1_phantom_vendor_1",
        type="phantom_vendor",
        entities=["RFC:" + rfc],
        _invoices=invoices,
        _txns=txns,
        _pos=pos,
        _efos_rfcs=[rfc],
        peso_amount=peso,
        difficulty="easy",
        _vendor=vendor,
        _publication=publication,
    )


# --------------------------------------------------------------------------
# S2 - kickback (entangled with S4 through the approval chain)
# --------------------------------------------------------------------------

def plant_kickback(w: World, gt: GroundTruth) -> dict:
    b, rng = w.b, w.b.rng
    registered = date(2019, 3, 26)
    rfc = w.fresh_rfc_moral("ARD", registered)
    vendor = b.add_vendor(
        rfc,
        "Proyectos Ardilonda Industrial, S.A. de C.V.",
        registered,
        "Carretera a Laredo km 14 3180, Apodaca, Nuevo Leon",
        b.new_clabe("072", "580"),
        "Mantenimiento industrial",
        "administracion@ardilonda.mx",
    )
    vendor["_border"] = False
    vendor["_tier"] = "large"
    w.vendor_rows[rfc] = vendor

    director = w.employees_by_id["EMP:0003"]
    b.add_contract(rfc, date(2024, 2, 1), 9_280_000.00,
                   "Servicio de mantenimiento preventivo y correctivo de linea. "
                   "Contrato abierto, ordenes segun demanda.")

    rounds = [
        (date(2024, 5, 14), 1_240_000.00, 0.070, 5),
        (date(2024, 9, 10), 980_000.00, 0.075, 8),
        (date(2025, 1, 21), 1_465_000.00, 0.068, 4),
        (date(2025, 4, 15), 1_120_000.00, 0.072, 9),
        (date(2025, 8, 12), 1_690_000.00, 0.071, 6),
    ]
    invoices, pay_txns, kick_txns, pos = [], [], [], []
    for issue, subtotal, pct, lag in rounds:
        concepto = "Mantenimiento mayor de linea de produccion"
        inv = b.add_invoice(rfc, COMPANY_RFC, issue, subtotal, 0.16, concepto,
                            "G03", "03", "PUE", "vigente")
        invoices.append(inv)
        po_date = business_day(issue - timedelta(days=11))
        pos.append(b.add_po(rfc, po_date, inv["total"], requester_for(w),
                            director["name"],
                            "Servicio de mantenimiento preventivo y correctivo"))
        b.book_purchase(inv, "mantenimiento", "CC-200 Mantenimiento",
                        booking_approver(w), concepto)
        pay_date = business_day(issue + timedelta(days=18))
        pay_txns.append(b.settle_purchase(inv, pay_date, COMPANY_OPERATING,
                                          vendor["bank_clabe"],
                                          "CC-200 Mantenimiento",
                                          booking_approver(w)))
        kick_date = business_day(pay_date + timedelta(days=lag))
        kick_txns.append(b.add_bank(
            kick_date, vendor["bank_clabe"], director["bank_clabe"],
            round(inv["total"] * pct, 2),
            "Transferencia " + kick_date.strftime("%Y%m"), "SPEI"))

    peso = round(sum(t["amount"] for t in kick_txns), 2)
    return gt.scheme(
        scheme_id="S2_kickback_1",
        type="kickback",
        entities=["RFC:" + rfc, "EMP:0003"],
        _invoices=invoices,
        _txns=kick_txns,
        _payment_txns=pay_txns,
        _pos=pos,
        peso_amount=peso,
        difficulty="medium",
        _vendor=vendor,
        _employee=director,
    )


# --------------------------------------------------------------------------
# S3 / S5 - round_tripping entangled with revenue_inflation
# --------------------------------------------------------------------------

def plant_round_tripping_and_revenue_inflation(w: World, gt: GroundTruth):
    b, rng = w.b, w.b.rng

    # The conduit supplier.
    sup_reg = date(2022, 11, 8)
    sup_rfc = w.fresh_rfc_moral("QUI", sup_reg)
    supplier = b.add_vendor(
        sup_rfc, "Suministros Quilanda, S.A. de C.V.", sup_reg,
        "Av. Constitucion 1820, Int. 7, Monterrey, Nuevo Leon",
        b.new_clabe("021", "580"), "Materiales de construccion",
        "cobranza@quilanda.mx")
    supplier["_border"] = False
    supplier["_tier"] = "large"
    w.vendor_rows[sup_rfc] = supplier

    # The related party.  It is both a registered supplier and a customer,
    # which is ordinary in a group structure and is exactly why the cycle is
    # not self-evidently improper.
    kab_reg = date(2023, 5, 30)
    kab_rfc = w.fresh_rfc_moral("KAB", kab_reg)
    kabrel = b.add_vendor(
        kab_rfc, "Distribuidora Kabrelisto, S.A. de C.V.", kab_reg,
        SHARED_TOWER, b.new_clabe("012", "580"),
        "Logistica y transporte", "tesoreria@kabrelisto.mx")
    kabrel["_border"] = False
    kabrel["_tier"] = "large"
    w.vendor_rows[kab_rfc] = kabrel
    w.customers[kab_rfc] = {"rfc": kab_rfc, "legal_name": kabrel["legal_name"],
                            "bank_clabe": kabrel["bank_clabe"]}

    cycle_rounds = [
        (date(2025, 5, 12), 2_340_000.00, date(2025, 5, 15), 2_310_000.00,
         date(2025, 5, 19), 2_285_000.00),
        (date(2025, 8, 11), 1_880_000.00, date(2025, 8, 14), 1_856_000.00,
         date(2025, 8, 18), 1_835_000.00),
        (date(2025, 11, 10), 2_610_000.00, date(2025, 11, 13), 2_578_000.00,
         date(2025, 11, 17), 2_548_000.00),
    ]

    rt_invoices, rt_txns, rt_sales, rt_context_txns = [], [], [], []
    for d1, a1, d2, a2, d3, a3 in cycle_rounds:
        # Leg 1 - the company pays the conduit supplier against a CFDI.
        concepto = "Suministro de material de empaque a granel"
        sub = round(a1 / 1.16, 2)
        inv = b.add_invoice(sup_rfc, COMPANY_RFC, business_day(d1 - timedelta(days=4)),
                            sub, 0.16, concepto, "G01", "03", "PUE", "vigente")
        rt_invoices.append(inv)
        b.add_po(sup_rfc, business_day(d1 - timedelta(days=9)), inv["total"],
                 requester_for(w), approver_for(w, inv["total"]), concepto)
        b.book_purchase(inv, "costo_prod", "CC-100 Produccion",
                        booking_approver(w), concepto)
        rt_txns.append(b.settle_purchase(inv, business_day(d1),
                                         COMPANY_OPERATING,
                                         supplier["bank_clabe"],
                                         "CC-100 Produccion",
                                         booking_approver(w),
                                         amount=a1))
        # Leg 2 - supplier to the related party.  No CFDI of ours covers this;
        # we only ever see our own invoices.  This leg is CONTEXT: it is part of
        # the story but not part of the pesos that left the audited company, and
        # a finding that adds it to the claim cannot reconcile per table.
        rt_context_txns.append(b.add_bank(
            business_day(d2), supplier["bank_clabe"], kabrel["bank_clabe"], a2,
            "Pago proveedor " + d2.strftime("%Y%m"), "SPEI"))
        # Leg 3 - the related party settles a sales invoice of ours, closing
        # the loop into the same account the money left from.
        sale_sub = round(a3 / 1.16, 2)
        sale = b.add_invoice(COMPANY_RFC, kab_rfc,
                             business_day(d3 - timedelta(days=6)), sale_sub,
                             0.16, "Venta de subensambles metalmecanicos",
                             "G01", "03", "PUE", "vigente")
        rt_sales.append(sale)
        b.book_sale(sale, "CC-500 Comercial", booking_approver(w),
                    "Venta de subensambles metalmecanicos")
        rt_context_txns.append(b.collect_sale(
            sale, business_day(d3), kabrel["bank_clabe"], COMPANY_OPERATING,
            "CC-500 Comercial", booking_approver(w), amount=a3))

    rt = gt.scheme(
        scheme_id="S3_round_tripping_1",
        type="round_tripping",
        entities=["RFC:" + COMPANY_RFC, "RFC:" + sup_rfc, "RFC:" + kab_rfc],
        _invoices=rt_invoices,
        _txns=rt_txns,
        _context_invoices=rt_sales,
        _context_txns=rt_context_txns,
        peso_amount=round(sum(r[1] for r in cycle_rounds), 2),
        difficulty="medium",
        _supplier=supplier,
        _kabrel=kabrel,
        _cycle_accounts=[COMPANY_OPERATING, supplier["bank_clabe"],
                         kabrel["bank_clabe"]],
    )

    # ---- S5: revenue recognised at period close and never collected -------
    sib_reg = date(2023, 9, 14)
    sib_rfc = w.fresh_rfc_moral("MEL", sib_reg)
    sibling = b.add_vendor(
        sib_rfc, "Comercializadora Melvinca, S.A. de C.V.", sib_reg,
        SHARED_TOWER, b.new_clabe("012", "580"), "Logistica y transporte",
        "tesoreria@melvinca.mx")
    sibling["_border"] = False
    sibling["_tier"] = "large"
    w.vendor_rows[sib_rfc] = sibling
    w.customers[sib_rfc] = {"rfc": sib_rfc, "legal_name": sibling["legal_name"],
                            "bank_clabe": sibling["bank_clabe"]}

    close_sales = [
        (kab_rfc, date(2025, 6, 24), 1_186_000.00, "vigente"),
        (kab_rfc, date(2025, 6, 27), 1_342_500.00, "cancelado"),
        (kab_rfc, date(2025, 6, 30), 1_075_000.00, "cancelado"),
        (kab_rfc, date(2025, 6, 30), 1_418_000.00, "vigente"),
        (sib_rfc, date(2025, 12, 22), 1_524_000.00, "cancelado"),
        (sib_rfc, date(2025, 12, 26), 1_260_000.00, "vigente"),
        (sib_rfc, date(2025, 12, 29), 1_680_000.00, "cancelado"),
        (sib_rfc, date(2025, 12, 30), 1_395_000.00, "cancelado"),
        (sib_rfc, date(2025, 12, 30), 1_148_000.00, "vigente"),
    ]
    ri_invoices = []
    for receiver, issue, subtotal, status in close_sales:
        concepto = "Venta de componentes maquinados - pedido consolidado"
        inv = b.add_invoice(COMPANY_RFC, receiver, issue, subtotal, 0.16,
                            concepto, "G01", "99", "PPD", status)
        ri_invoices.append(inv)
        # Revenue is booked at issue.  Nothing in the estate reverses the
        # entries for the invoices that were later cancelled, and no bank
        # transaction ever settles any of them.
        b.book_sale(inv, "CC-500 Comercial", booking_approver(w), concepto)

    ri = gt.scheme(
        scheme_id="S5_revenue_inflation_1",
        type="revenue_inflation",
        entities=["RFC:" + COMPANY_RFC, "RFC:" + kab_rfc, "RFC:" + sib_rfc],
        _invoices=ri_invoices,
        _txns=[],
        peso_amount=round(sum(inv["total"] for inv in ri_invoices), 2),
        difficulty="hard",
        _kabrel=kabrel,
        _sibling=sibling,
    )
    return rt, ri


# --------------------------------------------------------------------------
# S4 - threshold_splitting
# --------------------------------------------------------------------------

def plant_threshold_splitting(w: World, gt: GroundTruth) -> dict:
    b, rng = w.b, w.b.rng
    registered = date(2021, 6, 3)
    rfc = w.fresh_rfc_moral("OND", registered)
    vendor = b.add_vendor(
        rfc, "Suministros Ondarreta Industrial, S. de R.L. de C.V.", registered,
        "Blvd. Diaz Ordaz 980, Santa Catarina, Nuevo Leon",
        b.new_clabe("014", "580"), "Materiales de construccion",
        "ventas@ondarreta.mx")
    vendor["_border"] = False
    vendor["_tier"] = "mid"
    w.vendor_rows[rfc] = vendor

    coordinator = w.employees_by_id["EMP:0009"]
    engineer = w.employees_by_id["EMP:0018"]

    slices = [
        (date(2025, 2, 17), 96_400.00, "Adecuacion de nave industrial - seccion A"),
        (date(2025, 2, 18), 98_750.00, "Adecuacion de nave industrial - seccion B"),
        (date(2025, 2, 20), 97_200.00, "Adecuacion de nave industrial - seccion C"),
        (date(2025, 2, 24), 99_180.00, "Adecuacion de nave industrial - seccion D"),
        (date(2025, 2, 25), 96_900.00, "Adecuacion de nave industrial - seccion E"),
        (date(2025, 2, 27), 98_400.00, "Adecuacion de nave industrial - seccion F"),
    ]
    invoices, txns, pos = [], [], []
    for po_date, total, description in slices:
        pos.append(b.add_po(rfc, po_date, total, engineer["name"],
                            coordinator["name"], description))
        issue = business_day(po_date + timedelta(days=rng.randint(2, 6)))
        inv = b.add_invoice(rfc, COMPANY_RFC, issue, round(total / 1.16, 2),
                            0.16, description, "G01", "03", "PUE", "vigente")
        invoices.append(inv)
        b.book_purchase(inv, "costo_prod", "CC-600 Proyectos",
                        booking_approver(w), description)
        txns.append(b.settle_purchase(inv, business_day(issue + timedelta(days=21)),
                                      COMPANY_OPERATING, vendor["bank_clabe"],
                                      "CC-600 Proyectos", booking_approver(w)))

    return gt.scheme(
        scheme_id="S4_threshold_splitting_1",
        type="threshold_splitting",
        entities=["RFC:" + rfc, "EMP:0009"],
        _invoices=invoices,
        _txns=txns,
        _pos=pos,
        peso_amount=round(sum(inv["total"] for inv in invoices), 2),
        difficulty="medium",
        _vendor=vendor,
        _coordinator=coordinator,
        _engineer=engineer,
    )


# --------------------------------------------------------------------------
# Decoys - honest entities engineered to trip a detector
# --------------------------------------------------------------------------

def _simple_vendor(w: World, stem, legal_name, registered, address, category,
                   email, bank="044", plaza="580", tier=None, clabe_value=None):
    b = w.b
    rfc = w.fresh_rfc_moral(stem, registered)
    row = b.add_vendor(rfc, legal_name, registered, address,
                       clabe_value or b.new_clabe(bank, plaza), category, email)
    row["_border"] = False
    row["_tier"] = tier or CATEGORY_TIER[category]
    w.vendor_rows[rfc] = row
    return row


def plant_decoys(w: World, gt: GroundTruth, s1: dict) -> None:
    b, rng = w.b, w.b.rng

    # -- D1: brand-new vendor, identical repeating invoices, no PO, but the
    #        framework contract in the estate explains every peso.
    v = _simple_vendor(w, "NUV", "Servicios Nuvialta, S. de R.L. de C.V.",
                       date(2025, 8, 4),
                       "Av. Universidad 1140, Int. 3, Monterrey, Nuevo Leon",
                       "Servicios de TI", "facturacion@nuvialta.mx")
    b.add_contract(v["rfc"], date(2025, 8, 15), 1_479_040.00,
                   "Licenciamiento y soporte de software. Contrato marco, "
                   "cuota mensual fija de 106,400.00 MXN mas IVA, vigencia "
                   "12 meses, facturacion sin orden de compra por acuerdo de "
                   "clausula quinta.")
    d1_inv = []
    for m in range(5):
        issue = business_day(date(2025, 8, 20) + timedelta(days=30 * m))
        if issue > PERIOD_END:
            break
        inv = b.add_invoice(v["rfc"], COMPANY_RFC, issue, 106_400.00, 0.16,
                            "Licenciamiento y soporte de software - mensualidad",
                            "G03", "03", "PUE", "vigente")
        d1_inv.append(inv)
        b.book_purchase(inv, "gasto_admin", "CC-300 Administracion",
                        booking_approver(w), "Licenciamiento mensual")
        b.settle_purchase(inv, business_day(issue + timedelta(days=22)),
                          COMPANY_OPERATING, v["bank_clabe"],
                          "CC-300 Administracion", booking_approver(w))
    gt.decoy(entity="RFC:" + v["rfc"], signal="new_vendor_repeating_invoices_no_po",
             why_innocent="Contract for this vendor states a fixed monthly fee of "
                          "106,400.00 MXN and waives the purchase order; every "
                          "invoice matches the fee exactly.",
             invoices=d1_inv, _code="D1")

    # -- D2: near-identical legal name to the phantom vendor, different RFC,
    #        not on the EFOS list, fully documented.
    twin = _simple_vendor(w, "BER",
                          "Grupo Berlandia Servicios Integrales, S. de R.L. de C.V.",
                          date(2019, 3, 4),
                          "Av. Vallarta 3120, Int. 11, Zapopan, Jalisco",
                          "Consultoria", "contacto@berlandia.mx")
    b.add_contract(twin["rfc"], date(2024, 1, 15), 4_060_000.00,
                   "Servicios profesionales de consultoria. Contrato marco, "
                   "cuota mensual fija, vigencia 24 meses.")
    d2_inv = []
    for m in range(0, 22, 2):
        issue = business_day(add_months(date(2024, 1, 22), m))
        if issue > PERIOD_END:
            break
        inv = b.add_invoice(twin["rfc"], COMPANY_RFC, issue, 145_000.00, 0.16,
                            "Servicios profesionales de consultoria",
                            "G03", "03", "PUE", "vigente")
        d2_inv.append(inv)
        b.add_po(twin["rfc"], business_day(issue - timedelta(days=9)),
                 inv["total"], requester_for(w), approver_for(w, inv["total"]),
                 "Servicios profesionales de consultoria")
        b.book_purchase(inv, "gasto_admin", "CC-300 Administracion",
                        booking_approver(w), "Consultoria mensual")
        b.settle_purchase(inv, business_day(issue + timedelta(days=28)),
                          COMPANY_OPERATING, twin["bank_clabe"],
                          "CC-300 Administracion", booking_approver(w))
    gt.decoy(entity="RFC:" + twin["rfc"], signal="efos_legal_name_similarity",
             why_innocent="Legal name is one letter from the EFOS-listed "
                          "Corporativo Berlanda, but the RFC is different and does "
                          "not appear in efos_list. Contract, purchase orders and "
                          "payments span 24 months.",
             invoices=d2_inv, _code="D2", _confusable_with="RFC:" + s1["entities"][0][4:])

    # -- D3: an EFOS-listed entity the company never traded with.
    orphan_reg = date(2020, 4, 17)
    orphan_rfc = w.fresh_rfc_moral("ZAN", orphan_reg)
    b.add_efos(orphan_rfc, "Comercializadora Zantiva, S.A. de C.V.",
               "definitivo", date(2024, 12, 6))
    gt.decoy(entity="RFC:" + orphan_rfc, signal="efos_list_membership",
             why_innocent="Present in efos_list but issues no invoice to the "
                          "company and appears in no other table. An EFOS listing "
                          "with no transaction is not an exposure.",
             invoices=[], _code="D3")

    # -- D4: an employee who is also a registered sole-proprietor supplier, so
    #        vendor and employee legitimately share one CLABE.
    trainer = w.employees_by_id["EMP:0031"]
    born = date(1984, 2, 19)
    pf_rfc = w.fresh_rfc_fisica("TRVC", born)
    pf = b.add_vendor(pf_rfc,
                      trainer["name"] + " (persona fisica con actividad empresarial)",
                      date(2022, 5, 9),
                      "Calle Hidalgo 415, Guadalupe, Nuevo Leon",
                      trainer["bank_clabe"], "Capacitacion",
                      "capacitacion@" + slug(trainer["name"].split()[1]) + ".mx")
    pf["_border"] = False
    pf["_tier"] = "small"
    w.vendor_rows[pf_rfc] = pf
    b.add_contract(pf_rfc, date(2024, 3, 1), 348_000.00,
                   "Capacitacion tecnica especializada impartida por personal "
                   "propio bajo regimen de honorarios. Aprobado por Recursos "
                   "Humanos.")
    d4_inv = []
    for m in range(0, 20, 3):
        issue = business_day(add_months(date(2024, 3, 12), m))
        if issue > PERIOD_END:
            break
        inv = b.add_invoice(pf_rfc, COMPANY_RFC, issue, 42_000.00, 0.16,
                            "Capacitacion tecnica especializada", "G03", "03",
                            "PUE", "vigente")
        d4_inv.append(inv)
        b.add_po(pf_rfc, business_day(issue - timedelta(days=6)), inv["total"],
                 requester_for(w), approver_for(w, inv["total"]),
                 "Capacitacion tecnica especializada")
        b.book_purchase(inv, "gasto_admin", "CC-300 Administracion",
                        booking_approver(w), "Capacitacion")
        b.settle_purchase(inv, business_day(issue + timedelta(days=17)),
                          COMPANY_OPERATING, pf["bank_clabe"],
                          "CC-300 Administracion", booking_approver(w))
    gt.decoy(entity="RFC:" + pf_rfc, signal="vendor_employee_shared_clabe",
             why_innocent="The vendor IS the employee: a persona fisica con "
                          "actividad empresarial registered as a training supplier, "
                          "with a contract, purchase orders and invoices. One person, "
                          "one bank account, two roles.",
             invoices=d4_inv, _code="D4", _employee="EMP:0031")

    # -- D5: a vendor-to-employee transfer with a documented benign purpose.
    agency = _simple_vendor(w, "VIA", "Operadora Viatrezza, S.A. de C.V.",
                            date(2017, 10, 2),
                            "Av. Insurgentes Sur 2260, Ciudad de Mexico, Ciudad de Mexico",
                            "Servicios profesionales", "cuentas@viatrezza.mx")
    traveller = w.employees_by_id["EMP:0022"]
    d5_inv = []
    for issue, sub in ((date(2025, 3, 11), 64_800.00), (date(2025, 6, 10), 51_200.00)):
        inv = b.add_invoice(agency["rfc"], COMPANY_RFC, issue, sub, 0.16,
                            "Servicios de gestion de viajes corporativos",
                            "G03", "03", "PUE", "vigente")
        d5_inv.append(inv)
        b.add_po(agency["rfc"], business_day(issue - timedelta(days=8)),
                 inv["total"], requester_for(w), approver_for(w, inv["total"]),
                 "Servicios de gestion de viajes corporativos")
        b.book_purchase(inv, "gasto_admin", "CC-300 Administracion",
                        booking_approver(w), "Viajes corporativos")
        b.settle_purchase(inv, business_day(issue + timedelta(days=19)),
                          COMPANY_OPERATING, agency["bank_clabe"],
                          "CC-300 Administracion", booking_approver(w))
    d5_txn = b.add_bank(date(2025, 6, 24), agency["bank_clabe"],
                        traveller["bank_clabe"], 8_432.50,
                        "Reembolso gastos comprobados EXP-2025-0412", "SPEI")
    gt.decoy(entity="RFC:" + agency["rfc"], signal="vendor_to_employee_transfer",
             why_innocent="Single 8,432.50 MXN transfer described as reimbursement "
                          "of documented expenses, two weeks after the employee's "
                          "trip and unrelated in amount to any purchase order. The "
                          "vendor is a travel agency.",
             invoices=d5_inv, _code="D5", _txns=[d5_txn], _employee="EMP:0022")

    # -- D6: a benign directed bank cycle between three group accounts.
    alpha = _simple_vendor(w, "ORB", "Servicios Orbeda Corporativos, S.A. de C.V.",
                           date(2016, 7, 21),
                           "Av. Gonzalitos 520, Monterrey, Nuevo Leon",
                           "Servicios profesionales", "tesoreria@orbeda.mx")
    beta = _simple_vendor(w, "FEL", "Operadora Felquila, S.A. de C.V.",
                          date(2015, 11, 5),
                          "Blvd. Antonio L. Rodriguez 1888, Monterrey, Nuevo Leon",
                          "Arrendamiento de equipo", "tesoreria@felquila.mx")
    d6_txns = [
        b.add_bank(date(2024, 4, 18), COMPANY_OPERATING, alpha["bank_clabe"],
                   318_400.00, "Servicios administrativos compartidos 1T", "SPEI"),
        b.add_bank(date(2024, 9, 26), alpha["bank_clabe"], beta["bank_clabe"],
                   74_900.00, "Arrendamiento equipo montacargas 3T", "SPEI"),
        b.add_bank(date(2025, 2, 13), beta["bank_clabe"], COMPANY_OPERATING,
                   1_206_000.00, "Liquidacion saldo intercompania 2024", "SPEI"),
    ]
    for vend in (alpha, beta):
        b.add_contract(vend["rfc"], date(2024, 1, 8),
                       round(draw_amount(rng, "mid") * 8, 2),
                       "Contrato abierto de servicios corporativos compartidos.")
    gt.decoy(entity="RFC:" + alpha["rfc"], signal="directed_bank_transfer_cycle",
             why_innocent="The three legs are 5 and 10 months apart, differ by a "
                          "factor of sixteen in amount, and each carries its own "
                          "business purpose. Same topology as a round trip, none of "
                          "the same money.",
             invoices=[], _code="D6", _txns=d6_txns,
             _cycle_accounts=[COMPANY_OPERATING, alpha["bank_clabe"],
                              beta["bank_clabe"]])

    # -- D7: a 12-month retainer that sits just under the approval ladder.
    retainer = _simple_vendor(w, "SAL", "Consultores Salperga, S.C.",
                              date(2018, 1, 30),
                              "Av. Chapultepec 640, Guadalajara, Jalisco",
                              "Servicios legales", "honorarios@salperga.mx")
    b.add_contract(retainer["rfc"], date(2024, 12, 1), 1_074_000.00,
                   "Asesoria juridica corporativa. Iguala mensual fija de "
                   "89,500.00 MXN IVA incluido, vigencia 12 meses.")
    d7_inv = []
    for m in range(12):
        issue = business_day(add_months(date(2025, 1, 5), m))
        if issue > PERIOD_END:
            break
        inv = b.add_invoice(retainer["rfc"], COMPANY_RFC, issue,
                            round(89_500.00 / 1.16, 2), 0.16,
                            "Asesoria juridica corporativa - iguala mensual",
                            "G03", "03", "PUE", "vigente")
        d7_inv.append(inv)
        b.add_po(retainer["rfc"], business_day(issue - timedelta(days=4)),
                 inv["total"], w.requesters[1]["name"], w.coordinators[1]["name"],
                 "Asesoria juridica corporativa - iguala mensual")
        b.book_purchase(inv, "gasto_admin", "CC-300 Administracion",
                        booking_approver(w), "Iguala juridica")
        b.settle_purchase(inv, business_day(issue + timedelta(days=15)),
                          COMPANY_OPERATING, retainer["bank_clabe"],
                          "CC-300 Administracion", booking_approver(w))
    gt.decoy(entity="RFC:" + retainer["rfc"],
             signal="recurring_amount_just_below_approval_threshold",
             why_innocent="Twelve identical 89,500.00 MXN invoices one month apart, "
                          "matching a contract that fixes exactly that monthly "
                          "retainer for twelve months. Regular, not clustered.",
             invoices=d7_inv, _code="D7")

    # -- D8: genuine year-end seasonality, visible in both years.
    pack = _simple_vendor(w, "GRI", "Empaques Grisolia, S.A. de C.V.",
                          date(2013, 6, 12),
                          "Carretera a Laredo km 14 2210, Apodaca, Nuevo Leon",
                          "Empaque y embalaje", "ventas@grisolia.mx")
    b.add_contract(pack["rfc"], date(2023, 11, 1), 8_600_000.00,
                   "Suministro de material de empaque. Contrato abierto con "
                   "picos de temporada de fin de ano.")
    d8_inv = []
    for year in (2024, 2025):
        for month, count in ((3, 1), (5, 1), (7, 1), (9, 2), (10, 3), (11, 6), (12, 7)):
            for k in range(count):
                issue = business_day(date(year, month, min(2 + k * 4, 27)))
                if issue > PERIOD_END:
                    continue
                sub = draw_amount(rng, "mid")
                inv = b.add_invoice(pack["rfc"], COMPANY_RFC, issue, sub, 0.16,
                                    "Suministro de material de empaque", "G01",
                                    "03", "PUE", "vigente")
                d8_inv.append(inv)
                b.add_po(pack["rfc"], business_day(issue - timedelta(days=7)),
                         inv["total"], requester_for(w),
                         approver_for(w, inv["total"]),
                         "Suministro de material de empaque")
                b.book_purchase(inv, "costo_prod", "CC-100 Produccion",
                                booking_approver(w), "Material de empaque")
                b.settle_purchase(inv, business_day(issue + timedelta(days=24)),
                                  COMPANY_OPERATING, pack["bank_clabe"],
                                  "CC-100 Produccion", booking_approver(w))
    gt.decoy(entity="RFC:" + pack["rfc"], signal="period_end_invoice_concentration",
             why_innocent="The November-December spike repeats identically in 2024 "
                          "and 2025 and matches a packaging contract that states "
                          "seasonal peaks. The base rate makes this normal.",
             invoices=d8_inv, _code="D8")

    # -- D9: two invoices that look like a duplicate payment and are not.
    twin_site = _simple_vendor(w, "CIT", "Servicios Citarenco, S.A. de C.V.",
                               date(2020, 2, 26),
                               "Av. Revolucion 1450, Monterrey, Nuevo Leon",
                               "Limpieza y facilities", "cobranza@citarenco.mx")
    d9_inv = []
    for site, cc in (("Planta Apodaca", "CC-100 Produccion"),
                     ("Centro de distribucion Guadalupe", "CC-400 Logistica")):
        inv = b.add_invoice(twin_site["rfc"], COMPANY_RFC, date(2025, 7, 8),
                            67_500.00, 0.16,
                            "Servicio integral de limpieza - " + site,
                            "G03", "03", "PUE", "vigente")
        d9_inv.append(inv)
        b.add_po(twin_site["rfc"], date(2025, 6, 27), inv["total"],
                 requester_for(w), approver_for(w, inv["total"]),
                 "Servicio integral de limpieza - " + site)
        b.book_purchase(inv, "gasto_admin", cc, booking_approver(w),
                        "Limpieza " + site)
        b.settle_purchase(inv, date(2025, 7, 29), COMPANY_OPERATING,
                          twin_site["bank_clabe"], cc, booking_approver(w))
    gt.decoy(entity="RFC:" + twin_site["rfc"], signal="duplicate_payment_candidate",
             why_innocent="Same vendor, same date, same 78,300.00 MXN amount, but "
                          "two distinct purchase orders, two distinct sites and two "
                          "distinct cost centres. Two services, not one paid twice.",
             invoices=d9_inv, _code="D9")

    # -- D10: a large cancelled invoice that must not count as exposure.
    cancelled_v = _simple_vendor(w, "HAL", "Constructora Halperga, S.A. de C.V.",
                                 date(2014, 8, 19),
                                 "Blvd. Bernardo Quintana 3400, El Marques, Queretaro",
                                 "Construccion", "facturacion@halperga.mx")
    b.add_contract(cancelled_v["rfc"], date(2025, 1, 20), 6_264_000.00,
                   "Obra civil - adecuacion de nave industrial. Contrato por "
                   "proyecto pagado contra entregables.")
    cancelled_inv = b.add_invoice(cancelled_v["rfc"], COMPANY_RFC,
                                  date(2025, 9, 30), 2_180_000.00, 0.16,
                                  "Obra civil - estimacion de avance 3", "I01",
                                  "99", "PPD", "cancelado")
    b.book_purchase(cancelled_inv, "capex", "CC-600 Proyectos",
                    booking_approver(w), "Obra civil - estimacion 3")
    ok_inv = b.add_invoice(cancelled_v["rfc"], COMPANY_RFC, date(2025, 10, 14),
                           2_180_000.00, 0.16,
                           "Obra civil - estimacion de avance 3 (refacturacion)",
                           "I01", "03", "PUE", "vigente")
    b.add_po(cancelled_v["rfc"], date(2025, 9, 12), ok_inv["total"],
             requester_for(w), w.directors[2]["name"],
             "Obra civil - estimacion de avance 3")
    b.book_purchase(ok_inv, "capex", "CC-600 Proyectos", booking_approver(w),
                    "Obra civil - estimacion 3 refacturada")
    b.settle_purchase(ok_inv, date(2025, 11, 6), COMPANY_OPERATING,
                      cancelled_v["bank_clabe"], "CC-600 Proyectos",
                      booking_approver(w))
    gt.decoy(entity="RFC:" + cancelled_v["rfc"], signal="invoice_without_settlement",
             why_innocent="The 2,528,800.00 MXN invoice carries status 'cancelado' "
                          "and was reissued two weeks later; the reissued invoice "
                          "was paid once. Counting the cancelled one doubles the "
                          "exposure.",
             invoices=[cancelled_inv, ok_inv], _code="D10")

    # -- D11: a single very large legitimate capital payment.
    capex_v = _simple_vendor(w, "IND", "Ingenieria Indbania, S.A. de C.V.",
                             date(2011, 4, 8),
                             "Av. Manuel J. Cloutier 2050, Zapopan, Jalisco",
                             "Manufactura y maquinados", "ventas@indbania.mx")
    b.add_contract(capex_v["rfc"], date(2025, 2, 3), 4_872_000.00,
                   "Suministro e instalacion de equipo electromecanico. "
                   "Contrato por proyecto, pago contra entrega y puesta en marcha.")
    capex_inv = b.add_invoice(capex_v["rfc"], COMPANY_RFC, date(2025, 5, 27),
                              4_200_000.00, 0.16,
                              "Suministro e instalacion de equipo electromecanico",
                              "I08", "03", "PUE", "vigente")
    b.add_po(capex_v["rfc"], date(2025, 2, 10), capex_inv["total"],
             requester_for(w), w.directors[1]["name"],
             "Suministro e instalacion de equipo electromecanico")
    b.book_purchase(capex_inv, "capex", "CC-600 Proyectos", booking_approver(w),
                    "Equipo electromecanico")
    b.settle_purchase(capex_inv, date(2025, 6, 20), COMPANY_OPERATING,
                      capex_v["bank_clabe"], "CC-600 Proyectos",
                      booking_approver(w))
    gt.decoy(entity="RFC:" + capex_v["rfc"], signal="outlier_transaction_amount",
             why_innocent="4,872,000.00 MXN single payment backed by a contract "
                          "signed four months earlier, a director-approved purchase "
                          "order and a capitalised ledger entry. Large is not odd.",
             invoices=[capex_inv], _code="D11")

    # -- D12: a shared business-tower address, mostly honest tenants.
    tower_tenants = []
    for stem, name, cat in (("LAN", "Consultores Lanperga, S.C.", "Consultoria"),
                            ("ERM", "Servicios Ermatela, S.A. de C.V.", "Servicios profesionales"),
                            ("DOV", "Corporativo Dovnuvia, S.A. de C.V.", "Publicidad y medios")):
        t = _simple_vendor(w, stem, name,
                           date(rng.randint(2012, 2021), rng.randint(1, 12),
                                rng.randint(1, 28)),
                           SHARED_TOWER, cat,
                           "contacto@" + slug(name.split()[1]) + ".mx")
        tower_tenants.append(t)
        b.add_contract(t["rfc"], date(2024, rng.randint(1, 8), rng.randint(1, 28)),
                       round(draw_amount(rng, "mid") * 10, 2),
                       "Contrato marco de servicios corporativos.")
        for _ in range(rng.randint(5, 11)):
            issue = business_day(PERIOD_START + timedelta(days=rng.randint(0, 720)))
            sub = draw_amount(rng, "mid")
            inv = b.add_invoice(t["rfc"], COMPANY_RFC, issue, sub, 0.16,
                                rng.choice(w.po_vocab), "G03", "03", "PUE",
                                "vigente")
            b.add_po(t["rfc"], business_day(issue - timedelta(days=10)),
                     inv["total"], requester_for(w), approver_for(w, inv["total"]),
                     "Servicios corporativos")
            b.book_purchase(inv, "gasto_admin", "CC-300 Administracion",
                            booking_approver(w), "Servicios corporativos")
            b.settle_purchase(inv, business_day(issue + timedelta(days=26)),
                              COMPANY_OPERATING, t["bank_clabe"],
                              "CC-300 Administracion", booking_approver(w))
    gt.decoy(entity="RFC:" + tower_tenants[0]["rfc"], signal="shared_vendor_address",
             why_innocent="Five vendors share the same address because it is a "
                          "leased office tower. Three of the five, including this "
                          "one, have contracts, purchase orders and a multi-year "
                          "payment history. Address is not control.",
             invoices=[], _code="D12",
             _co_tenants=["RFC:" + t["rfc"] for t in tower_tenants])

    # -- D13: an honest vendor with no contract row at all.
    nocontract = _simple_vendor(w, "PAL", "Refacciones Palrisco, S.A. de C.V.",
                                date(2016, 9, 15),
                                "Calle Zaragoza 780, Saltillo, Coahuila",
                                "Refacciones industriales", "ventas@palrisco.mx")
    d13_inv = []
    for _ in range(9):
        issue = business_day(PERIOD_START + timedelta(days=rng.randint(30, 700)))
        sub = draw_amount(rng, "mid")
        inv = b.add_invoice(nocontract["rfc"], COMPANY_RFC, issue, sub, 0.16,
                            "Suministro de refacciones industriales", "G01",
                            "03", "PUE", "vigente")
        d13_inv.append(inv)
        b.add_po(nocontract["rfc"], business_day(issue - timedelta(days=12)),
                 inv["total"], requester_for(w), approver_for(w, inv["total"]),
                 "Suministro de refacciones industriales")
        b.book_purchase(inv, "mantenimiento", "CC-200 Mantenimiento",
                        booking_approver(w), "Refacciones")
        b.settle_purchase(inv, business_day(issue + timedelta(days=20)),
                          COMPANY_OPERATING, nocontract["bank_clabe"],
                          "CC-200 Mantenimiento", booking_approver(w))
    gt.decoy(entity="RFC:" + nocontract["rfc"], signal="no_contract_on_file",
             why_innocent="Nine invoices, each with its own approved purchase order "
                          "and payment, but no row in contracts. The contracts table "
                          "is incomplete for spot purchases. Absence of a record in "
                          "the estate is not absence of a contract.",
             invoices=d13_inv, _code="D13")

    # -- D14: unusual but entirely valid CFDI coding.
    odd = _simple_vendor(w, "XEN", "Transportes Xenmendi, S.A. de C.V.",
                         date(2019, 12, 3),
                         "Prol. Ruiz Cortines 1620, Guadalupe, Nuevo Leon",
                         "Logistica y transporte", "facturacion@xenmendi.mx")
    b.add_contract(odd["rfc"], date(2024, 2, 12), 2_320_000.00,
                   "Servicio de transporte y distribucion. Contrato abierto, "
                   "pago en parcialidades diferido a 60 dias.")
    d14_inv = []
    for m in range(0, 21, 3):
        issue = business_day(add_months(date(2024, 3, 6), m))
        if issue > PERIOD_END:
            break
        sub = draw_amount(rng, "mid")
        inv = b.add_invoice(odd["rfc"], COMPANY_RFC, issue, sub, 0.16,
                            "Servicio de transporte y distribucion", "G03",
                            "99", "PPD", "vigente")
        d14_inv.append(inv)
        b.add_po(odd["rfc"], business_day(issue - timedelta(days=14)),
                 inv["total"], requester_for(w), approver_for(w, inv["total"]),
                 "Servicio de transporte y distribucion")
        b.book_purchase(inv, "gasto_venta", "CC-400 Logistica",
                        booking_approver(w), "Transporte")
        first = round(inv["total"] * 0.5, 2)
        b.settle_purchase(inv, business_day(issue + timedelta(days=62)),
                          COMPANY_OPERATING, odd["bank_clabe"],
                          "CC-400 Logistica", booking_approver(w), amount=first)
        second = business_day(issue + timedelta(days=94))
        if second <= PERIOD_END:
            b.settle_purchase(inv, second, COMPANY_OPERATING, odd["bank_clabe"],
                              "CC-400 Logistica", booking_approver(w),
                              amount=round(inv["total"] - first, 2))
    gt.decoy(entity="RFC:" + odd["rfc"], signal="unusual_cfdi_coding",
             why_innocent="forma_pago 99 with metodo_pago PPD is the coding the SAT "
                          "filling guide requires for deferred payment, and the "
                          "contract sets 60-day terms. The partial settlements are "
                          "the instalments, not short payments.",
             invoices=d14_inv, _code="D14")

    # -- D15: five vendors sharing one outsourced accountant's mail domain.
    shared_domain = "despacho-contable-norena.mx"
    shared = []
    for stem, name, cat in (("MEL", "Suministros Melgesta, S.A. de C.V.", "Papeleria y consumibles"),
                            ("URB", "Servicios Urbcerta, S.A. de C.V.", "Limpieza y facilities"),
                            ("JOR", "Distribuidora Jortela, S.A. de C.V.", "Alimentos y comedor industrial"),
                            ("RAS", "Comercializadora Rasfolia, S.A. de C.V.", "Papeleria y consumibles"),
                            ("TOR", "Servicios Torvinca, S.A. de C.V.", "Seguridad privada")):
        v2 = _simple_vendor(w, stem, name,
                            date(rng.randint(2013, 2022), rng.randint(1, 12),
                                 rng.randint(1, 28)),
                            make_address(rng)[0], cat,
                            "facturas." + slug(name.split()[1]) + "@" + shared_domain)
        shared.append(v2)
        for _ in range(rng.randint(4, 10)):
            issue = business_day(PERIOD_START + timedelta(days=rng.randint(0, 720)))
            sub = draw_amount(rng, "small")
            inv = b.add_invoice(v2["rfc"], COMPANY_RFC, issue, sub, 0.16,
                                rng.choice(w.po_vocab), "G03", "03", "PUE",
                                "vigente")
            b.add_po(v2["rfc"], business_day(issue - timedelta(days=8)),
                     inv["total"], requester_for(w), approver_for(w, inv["total"]),
                     "Suministro recurrente")
            b.book_purchase(inv, CATEGORY_ACCOUNT[cat], "CC-300 Administracion",
                            booking_approver(w), "Suministro recurrente")
            b.settle_purchase(inv, business_day(issue + timedelta(days=21)),
                              COMPANY_OPERATING, v2["bank_clabe"],
                              "CC-300 Administracion", booking_approver(w))
    gt.decoy(entity="RFC:" + shared[0]["rfc"], signal="shared_contact_email_domain",
             why_innocent="Five small suppliers bill from the same mail domain "
                          "because they use the same outsourced accounting practice. "
                          "They have different RFCs, different addresses, different "
                          "CLABEs and different categories.",
             invoices=[], _code="D15",
             _co_vendors=["RFC:" + v2["rfc"] for v2 in shared])

    # -- D16: on the EFOS list, but every invoice predates the publication.
    early_reg = date(2016, 1, 26)
    early = _simple_vendor(w, "GAL", "Manufacturas Galzeta, S.A. de C.V.",
                           early_reg,
                           "Av. Eugenio Garza Sada 3900, Monterrey, Nuevo Leon",
                           "Manufactura y maquinados", "ventas@galzeta.mx")
    b.add_efos(early["rfc"], "Manufacturas Galzeta, S.A. de C.V.", "presunto",
               date(2025, 12, 12))
    b.add_contract(early["rfc"], date(2023, 10, 2), 5_104_000.00,
                   "Manufactura de subensambles metalmecanicos. Contrato por "
                   "proyecto pagado contra entregables.")
    d16_inv = []
    for issue, sub in ((date(2024, 2, 14), 420_000.00),
                       (date(2024, 4, 23), 388_000.00),
                       (date(2024, 6, 18), 512_000.00),
                       (date(2024, 8, 27), 445_000.00),
                       (date(2024, 10, 15), 396_000.00)):
        inv = b.add_invoice(early["rfc"], COMPANY_RFC, issue, sub, 0.16,
                            "Manufactura de subensambles metalmecanicos", "G01",
                            "03", "PUE", "vigente")
        d16_inv.append(inv)
        b.add_po(early["rfc"], business_day(issue - timedelta(days=18)),
                 inv["total"], requester_for(w), approver_for(w, inv["total"]),
                 "Manufactura de subensambles metalmecanicos")
        b.book_purchase(inv, "costo_prod", "CC-100 Produccion",
                        booking_approver(w), "Subensambles")
        b.settle_purchase(inv, business_day(issue + timedelta(days=30)),
                          COMPANY_OPERATING, early["bank_clabe"],
                          "CC-100 Produccion", booking_approver(w))
    gt.decoy(entity="RFC:" + early["rfc"], signal="efos_vendor_match",
             why_innocent="On the EFOS list as 'presunto', published 2025-12-12. "
                          "Every invoice from this vendor was issued between "
                          "February and October 2024, more than a year earlier, each "
                          "with a purchase order under a 2023 contract. A presumption "
                          "published later is not proof about earlier trade.",
             invoices=d16_inv, _code="D16")

    # -- D17: the generic public RFC used for petty-cash retail purchases.
    petty = b.add_vendor("XAXX010101000", "Publico en General",
                         date(2013, 1, 1),
                         "Sin domicilio registrado - operaciones con publico en general",
                         COMPANY_PETTY, "Papeleria y consumibles",
                         "cajachica@meridiano.mx")
    petty["_border"] = False
    petty["_tier"] = "small"
    w.vendor_rows["XAXX010101000"] = petty
    d17_inv = []
    for _ in range(14):
        issue = business_day(PERIOD_START + timedelta(days=rng.randint(0, 720)))
        sub = round(rng.uniform(380, 4200), 2)
        inv = b.add_invoice("XAXX010101000", COMPANY_RFC, issue, sub, 0.16,
                            "Compras menores de caja chica", "G03", "01", "PUE",
                            "vigente")
        d17_inv.append(inv)
        b.book_purchase(inv, "gasto_admin", "CC-300 Administracion",
                        booking_approver(w), "Caja chica")
    gt.decoy(entity="RFC:XAXX010101000", signal="generic_or_malformed_rfc",
             why_innocent="XAXX010101000 is the SAT generic RFC for sales to the "
                          "general public. It is a placeholder, not a supplier, and "
                          "the amounts are petty-cash sized.",
             invoices=d17_inv, _code="D17")

    # -- D18: two suppliers of one group collecting into one treasury account.
    group_clabe = b.new_clabe("036", "180")
    group = []
    for stem, name, cat in (("VAL", "Manufacturas Valdova, S.A. de C.V.",
                             "Manufactura y maquinados"),
                            ("VAL", "Servicios Valdova Integrales, S.A. de C.V.",
                             "Mantenimiento industrial")):
        g = _simple_vendor(w, stem, name,
                           date(2015, 4, 21) if not group else date(2015, 4, 23),
                           "Av. Insurgentes Sur 1980, Int. 9, Ciudad de Mexico, "
                           "Ciudad de Mexico", cat,
                           "cobranza.corporativa@valdova.mx",
                           clabe_value=group_clabe)
        group.append(g)
        b.add_contract(g["rfc"], date(2024, 2, 5),
                       round(draw_amount(rng, "mid") * 9, 2),
                       "Contrato abierto. La cobranza de ambas sociedades del "
                       "grupo se concentra en una sola cuenta de tesoreria "
                       "corporativa, segun clausula septima.")
        for _ in range(rng.randint(5, 9)):
            issue = business_day(PERIOD_START + timedelta(days=rng.randint(0, 700)))
            sub = draw_amount(rng, "mid")
            inv = b.add_invoice(g["rfc"], COMPANY_RFC, issue, sub, 0.16,
                                rng.choice(w.po_vocab), "G01", "03", "PUE",
                                "vigente")
            b.add_po(g["rfc"], business_day(issue - timedelta(days=9)),
                     inv["total"], requester_for(w), approver_for(w, inv["total"]),
                     "Suministro corporativo")
            b.book_purchase(inv, CATEGORY_ACCOUNT[cat], "CC-100 Produccion",
                            booking_approver(w), "Suministro corporativo")
            b.settle_purchase(inv, business_day(issue + timedelta(days=23)),
                              COMPANY_OPERATING, group_clabe, "CC-100 Produccion",
                              booking_approver(w))
    gt.decoy(entity="RFC:" + group[0]["rfc"], signal="shared_vendor_clabe",
             why_innocent="Two different RFCs collect into one CLABE because both "
                          "are companies of the same group and their contracts state "
                          "that collection is centralised in a corporate treasury "
                          "account. Both have their own contracts, purchase orders "
                          "and deliveries.",
             invoices=[], _code="D18",
             _co_vendors=["RFC:" + g["rfc"] for g in group],
             _txns=[])


# --------------------------------------------------------------------------
# World assembly
# --------------------------------------------------------------------------

COMPANY_OPERATING = ""
COMPANY_TREASURY = ""
COMPANY_PETTY = ""

ORDINARY_VENDOR_COUNT = 96


def build_world(seed: int = SEED):
    global COMPANY_OPERATING, COMPANY_TREASURY, COMPANY_PETTY

    refs = {
        "bank": load_reference("bank_codes_reference.json"),
        "cfdi": load_reference("sat_cfdi_catalog_snapshot.json"),
        "efos": load_reference("sat_efos_structure_reference.json"),
        "denue": load_reference("denue_category_sample.json"),
        "procurement": load_reference("procurement_terms_reference.json"),
    }

    b = EstateBuilder(seed)
    COMPANY_OPERATING = fixed_clabe(b, "072", "580", "90000000101")
    COMPANY_TREASURY = fixed_clabe(b, "012", "580", "90000000102")
    COMPANY_PETTY = fixed_clabe(b, "058", "580", "90000000103")

    w = World(b, refs)
    gt = GroundTruth()

    build_employees(w)

    # Ordinary supplier base first, so the honest traffic dominates.
    for _ in range(ORDINARY_VENDOR_COUNT):
        vendor = w.new_vendor()
        build_ordinary_vendor(w, vendor)

    build_customers(w)
    build_ordinary_sales(w)
    build_efos_background(w)

    # Planted schemes.
    s1 = plant_phantom_vendor(w, gt)
    plant_kickback(w, gt)
    plant_round_tripping_and_revenue_inflation(w, gt)
    plant_threshold_splitting(w, gt)

    # Decoys last; they reference s1 for the name-similarity trap.
    plant_decoys(w, gt, s1)

    b.finalize_ids()
    return b, w, gt


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

DDL = """
CREATE TABLE vendors (
    rfc             TEXT PRIMARY KEY,
    legal_name      TEXT,
    registered_date TEXT,
    address         TEXT,
    bank_clabe      TEXT,
    category        TEXT,
    contact_email   TEXT
);
CREATE TABLE invoices (
    uuid          TEXT PRIMARY KEY,
    issuer_rfc    TEXT,
    receiver_rfc  TEXT,
    issue_date    TEXT,
    subtotal      REAL,
    iva           REAL,
    total         REAL,
    concepto_text TEXT,
    uso_cfdi      TEXT,
    forma_pago    TEXT,
    metodo_pago   TEXT,
    status        TEXT
);
CREATE TABLE ledger (
    entry_id     INTEGER PRIMARY KEY,
    date         TEXT,
    account_code TEXT,
    account_name TEXT,
    debit        REAL,
    credit       REAL,
    description  TEXT,
    invoice_uuid TEXT,
    cost_center  TEXT,
    approver     TEXT
);
CREATE TABLE bank_txns (
    txn_id     TEXT PRIMARY KEY,
    date       TEXT,
    from_clabe TEXT,
    to_clabe   TEXT,
    amount     REAL,
    reference  TEXT,
    channel    TEXT
);
CREATE TABLE purchase_orders (
    po_id       TEXT PRIMARY KEY,
    vendor_rfc  TEXT,
    date        TEXT,
    amount      REAL,
    requester   TEXT,
    approver    TEXT,
    description TEXT
);
CREATE TABLE contracts (
    contract_id TEXT PRIMARY KEY,
    vendor_rfc  TEXT,
    start_date  TEXT,
    value       REAL,
    scope_text  TEXT
);
CREATE TABLE employees (
    emp_id     TEXT PRIMARY KEY,
    name       TEXT,
    role       TEXT,
    bank_clabe TEXT,
    hire_date  TEXT
);
CREATE TABLE efos_list (
    rfc              TEXT PRIMARY KEY,
    legal_name       TEXT,
    status           TEXT,
    publication_date TEXT
);
"""

COLUMNS = {
    "vendors": ["rfc", "legal_name", "registered_date", "address",
                "bank_clabe", "category", "contact_email"],
    "invoices": ["uuid", "issuer_rfc", "receiver_rfc", "issue_date", "subtotal",
                 "iva", "total", "concepto_text", "uso_cfdi", "forma_pago",
                 "metodo_pago", "status"],
    "ledger": ["entry_id", "date", "account_code", "account_name", "debit",
               "credit", "description", "invoice_uuid", "cost_center",
               "approver"],
    "bank_txns": ["txn_id", "date", "from_clabe", "to_clabe", "amount",
                  "reference", "channel"],
    "purchase_orders": ["po_id", "vendor_rfc", "date", "amount", "requester",
                        "approver", "description"],
    "contracts": ["contract_id", "vendor_rfc", "start_date", "value",
                  "scope_text"],
    "employees": ["emp_id", "name", "role", "bank_clabe", "hire_date"],
    "efos_list": ["rfc", "legal_name", "status", "publication_date"],
}

SORT_KEY = {
    "vendors": lambda r: r["rfc"],
    "invoices": lambda r: r["uuid"],
    "ledger": lambda r: r["entry_id"],
    "bank_txns": lambda r: r["txn_id"],
    "purchase_orders": lambda r: r["po_id"],
    "contracts": lambda r: r["contract_id"],
    "employees": lambda r: r["emp_id"],
    "efos_list": lambda r: r["rfc"],
}


def table_rows(b: EstateBuilder) -> dict:
    raw = {
        "vendors": b.vendors, "invoices": b.invoices, "ledger": b.ledger,
        "bank_txns": b.bank, "purchase_orders": b.pos,
        "contracts": b.contracts, "employees": b.employees,
        "efos_list": b.efos,
    }
    out = {}
    for table, rows in raw.items():
        cols = COLUMNS[table]
        ordered = sorted(rows, key=SORT_KEY[table])
        out[table] = [tuple(r[c] for c in cols) for r in ordered]
    return out


def _atomic_replace(tmp: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, target)


def write_estate_db(path: Path, rows: dict) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        for table in OFFICIAL_TABLES:
            cols = COLUMNS[table]
            placeholders = ",".join("?" * len(cols))
            conn.executemany(
                "INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(cols),
                                                     placeholders),
                rows[table])
        conn.commit()
    finally:
        conn.close()


def write_estate_csv_zip(path: Path, rows: dict) -> None:
    if path.exists():
        path.unlink()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in OFFICIAL_TABLES:
            buf = io.StringIO(newline="")
            writer = csv.writer(buf, lineterminator="\n")
            writer.writerow(COLUMNS[table])
            for row in rows[table]:
                writer.writerow([
                    ("%.2f" % v) if isinstance(v, float) else v for v in row
                ])
            # An explicit ZipInfo is required: zipfile.writestr(str, ...)
            # stamps the current wall clock into the entry header, which makes
            # the archive differ between two runs of the same seed.
            info = zipfile.ZipInfo(table + ".csv",
                                   date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, buf.getvalue().encode("utf-8"))


ANSWER_DDL = """
CREATE TABLE answer_key_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE answer_key_schemes (
    scheme_id   TEXT PRIMARY KEY,
    scheme_type TEXT,
    entities    TEXT,
    peso_amount REAL,
    difficulty  TEXT,
    notes       TEXT
);
CREATE TABLE answer_key_scheme_records (
    scheme_id    TEXT,
    source_table TEXT,
    record_id    TEXT,
    role         TEXT
);
CREATE TABLE answer_key_decoys (
    decoy_id     TEXT PRIMARY KEY,
    entity       TEXT,
    signal       TEXT,
    why_innocent TEXT,
    expected_outcome TEXT
);
CREATE TABLE answer_key_decoy_records (
    decoy_id     TEXT,
    source_table TEXT,
    record_id    TEXT,
    role         TEXT
);
"""


def write_answers_db(path: Path, rows: dict, gt: GroundTruth, seed: int,
                     notes: dict) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        conn.executescript(ANSWER_DDL)
        for table in OFFICIAL_TABLES:
            cols = COLUMNS[table]
            conn.executemany(
                "INSERT INTO %s (%s) VALUES (%s)" % (
                    table, ",".join(cols), ",".join("?" * len(cols))),
                rows[table])

        conn.executemany(
            "INSERT INTO answer_key_meta (key, value) VALUES (?, ?)",
            [("seed", str(seed)),
             ("company_rfc", COMPANY_RFC),
             ("company_legal_name", COMPANY_NAME),
             ("period_start", iso(PERIOD_START)),
             ("period_end", iso(PERIOD_END)),
             ("company_operating_clabe", COMPANY_OPERATING),
             ("company_treasury_clabe", COMPANY_TREASURY),
             ("company_petty_cash_clabe", COMPANY_PETTY),
             ("internal_approval_level_1", "%.2f" % APPROVAL_L1),
             ("internal_approval_level_2", "%.2f" % APPROVAL_L2),
             ("generator", "dev_eval/generate_robust_realism_estate.py"),
             ("warning",
              "THIS FILE CONTAINS THE ANSWER KEY. Never point the production "
              "auditor at it. Use robust_realism_estate.db instead.")])

        for s in gt.schemes:
            note = notes.get(s["scheme_id"], {})
            conn.execute(
                "INSERT INTO answer_key_schemes (scheme_id, scheme_type, "
                "entities, peso_amount, difficulty, notes) VALUES (?,?,?,?,?,?)",
                (s["scheme_id"], s["type"], " | ".join(s["entities"]),
                 s["peso_amount"], s["difficulty"],
                 note.get("expected_outcome", "")))
            for inv in s.get("_invoices", []):
                conn.execute(
                    "INSERT INTO answer_key_scheme_records VALUES (?,?,?,?)",
                    (s["scheme_id"], "invoices", inv["uuid"], "supporting_invoice"))
            for txn in s.get("_txns", []):
                conn.execute(
                    "INSERT INTO answer_key_scheme_records VALUES (?,?,?,?)",
                    (s["scheme_id"], "bank_txns", txn["txn_id"], "supporting_txn"))
            for inv in s.get("_context_invoices", []):
                conn.execute(
                    "INSERT INTO answer_key_scheme_records VALUES (?,?,?,?)",
                    (s["scheme_id"], "invoices", inv["uuid"], "context_invoice"))
            for txn in s.get("_context_txns", []):
                conn.execute(
                    "INSERT INTO answer_key_scheme_records VALUES (?,?,?,?)",
                    (s["scheme_id"], "bank_txns", txn["txn_id"], "context_txn"))
            for txn in s.get("_payment_txns", []):
                conn.execute(
                    "INSERT INTO answer_key_scheme_records VALUES (?,?,?,?)",
                    (s["scheme_id"], "bank_txns", txn["txn_id"],
                     "context_company_payment"))
            for po in s.get("_pos", []):
                conn.execute(
                    "INSERT INTO answer_key_scheme_records VALUES (?,?,?,?)",
                    (s["scheme_id"], "purchase_orders", po["po_id"],
                     "supporting_po"))
            for rfc in s.get("_efos_rfcs", []):
                conn.execute(
                    "INSERT INTO answer_key_scheme_records VALUES (?,?,?,?)",
                    (s["scheme_id"], "efos_list", rfc, "supporting_efos"))

        for d in gt.decoys:
            note = notes.get(d["_code"], {})
            conn.execute(
                "INSERT INTO answer_key_decoys (decoy_id, entity, signal, "
                "why_innocent, expected_outcome) VALUES (?,?,?,?,?)",
                (d["_code"], d["entity"], d["signal"], d["why_innocent"],
                 note.get("expected_outcome", "no finding")))
            for inv in d.get("invoices", []):
                conn.execute(
                    "INSERT INTO answer_key_decoy_records VALUES (?,?,?,?)",
                    (d["_code"], "invoices", inv["uuid"], "clearing_invoice"))
            for txn in d.get("_txns", []):
                conn.execute(
                    "INSERT INTO answer_key_decoy_records VALUES (?,?,?,?)",
                    (d["_code"], "bank_txns", txn["txn_id"], "clearing_txn"))
        conn.commit()
    finally:
        conn.close()


def build_ground_truth(gt: GroundTruth, seed: int) -> dict:
    """Exactly the shape of the official ground_truth_schema.json."""
    schemes = []
    for s in gt.schemes:
        schemes.append({
            "scheme_id": s["scheme_id"],
            "type": s["type"],
            "entities": list(s["entities"]),
            "supporting_invoices": [i["uuid"] for i in s.get("_invoices", [])],
            "supporting_txns": [t["txn_id"] for t in s.get("_txns", [])],
            "peso_amount": s["peso_amount"],
            "difficulty": s["difficulty"],
        })
    decoys = []
    for d in gt.decoys:
        decoys.append({
            "entity": d["entity"],
            "signal": d["signal"],
            "why_innocent": d["why_innocent"],
            "invoices": [i["uuid"] for i in d.get("invoices", [])],
        })
    return {
        "seed": seed,
        "company_rfc": COMPANY_RFC,
        "schemes": schemes,
        "decoys": decoys,
    }


def write_json(path: Path, payload) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    _atomic_replace(Path(tmp), path)


# --------------------------------------------------------------------------
# Scenario notes (manifest prose; never touches the estate)
# --------------------------------------------------------------------------

# ``fires_today`` was MEASURED by running the six deterministic production
# detectors over this estate at repo commit e10bb17.  A decoy that does not
# fire today is still bait: it is aimed at a detector the roadmap calls for
# (documentary coverage, base rate, duplicate payment, period-end
# concentration, inferred approval threshold).
FIRES_TODAY = {"D4", "D5", "D16", "D18"}

NOTES = {
"S1_phantom_vendor_1": {
 "kind": "true positive",
 "initial": "A consultancy registered eight months before its first invoice bills "
            "2.4 million pesos in six months, is paid within a week every time, "
            "and its RFC appears in efos_list with status 'definitivo'.",
 "alternative": "A genuinely new consultancy with fast payment terms. Being on the "
                "69-B list is an act of the tax authority, not a fact about this "
                "company's dealings.",
 "support": "Exact RFC match against efos_list; publication_date 2025-03-14 precedes "
            "every one of the seven invoices; no contract row; four of seven "
            "invoices have no purchase order and the other three were raised the "
            "same day as the invoice.",
 "counter": "The vendor shares its address with three tenants that check out (D12), "
            "so the address proves nothing. Fast payment alone is a treasury "
            "practice, not a scheme.",
 "next_action": "get_vendor_contracts, get_vendor_purchase_orders, check_efos, then "
                "compare efos_list.publication_date against every issue_date.",
 "must_not": "Do not claim the invoices are fake or that the vendor does not exist. "
             "The defensible statement is that the issuer is on the definitive 69-B "
             "list and continued to invoice after publication, with no contract and "
             "incomplete purchase-order coverage in the supplied estate.",
 "expected_outcome": "authorize_probable",
 "path": ["EFOS match observed -> lead opened",
          "H1 phantom_vendor",
          "Challenger: could the listing post-date the trade? -> check publication_date",
          "Investigator: check_efos + get_vendor_invoices -> every invoice is later",
          "Challenger: is there a contract that explains it? -> get_vendor_contracts -> none found",
          "Method Critic: EFOS row and invoice rows are independent records -> clear",
          "Verifier: PHANTOM-EFOS-INVOICE-LINK + PESO-RECONCILIATION verified",
          "Gate: authorize_probable"],
},
"S2_kickback_1": {
 "kind": "true positive",
 "initial": "Every purchase order for this maintenance contractor is approved by the "
            "same director, and 4 to 9 days after each payment the contractor "
            "transfers about 7% of it to that director's personal account.",
 "alternative": "The director could hold a legitimate, disclosed commercial "
                "relationship with the contractor, or the transfers could be "
                "unrelated personal business between two parties who happen to bank "
                "with the same institution.",
 "support": "Five payment/transfer pairs; the percentage is stable at 6.8-7.5%; the "
            "lag is always under ten days; the recipient CLABE belongs to the same "
            "employee who signed each purchase order.",
 "counter": "The contractor has a real 2024 framework contract and performs work "
            "billed under it. The percentage being stable is consistent with an "
            "agreed commission, which would not be illegal if disclosed. The estate "
            "contains no conflict-of-interest register, so non-disclosure cannot be "
            "read from it.",
 "next_action": "get_bank_transactions_for_clabe on both CLABEs, get_vendor_purchase_orders "
                "to confirm the approver on every order, then test whether any OTHER "
                "employee receives transfers from this vendor.",
 "must_not": "Do not state that a bribe was paid or that the work was not performed. "
             "State the documented facts: same approver on every order, and a "
             "proportional payment from the supplier to that approver's account "
             "within days of each settlement.",
 "expected_outcome": "authorize_probable (needs a kickback verifier, which does not exist yet)",
 "path": ["vendor-to-employee transfer observed -> lead opened",
          "H1 kickback",
          "Challenger: same bank? different account? -> compare full 18-digit CLABE, not the 3-digit prefix",
          "Investigator: get_vendor_purchase_orders -> same approver on 5 of 5",
          "Challenger: is the contract real? -> get_vendor_contracts -> yes, work is real",
          "Method Critic: the payment leg and the transfer leg are different rows -> independent",
          "Conclusion: the conflict is documented; the intent is not"],
},
"S3_round_tripping_1": {
 "kind": "true positive",
 "initial": "Money leaves the operating account for a materials supplier, moves to a "
            "related distributor three days later at 98-99% of the amount, and comes "
            "back into the SAME operating account four days after that, settling one "
            "of our own sales invoices. Three times, in May, August and November 2025.",
 "alternative": "A group treasury arrangement: the company buys from one affiliate "
                "and sells to another, and balances net out. Circular flows inside a "
                "corporate group are ordinary.",
 "support": "Continuity of amount (each leg within 1.5% of the previous), continuity "
            "of time (3 to 4 days per leg), and the loop closes on the same CLABE it "
            "opened on. The sales invoice is settled by money that originated with us.",
 "counter": "Each outbound leg has a CFDI and a purchase order. The middle leg has no "
            "invoice in the estate at all, and absence of that record is not evidence "
            "the payment was improper - we only ever hold our own CFDIs.",
 "next_action": "Resolve the owner of every CLABE in the cycle, then check whether any "
                "leg is covered by an invoice, PO and contract that independently "
                "explain it. Compare against the benign cycle in D6.",
 "must_not": "Do not claim the goods were never delivered. A cycle in the transfer "
             "graph is a topological fact; same-money continuity is an inference that "
             "requires the amount and date continuity to be stated explicitly.",
 "expected_outcome": "need_more_work (no round_tripping verifier in scope)",
 "path": ["bank cycle detected -> lead opened",
          "H1 round_tripping",
          "Challenger: D6 has the same topology and is innocent - what distinguishes them?",
          "Investigator: compare amount deltas and date gaps leg by leg",
          "Method Critic: the cycle detector and the GNN both read the same bank rows - NOT independent corroboration",
          "Conclusion: structure plus continuity, reported as probable at most"],
},
"S4_threshold_splitting_1": {
 "kind": "true positive",
 "initial": "Six purchase orders between 96,400 and 99,180 pesos, raised over eleven "
            "days, same requester, same approver, describing sections A through F of "
            "one job that totals 586,830 pesos.",
 "alternative": "Genuinely phased delivery. Contractors do bill a large job in "
                "sections, and a coordinator approving six orders in his own band is "
                "not by itself irregular.",
 "support": "All six sit under 100,000; every other order in the estate above 100,000 "
            "is signed by a gerente or a director, and above 500,000 always by a "
            "director; the six descriptions are sections of a single scope; no "
            "contract covers them.",
 "counter": "The approval ladder is INFERRED from the data, not stated anywhere in "
            "the estate. Roughly one order in twenty in this estate is signed a level "
            "above the ladder, so the ladder is a tendency, not a rule. D7 shows twelve "
            "invoices just under the same number that are entirely legitimate.",
 "next_action": "get_vendor_contracts (none found), then reconstruct the approver "
                "distribution by amount band across the whole purchase_orders table "
                "and state the inferred threshold as an inference with its support.",
 "must_not": "Do not cite a statutory procurement threshold. No law in the estate "
             "fixes 100,000 pesos. The rule at issue is an internal approval limit "
             "inferred from the company's own approval pattern, and it must be "
             "presented as inferred.",
 "expected_outcome": "authorize_probable (needs a threshold_splitting verifier)",
 "path": ["invoice cluster detected -> lead opened",
          "H1 threshold_splitting",
          "Challenger: recurring services contract? -> get_vendor_contracts -> none",
          "Challenger: compare with D7, which IS covered by a contract",
          "Investigator: derive the approver-by-amount distribution",
          "Method Critic: the inferred threshold is a company tendency, label it as inferred",
          "Conclusion: reportable with the threshold stated as inferred"],
},
"S5_revenue_inflation_1": {
 "kind": "true positive",
 "initial": "Nine sales invoices totalling about 14.3 million pesos are issued in the "
            "last seven days of June 2025 and the last nine days of December 2025 to "
            "two related parties. Revenue is booked for all nine. Five are later "
            "cancelled. None is ever collected.",
 "alternative": "Ordinary year-end business. D8 shows that November and December are "
                "genuinely the company's heaviest months, and PPD terms legitimately "
                "delay settlement past the period end.",
 "support": "Zero of nine settled, against 93% settlement for every other customer; "
            "both counterparties share an address with the phantom vendor's tower and "
            "with each other; the ledger carries 4100 revenue credits for all nine "
            "with no reversing entry for the five cancelled ones.",
 "counter": "Invoices issued on 30 December under PPD terms would not be expected to "
            "settle inside the period. The cancellations may be ordinary commercial "
            "corrections. The estate contains no accounting policy that says a "
            "cancelled CFDI must be reversed.",
 "next_action": "Compute the settlement rate per customer over the whole period, then "
                "check whether the June batch - which had six months to settle - "
                "settled. It did not.",
 "must_not": "Do not claim the sales never happened. State that revenue was recognised "
             "on invoices that were subsequently cancelled or never settled, and that "
             "no reversing entry appears in the supplied ledger.",
 "expected_outcome": "need_more_work (no revenue_inflation detector or verifier)",
 "path": ["no detector fires today - this scheme is currently invisible",
          "Expected once a period-close detector exists: settlement-rate outlier -> lead",
          "Challenger: seasonality? -> compare with D8, which repeats across both years",
          "Investigator: check the June batch, which had six months to settle"],
},
"D1": {"expected_outcome": "decline_hypothesis", "closed_by": "investigator",
       "next_action": "get_vendor_contracts"},
"D2": {"expected_outcome": "no meaningful lead", "closed_by": "validator",
       "next_action": "check_efos on the exact RFC"},
"D3": {"expected_outcome": "no meaningful lead", "closed_by": "investigator",
       "next_action": "get_vendor_invoices - returns nothing"},
"D4": {"expected_outcome": "decline_hypothesis", "closed_by": "challenger",
       "next_action": "get_employee + get_vendor + get_vendor_contracts"},
"D5": {"expected_outcome": "decline_hypothesis", "closed_by": "challenger",
       "next_action": "get_bank_transactions_for_clabe, read the reference text"},
"D6": {"expected_outcome": "decline_hypothesis", "closed_by": "challenger",
       "next_action": "compare leg amounts and date gaps"},
"D7": {"expected_outcome": "decline_hypothesis", "closed_by": "investigator",
       "next_action": "get_vendor_contracts"},
"D8": {"expected_outcome": "decline_hypothesis", "closed_by": "investigator",
       "next_action": "get_vendor_invoices across both years (base rate)"},
"D9": {"expected_outcome": "decline_hypothesis", "closed_by": "investigator",
       "next_action": "get_vendor_purchase_orders"},
"D10": {"expected_outcome": "decline_hypothesis", "closed_by": "validator",
        "next_action": "read invoices.status before counting exposure"},
"D11": {"expected_outcome": "no meaningful lead", "closed_by": "investigator",
        "next_action": "get_vendor_contracts + get_vendor_purchase_orders"},
"D12": {"expected_outcome": "no meaningful lead", "closed_by": "challenger",
        "next_action": "get_vendor for each co-tenant"},
"D13": {"expected_outcome": "inconclusive", "closed_by": "challenger",
        "next_action": "get_vendor_contracts - returns nothing; say so precisely"},
"D14": {"expected_outcome": "no meaningful lead", "closed_by": "investigator",
        "next_action": "read metodo_pago before judging forma_pago"},
"D15": {"expected_outcome": "no meaningful lead", "closed_by": "challenger",
        "next_action": "get_vendor for each of the five"},
"D16": {"expected_outcome": "decline_hypothesis", "closed_by": "challenger",
        "next_action": "compare efos_list.publication_date with every issue_date"},
"D17": {"expected_outcome": "no meaningful lead", "closed_by": "investigator",
        "next_action": "recognise the SAT generic RFC"},
"D18": {"expected_outcome": "decline_hypothesis", "closed_by": "challenger",
        "next_action": "get_vendor_contracts for both RFCs and read clause seven"},
}


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

def _ids(rows, key, limit=None):
    values = [r[key] for r in rows]
    if limit and len(values) > limit:
        return ", ".join(values[:limit]) + " ... (+%d more)" % (len(values) - limit)
    return ", ".join(values) if values else "(none)"


def write_manifest(path: Path, b: EstateBuilder, w: World, gt: GroundTruth,
                   seed: int, counts: dict) -> None:
    L = []
    A = L.append
    A("# Robust realism estate - scenario manifest")
    A("")
    A("Seed `%d`. Period %s to %s. Company `RFC:%s` (%s)."
      % (seed, iso(PERIOD_START), iso(PERIOD_END), COMPANY_RFC, COMPANY_NAME))
    A("")
    A("> **This file is the answer key in prose.** It lives in `dev_eval/` and must "
      "never be reachable from `src/`.")
    A("")
    A("Company accounts: operating `%s`, treasury `%s`, petty cash `%s`."
      % (COMPANY_OPERATING, COMPANY_TREASURY, COMPANY_PETTY))
    A("")
    A("Internal approval ladder modelled in the data (stated nowhere in the estate; "
      "discoverable only from the relationship between `purchase_orders.amount` and "
      "`purchase_orders.approver`): under %.0f MXN a Coordinador may sign, under "
      "%.0f a Gerente, above that a Director. About one order in twenty is signed "
      "one level high, so the ladder is a tendency and not a rule."
      % (APPROVAL_L1, APPROVAL_L2))
    A("")
    A("## Row counts")
    A("")
    A("| table | rows |")
    A("|---|---:|")
    for table in OFFICIAL_TABLES:
        A("| `%s` | %d |" % (table, counts[table]))
    A("")
    A("## Planted schemes")
    A("")
    for s in gt.schemes:
        note = NOTES[s["scheme_id"]]
        A("### %s - `%s`" % (s["scheme_id"], s["type"]))
        A("")
        A("- **Classification:** %s" % note["kind"])
        A("- **Difficulty:** %s" % s["difficulty"])
        A("- **Entities:** %s" % ", ".join("`%s`" % e for e in s["entities"]))
        A("- **peso_amount:** %s MXN" % ("{:,.2f}".format(s["peso_amount"])))
        if s.get("_vendor"):
            A("- **Vendor CLABE:** `%s`" % s["_vendor"]["bank_clabe"])
        if s.get("_employee"):
            A("- **Employee CLABE:** `%s` (%s, %s)"
              % (s["_employee"]["bank_clabe"], s["_employee"]["emp_id"],
                 s["_employee"]["role"]))
        if s.get("_cycle_accounts"):
            A("- **Cycle accounts:** %s"
              % " -> ".join("`%s`" % c for c in s["_cycle_accounts"] + [s["_cycle_accounts"][0]]))
        if s.get("_efos_rfcs"):
            A("- **efos_list rows:** %s" % ", ".join("`%s`" % r for r in s["_efos_rfcs"]))
        if s.get("_publication"):
            A("- **EFOS publication_date:** %s" % iso(s["_publication"]))
        A("- **Invoice UUIDs:** %s" % _ids(s.get("_invoices", []), "uuid"))
        A("- **Transaction ids:** %s" % _ids(s.get("_txns", []), "txn_id"))
        if s.get("_payment_txns"):
            A("- **Company payment ids (context, not the illicit leg):** %s"
              % _ids(s["_payment_txns"], "txn_id"))
        if s.get("_context_txns"):
            A("- **Remaining cycle legs (context; adding them to the claim breaks "
              "per-table reconciliation):** %s" % _ids(s["_context_txns"], "txn_id"))
        if s.get("_context_invoices"):
            A("- **Related sales invoices (context; also cited by S5):** %s"
              % _ids(s["_context_invoices"], "uuid"))
        if s.get("_pos"):
            A("- **PO ids:** %s" % _ids(s["_pos"], "po_id"))
        A("")
        A("**First read:** %s" % note["initial"])
        A("")
        A("**Legitimate alternative:** %s" % note["alternative"])
        A("")
        A("**Strongest supporting evidence:** %s" % note["support"])
        A("")
        A("**Strongest counterevidence:** %s" % note["counter"])
        A("")
        A("**Expected next investigative action:** %s" % note["next_action"])
        A("")
        A("**What must NOT be concluded:** %s" % note["must_not"])
        A("")
        A("**Expected final outcome:** `%s`" % note["expected_outcome"])
        A("")
        A("**Expected investigation path:**")
        A("")
        A("```")
        for i, step in enumerate(note["path"]):
            A(("    " * min(i, 4)) + ("-> " if i else "") + step)
        A("```")
        A("")
    A("## Decoys - honest entities that trip a detector")
    A("")
    for d in gt.decoys:
        note = NOTES[d["_code"]]
        A("### %s - `%s`" % (d["_code"], d["entity"]))
        A("")
        A("- **Classification:** benign lookalike")
        A("- **Signal that fires:** `%s`" % d["signal"])
        A("- **Why innocent:** %s" % d["why_innocent"])
        for extra_key, label in (("_co_tenants", "Co-tenants at the same address"),
                                 ("_co_vendors", "Vendors sharing the mail domain"),
                                 ("_cycle_accounts", "Cycle accounts"),
                                 ("_employee", "Employee"),
                                 ("_confusable_with", "Confusable with")):
            if d.get(extra_key):
                value = d[extra_key]
                if isinstance(value, list):
                    value = ", ".join("`%s`" % v for v in value)
                else:
                    value = "`%s`" % value
                A("- **%s:** %s" % (label, value))
        A("- **Invoice UUIDs:** %s" % _ids(d.get("invoices", []), "uuid", 8))
        if d.get("_txns"):
            A("- **Transaction ids:** %s" % _ids(d["_txns"], "txn_id"))
        A("- **Fires on a production detector today:** %s"
          % ("yes" if d["_code"] in FIRES_TODAY else
             "no - aimed at a detector on the roadmap"))
        A("- **Expected next investigative action:** %s" % note["next_action"])
        A("- **What must NOT be concluded:** that the signal alone is the finding.")
        A("- **Expected final outcome:** `%s` (closed by %s)"
          % (note["expected_outcome"], note["closed_by"]))
        A("")
    A("## Scoring note")
    A("")
    A("Accusing any entity in the decoy section is a false accusation and is weighted "
      "at least as heavily as a missed scheme. An empty findings list scores better "
      "than a findings list that includes D2, D4, D6, D7 or D16.")
    A("")
    A("**The decoy list is not the whole innocent population.** Every entity in this "
      "estate that does not appear in the *Planted schemes* section above is honest. "
      "The decoy section names only the ones deliberately engineered as bait; the "
      "ordinary supplier base contains many more entities that a loose detector will "
      "flag. The scoring rule is therefore: an accusation against any entity absent "
      "from the schemes section is a false accusation, whether or not it is named "
      "here. At commit e10bb17 the short-window invoice-cluster detector fires on ten "
      "ordinary vendors that appear nowhere in this manifest - each of those is a "
      "lead that must be closed, not a finding.")
    A("")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Evidence dependency map
# --------------------------------------------------------------------------

UNIVERSAL_CONSUMERS = {
    "bank_txns": ["graph.bank_graph.build_bank_multidigraph",
                  "gnn.graph_builder.build_hetero_graph"],
    "vendors": ["gnn.graph_builder.build_hetero_graph"],
    "employees": ["gnn.graph_builder.build_hetero_graph"],
    "invoices": ["detectors.deterministic_relational."
                 "detect_short_window_similar_invoice_clusters"],
}


def _short_cycles(edges: set, max_len: int = 4) -> set:
    """Directed simple cycles up to max_len over a small CLABE graph."""
    adj: dict = {}
    for a, c in edges:
        adj.setdefault(a, set()).add(c)
    found = set()
    nodes = sorted(adj)
    for start in nodes:
        stack = [(start, (start,))]
        while stack:
            node, path = stack.pop()
            for nxt in sorted(adj.get(node, ())):
                if nxt == start and len(path) >= 2:
                    found.add(path)
                elif nxt not in path and len(path) < max_len and nxt > start:
                    stack.append((nxt, path + (nxt,)))
    return found


def build_evidence_dependency_map(b: EstateBuilder, gt: GroundTruth) -> dict:
    vendor_clabe = {}
    for v in b.vendors:
        vendor_clabe.setdefault(v["bank_clabe"], []).append(v["rfc"])
    employee_clabe = {}
    for e in b.employees:
        employee_clabe.setdefault(e["bank_clabe"], []).append(e["emp_id"])
    efos_rfcs = {e["rfc"] for e in b.efos}

    consumers: dict = {}

    def add(table, record_id, analytic):
        consumers.setdefault(table, {}).setdefault(str(record_id), set()).add(analytic)

    for v in b.vendors:
        add("vendors", v["rfc"], "gnn.graph_builder.build_hetero_graph")
        if v["rfc"] in efos_rfcs:
            add("vendors", v["rfc"],
                "detectors.deterministic_tabular.detect_efos_vendor_matches")
            add("vendors", v["rfc"],
                "verifier.official_verifier.PHANTOM-EFOS-INVOICE-LINK")
        if v["bank_clabe"] in employee_clabe:
            add("vendors", v["rfc"],
                "detectors.deterministic_tabular.detect_vendor_employee_shared_clabe")
        if len(vendor_clabe.get(v["bank_clabe"], [])) > 1:
            add("vendors", v["rfc"],
                "detectors.deterministic_tabular.detect_shared_vendor_clabe")

    for e in b.employees:
        add("employees", e["emp_id"], "gnn.graph_builder.build_hetero_graph")
        if e["bank_clabe"] in vendor_clabe:
            add("employees", e["emp_id"],
                "detectors.deterministic_tabular.detect_vendor_employee_shared_clabe")
            add("employees", e["emp_id"],
                "detectors.deterministic_relational.detect_vendor_to_employee_transfers")

    edges = {(t["from_clabe"], t["to_clabe"]) for t in b.bank}
    cycle_edges = set()
    for cycle in _short_cycles(edges):
        for i in range(len(cycle)):
            cycle_edges.add((cycle[i], cycle[(i + 1) % len(cycle)]))

    for t in b.bank:
        add("bank_txns", t["txn_id"], "graph.bank_graph.build_bank_multidigraph")
        add("bank_txns", t["txn_id"], "gnn.graph_builder.build_hetero_graph")
        if t["from_clabe"] in vendor_clabe and t["to_clabe"] in employee_clabe:
            add("bank_txns", t["txn_id"],
                "detectors.deterministic_relational.detect_vendor_to_employee_transfers")
        if (t["from_clabe"], t["to_clabe"]) in cycle_edges:
            add("bank_txns", t["txn_id"],
                "detectors.deterministic_relational.detect_directed_bank_transfer_cycles")

    for inv in b.invoices:
        add("invoices", inv["uuid"],
            "detectors.deterministic_relational."
            "detect_short_window_similar_invoice_clusters")
        if inv["issuer_rfc"] in efos_rfcs:
            add("invoices", inv["uuid"],
                "verifier.official_verifier.PHANTOM-EFOS-INVOICE-LINK")
            add("invoices", inv["uuid"], "output.reconciliation.reconcile_pesos")

    multi = {}
    for table in sorted(consumers):
        for record_id in sorted(consumers[table]):
            names = sorted(consumers[table][record_id])
            if len(names) >= 3:
                multi.setdefault(table, {})[record_id] = names

    # Same-money groups: one economic event seen in three tables.  Citing all
    # three is ONE fact rendered three ways, never three independent signals.
    ledger_by_invoice: dict = {}
    for row in b.ledger:
        if row["invoice_uuid"]:
            ledger_by_invoice.setdefault(row["invoice_uuid"], []).append(row["entry_id"])

    same_money = []
    for s in gt.schemes:
        for inv in s.get("_invoices", []):
            entry_ids = sorted(ledger_by_invoice.get(inv["uuid"], []))
            txn_ids = sorted(
                t["txn_id"] for t in b.bank
                if t["reference"].endswith(inv["uuid"][:8]))
            if entry_ids or txn_ids:
                same_money.append({
                    "scheme_id": s["scheme_id"],
                    "invoice_uuid": inv["uuid"],
                    "ledger_entry_ids": entry_ids,
                    "bank_txn_ids": txn_ids,
                    "note": "One economic event. An invoice, its GL entries and the "
                            "transfer that settled it are the same pesos in three "
                            "tables, not three independent corroborations.",
                })

    return {
        "_about": "Which analytics consume which records. DEV/EVAL ONLY. Built to "
                  "test whether the Method Critic notices that two signals rest on "
                  "the same underlying rows.",
        "seed": SEED,
        "universal_consumers": UNIVERSAL_CONSUMERS,
        "records_feeding_three_or_more_analytics": multi,
        "same_money_groups": same_money,
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def generate(out_dir: Path, seed: int = SEED) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    b, w, gt = build_world(seed)
    rows = table_rows(b)
    counts = {t: len(rows[t]) for t in OFFICIAL_TABLES}

    estate_path = out_dir / "robust_realism_estate.db"
    answers_path = out_dir / "robust_realism_estate_answers.db"
    zip_path = out_dir / "robust_realism_estate_csv.zip"
    gt_path = out_dir / "robust_realism_ground_truth.json"
    manifest_path = out_dir / "robust_realism_manifest.md"
    depmap_path = out_dir / "evidence_dependency_map.json"

    write_estate_db(estate_path, rows)
    write_estate_csv_zip(zip_path, rows)
    write_answers_db(answers_path, rows, gt, seed, NOTES)
    write_json(gt_path, build_ground_truth(gt, seed))
    write_manifest(manifest_path, b, w, gt, seed, counts)
    write_json(depmap_path, build_evidence_dependency_map(b, gt))

    return {
        "counts": counts,
        "schemes": len(gt.schemes),
        "decoys": len(gt.decoys),
        "estate": estate_path,
        "answers": answers_path,
        "zip": zip_path,
        "ground_truth": gt_path,
        "manifest": manifest_path,
        "dependency_map": depmap_path,
        "builder": b,
        "world": w,
        "gt": gt,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=str(HERE),
                    help="directory to write the estate and answer key into")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    result = generate(Path(args.out_dir), args.seed)
    print("seed %d  period %s..%s  company %s"
          % (args.seed, iso(PERIOD_START), iso(PERIOD_END), COMPANY_RFC))
    for table in OFFICIAL_TABLES:
        print("  %-16s %6d" % (table, result["counts"][table]))
    print("  schemes %d   decoys %d" % (result["schemes"], result["decoys"]))
    for key in ("estate", "answers", "zip", "ground_truth", "manifest",
                "dependency_map"):
        print("  wrote %s" % result[key])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
