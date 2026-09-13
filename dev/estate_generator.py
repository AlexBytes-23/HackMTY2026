"""Deterministic synthetic estate generator with a matching answer key.

Given a seed this module writes an ``estate.db`` that conforms exactly to
``estate_schema.sql`` (eight tables, official column names, official enum
values) and returns the answer key that conforms to ``ground_truth_schema.json``.

Determinism rules honoured here:

* the only source of randomness is ``random.Random(spec.seed)``;
* no wall-clock time, no ``uuid4``, no iteration over a ``set``;
* every row list is built in a fixed order and inserted in that order, so the
  same seed produces the same database bytes and the same answer-key bytes.

This module is development-only tooling.  Nothing under ``src/`` may import it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from random import Random
from typing import Any, Callable


# ==========================================================================
# OFFICIAL CONSTANTS
# ==========================================================================

#: The audited company is always the receiver of a purchase invoice.
COMPANY_RFC = "EMP920101AB1"

#: The audited company's operating account; the payer on every company payment.
COMPANY_CLABE = "000000000000000099"

COMPANY_LEGAL_NAME = "Empresa Auditada SA de CV"

IVA_RATE = 0.16

#: Every generated CFDI uses these SAT catalog codes; no planted scheme needs
#: a different combination.
USO_CFDI = "G03"
FORMA_PAGO = "03"
METODO_PAGO = "PUE"

#: Internal purchase-order approval limit *defined by this generator*.  It is a
#: control limit, not a legal threshold, and it is the number the
#: ``threshold_splitting`` scheme is built around.
INTERNAL_APPROVAL_THRESHOLD_MXN = 100_000.00

SCHEME_TYPES: tuple[str, ...] = (
    "phantom_vendor",
    "kickback",
    "round_tripping",
    "threshold_splitting",
    "revenue_inflation",
)

#: Fiscal window every generated estate lives in.
PERIOD_START = date(2026, 1, 5)
PERIOD_END = date(2026, 12, 18)
#: First-semester close, used by the revenue-inflation scheme and one decoy.
PERIOD_CLOSE = date(2026, 6, 30)


ESTATE_DDL = """
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


#: Official column order, used for both the DDL above and the INSERT order.
TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "vendors": (
        "rfc",
        "legal_name",
        "registered_date",
        "address",
        "bank_clabe",
        "category",
        "contact_email",
    ),
    "invoices": (
        "uuid",
        "issuer_rfc",
        "receiver_rfc",
        "issue_date",
        "subtotal",
        "iva",
        "total",
        "concepto_text",
        "uso_cfdi",
        "forma_pago",
        "metodo_pago",
        "status",
    ),
    "ledger": (
        "entry_id",
        "date",
        "account_code",
        "account_name",
        "debit",
        "credit",
        "description",
        "invoice_uuid",
        "cost_center",
        "approver",
    ),
    "bank_txns": (
        "txn_id",
        "date",
        "from_clabe",
        "to_clabe",
        "amount",
        "reference",
        "channel",
    ),
    "purchase_orders": (
        "po_id",
        "vendor_rfc",
        "date",
        "amount",
        "requester",
        "approver",
        "description",
    ),
    "contracts": (
        "contract_id",
        "vendor_rfc",
        "start_date",
        "value",
        "scope_text",
    ),
    "employees": (
        "emp_id",
        "name",
        "role",
        "bank_clabe",
        "hire_date",
    ),
    "efos_list": (
        "rfc",
        "legal_name",
        "status",
        "publication_date",
    ),
}


# ==========================================================================
# VOCABULARY (fixed tuples: never a set, never shuffled in place)
# ==========================================================================

_RFC_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_RFC_HOMOCLAVE = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_BANK_CODES = ("002", "012", "014", "021", "044", "072", "102", "127")

_VENDOR_PREFIXES = (
    "Aceros",
    "Servicios Integrales",
    "Logistica",
    "Constructora",
    "Comercializadora",
    "Tecnologia",
    "Grupo Industrial",
    "Suministros",
    "Refacciones",
    "Ingenieria",
    "Mantenimiento",
    "Distribuidora",
    "Consultoria",
    "Transportes",
    "Papeleria",
    "Seguridad Privada",
    "Limpieza",
    "Capacitacion",
    "Arrendadora",
    "Insumos",
)

_VENDOR_ROOTS = (
    "del Norte",
    "Regia",
    "Monterrey",
    "Cumbres",
    "Santa Catarina",
    "Apodaca",
    "Escobedo",
    "Linda Vista",
    "Valle Alto",
    "San Nicolas",
    "Mitras",
    "Contry",
)

_VENDOR_SUFFIXES = ("SA de CV", "S de RL de CV", "SC", "SAPI de CV")

_CATEGORIES = (
    "Consultoria",
    "Mantenimiento",
    "Insumos",
    "Logistica",
    "Tecnologia",
    "Limpieza",
    "Seguridad",
    "Papeleria",
    "Capacitacion",
    "Arrendamiento",
)

_STREETS = (
    "Av. Constitucion",
    "Calle Hidalgo",
    "Av. Lazaro Cardenas",
    "Calle Zaragoza",
    "Av. Gonzalitos",
    "Calle Morelos",
    "Av. Revolucion",
    "Calle Juarez",
)

_CITIES = (
    "Monterrey, NL",
    "San Pedro Garza Garcia, NL",
    "Guadalupe, NL",
    "Apodaca, NL",
    "San Nicolas de los Garza, NL",
    "Santa Catarina, NL",
)

_FIRST_NAMES = (
    "Ana",
    "Bruno",
    "Carla",
    "Diego",
    "Elena",
    "Fernando",
    "Gabriela",
    "Hector",
    "Irene",
    "Javier",
    "Karla",
    "Luis",
    "Mariana",
    "Noe",
    "Olivia",
    "Pablo",
)

_SURNAMES = (
    "Garza",
    "Trevino",
    "Ramos",
    "Villarreal",
    "Cantu",
    "Salinas",
    "Elizondo",
    "Montemayor",
    "Zavala",
    "Ibarra",
    "Robles",
    "Quiroga",
    "Ochoa",
    "Nava",
    "Lozano",
    "Guerra",
)

#: Index 0 is the finance director, 1 the purchasing manager, 2 the buyer.
#: The schemes below depend on that ordering.
_ROLES = (
    "Director de Finanzas",
    "Gerente de Compras",
    "Analista de Compras",
    "Contador General",
    "Tesorero",
    "Gerente de Operaciones",
    "Analista Contable",
    "Auditor Interno",
)

_COST_CENTERS = (
    "CC-100 Produccion",
    "CC-200 Administracion",
    "CC-300 Ventas",
    "CC-400 Mantenimiento",
    "CC-500 Sistemas",
)

_CONCEPTOS = (
    "Suministro de refacciones industriales",
    "Mantenimiento preventivo de equipo",
    "Servicio de transporte de carga",
    "Licenciamiento de software administrativo",
    "Material de limpieza para planta",
    "Servicio de vigilancia mensual",
    "Papeleria y consumibles de oficina",
    "Capacitacion tecnica para operadores",
    "Arrendamiento de maquinaria ligera",
    "Consultoria de mejora de procesos",
)

_EXPENSE_ACCOUNT = ("5000", "Gastos operativos")
_PAYABLE_ACCOUNT = ("2100", "Cuentas por pagar")
_RECEIVABLE_ACCOUNT = ("1200", "Clientes por cobrar")
_REVENUE_ACCOUNT = ("4000", "Ingresos por ventas")

_NO_APPROVER = "Sin autorizacion registrada"


# ==========================================================================
# PUBLIC SPEC
# ==========================================================================

@dataclass(frozen=True)
class EstateSpec:
    """What to plant in one generated estate."""

    seed: int
    n_honest_vendors: int = 18
    n_employees: int = 8
    schemes: tuple[str, ...] = SCHEME_TYPES
    n_decoys: int = 10


def generate_estate(spec: EstateSpec, db_path: str | Path) -> dict:
    """Write estate.db at db_path. Return the ground-truth dict."""

    builder = _EstateBuilder(spec)
    builder.build()
    builder.write(db_path)
    return builder.answer_key()


def dump_answer_key(answer_key: dict, path: str | Path) -> Path:
    """Write the answer key as UTF-8 JSON with byte-stable formatting."""

    target = Path(path)
    if target.parent != Path(""):
        target.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(answer_key, indent=2, ensure_ascii=False) + "\n"
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)

    return target


# ==========================================================================
# BUILDER
# ==========================================================================

class _EstateBuilder:
    """Accumulates estate rows and the matching answer-key entries."""

    def __init__(self, spec: EstateSpec) -> None:
        self.spec = spec

        self.vendors: list[dict[str, Any]] = []
        self.invoices: list[dict[str, Any]] = []
        self.ledger: list[dict[str, Any]] = []
        self.bank_txns: list[dict[str, Any]] = []
        self.purchase_orders: list[dict[str, Any]] = []
        self.contracts: list[dict[str, Any]] = []
        self.employees: list[dict[str, Any]] = []
        self.efos_list: list[dict[str, Any]] = []
        self.schemes: list[dict[str, Any]] = []
        self.decoys: list[dict[str, Any]] = []

        unknown = [
            scheme for scheme in spec.schemes if scheme not in SCHEME_TYPES
        ]
        if unknown:
            raise ValueError(
                "Unknown scheme types: " + ", ".join(sorted(unknown))
            )
        if spec.n_honest_vendors < 1:
            raise ValueError("n_honest_vendors must be at least 1.")
        if spec.n_employees < 3:
            raise ValueError(
                "n_employees must be at least 3; the schemes need a finance "
                "director, a purchasing manager and a buyer."
            )
        if spec.n_decoys < 0:
            raise ValueError("n_decoys must not be negative.")

        self.rng = Random(spec.seed)
        self._used_rfcs: set[str] = {COMPANY_RFC}
        self._used_clabes: set[str] = {COMPANY_CLABE}
        self._used_names: set[str] = {COMPANY_LEGAL_NAME}
        self._counters: dict[str, int] = {
            "invoices": 0,
            "bank_txns": 0,
            "purchase_orders": 0,
            "contracts": 0,
            "ledger": 0,
        }

    # ------------------------------------------------------------------
    # primitive generators
    # ------------------------------------------------------------------

    def _next_id(self, kind: str, prefix: str) -> str:
        self._counters[kind] += 1
        return f"{prefix}-{self._counters[kind]:05d}"

    def _next_entry_id(self) -> int:
        self._counters["ledger"] += 1
        return self._counters["ledger"]

    def _new_rfc(self) -> str:
        while True:
            letters = "".join(
                self.rng.choice(_RFC_LETTERS) for _ in range(3)
            )
            year = self.rng.randint(0, 99)
            month = self.rng.randint(1, 12)
            day = self.rng.randint(1, 28)
            homoclave = "".join(
                self.rng.choice(_RFC_HOMOCLAVE) for _ in range(3)
            )
            rfc = f"{letters}{year:02d}{month:02d}{day:02d}{homoclave}"
            if rfc not in self._used_rfcs:
                self._used_rfcs.add(rfc)
                return rfc

    def _new_clabe(self, bank_code: str | None = None) -> str:
        while True:
            bank = bank_code or self.rng.choice(_BANK_CODES)
            account = self.rng.randrange(10**15)
            clabe = f"{bank}{account:015d}"
            if clabe not in self._used_clabes:
                self._used_clabes.add(clabe)
                return clabe

    def _new_legal_name(self) -> str:
        while True:
            name = " ".join(
                (
                    self.rng.choice(_VENDOR_PREFIXES),
                    self.rng.choice(_VENDOR_ROOTS),
                    self.rng.choice(_VENDOR_SUFFIXES),
                )
            )
            if name not in self._used_names:
                self._used_names.add(name)
                return name

    def _new_address(self) -> str:
        return (
            f"{self.rng.choice(_STREETS)} {self.rng.randint(100, 4999)}, "
            f"{self.rng.choice(_CITIES)}"
        )

    @staticmethod
    def _email_for(legal_name: str, rfc: str) -> str:
        words = [
            word
            for word in legal_name.split()
            if word not in {"SA", "de", "CV", "S", "RL", "SC", "SAPI"}
        ]
        slug = "-".join(word.lower() for word in words[:2]) or "proveedor"
        return f"cuentas@{slug}-{rfc[-3:].lower()}.mx"

    def _employee_at(self, index: int) -> dict[str, Any]:
        return self.employees[index % len(self.employees)]

    def _random_employee(self) -> dict[str, Any]:
        return self.employees[self.rng.randrange(len(self.employees))]

    def _cost_center(self) -> str:
        return self.rng.choice(_COST_CENTERS)

    @staticmethod
    def _split_total(subtotal: float) -> tuple[float, float, float]:
        base = round(float(subtotal), 2)
        iva = round(base * IVA_RATE, 2)
        return base, iva, round(base + iva, 2)

    # ------------------------------------------------------------------
    # row writers
    # ------------------------------------------------------------------

    def _add_vendor(
        self,
        *,
        rfc: str,
        legal_name: str,
        registered_date: date,
        bank_clabe: str,
        category: str,
    ) -> dict[str, Any]:
        row = {
            "rfc": rfc,
            "legal_name": legal_name,
            "registered_date": registered_date.isoformat(),
            "address": self._new_address(),
            "bank_clabe": bank_clabe,
            "category": category,
            "contact_email": self._email_for(legal_name, rfc),
        }
        self.vendors.append(row)
        return row

    def _new_vendor(
        self,
        *,
        registered_date: date,
        category: str | None = None,
        bank_code: str | None = None,
        bank_clabe: str | None = None,
    ) -> dict[str, Any]:
        return self._add_vendor(
            rfc=self._new_rfc(),
            legal_name=self._new_legal_name(),
            registered_date=registered_date,
            bank_clabe=bank_clabe or self._new_clabe(bank_code),
            category=category or self.rng.choice(_CATEGORIES),
        )

    def _add_efos(
        self,
        *,
        rfc: str,
        legal_name: str,
        status: str,
        publication_date: date,
    ) -> dict[str, Any]:
        if status not in {"definitivo", "presunto"}:
            raise ValueError(f"Invalid efos_list status: {status}")
        row = {
            "rfc": rfc,
            "legal_name": legal_name,
            "status": status,
            "publication_date": publication_date.isoformat(),
        }
        self.efos_list.append(row)
        return row

    def _add_invoice(
        self,
        *,
        issuer_rfc: str,
        receiver_rfc: str,
        issue_date: date,
        subtotal: float,
        concepto_text: str,
        status: str = "vigente",
    ) -> dict[str, Any]:
        base, iva, total = self._split_total(subtotal)
        row = {
            "uuid": self._next_id("invoices", "INV"),
            "issuer_rfc": issuer_rfc,
            "receiver_rfc": receiver_rfc,
            "issue_date": issue_date.isoformat(),
            "subtotal": base,
            "iva": iva,
            "total": total,
            "concepto_text": concepto_text,
            "uso_cfdi": USO_CFDI,
            "forma_pago": FORMA_PAGO,
            "metodo_pago": METODO_PAGO,
            "status": status,
        }
        self.invoices.append(row)
        return row

    def _add_ledger_pair(
        self,
        *,
        entry_date: date,
        debit_account: tuple[str, str],
        credit_account: tuple[str, str],
        value: float,
        description: str,
        invoice_uuid: str | None,
        cost_center: str,
        approver: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for account, is_debit in ((debit_account, True), (credit_account, False)):
            row = {
                "entry_id": self._next_entry_id(),
                "date": entry_date.isoformat(),
                "account_code": account[0],
                "account_name": account[1],
                "debit": round(value, 2) if is_debit else 0.0,
                "credit": 0.0 if is_debit else round(value, 2),
                "description": description,
                "invoice_uuid": invoice_uuid,
                "cost_center": cost_center,
                "approver": approver,
            }
            self.ledger.append(row)
            rows.append(row)
        return rows

    def _add_bank_txn(
        self,
        *,
        txn_date: date,
        from_clabe: str,
        to_clabe: str,
        amount: float,
        reference: str,
        channel: str = "SPEI",
    ) -> dict[str, Any]:
        row = {
            "txn_id": self._next_id("bank_txns", "BNK"),
            "date": txn_date.isoformat(),
            "from_clabe": from_clabe,
            "to_clabe": to_clabe,
            "amount": round(float(amount), 2),
            "reference": reference,
            "channel": channel,
        }
        self.bank_txns.append(row)
        return row

    def _add_purchase_order(
        self,
        *,
        vendor_rfc: str,
        po_date: date,
        amount: float,
        requester: str,
        approver: str,
        description: str,
    ) -> dict[str, Any]:
        row = {
            "po_id": self._next_id("purchase_orders", "PO"),
            "vendor_rfc": vendor_rfc,
            "date": po_date.isoformat(),
            "amount": round(float(amount), 2),
            "requester": requester,
            "approver": approver,
            "description": description,
        }
        self.purchase_orders.append(row)
        return row

    def _add_contract(
        self,
        *,
        vendor_rfc: str,
        start_date: date,
        value: float,
        scope_text: str,
    ) -> dict[str, Any]:
        row = {
            "contract_id": self._next_id("contracts", "CTR"),
            "vendor_rfc": vendor_rfc,
            "start_date": start_date.isoformat(),
            "value": round(float(value), 2),
            "scope_text": scope_text,
        }
        self.contracts.append(row)
        return row

    # ------------------------------------------------------------------
    # composite writers
    # ------------------------------------------------------------------

    def _book_purchase(
        self,
        *,
        vendor: dict[str, Any],
        issue_date: date,
        subtotal: float,
        concepto_text: str,
        approver: str,
        cost_center: str | None = None,
        status: str = "vigente",
        pay_after_days: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Invoice + balanced ledger pair + (optionally) the payment."""

        centre = cost_center or self._cost_center()
        invoice = self._add_invoice(
            issuer_rfc=vendor["rfc"],
            receiver_rfc=COMPANY_RFC,
            issue_date=issue_date,
            subtotal=subtotal,
            concepto_text=concepto_text,
            status=status,
        )
        self._add_ledger_pair(
            entry_date=issue_date,
            debit_account=_EXPENSE_ACCOUNT,
            credit_account=_PAYABLE_ACCOUNT,
            value=invoice["total"],
            description=f"Registro factura {invoice['uuid']}",
            invoice_uuid=invoice["uuid"],
            cost_center=centre,
            approver=approver,
        )

        payment = None
        if pay_after_days is not None:
            payment = self._add_bank_txn(
                txn_date=issue_date + timedelta(days=pay_after_days),
                from_clabe=COMPANY_CLABE,
                to_clabe=vendor["bank_clabe"],
                amount=invoice["total"],
                reference=f"Pago factura {invoice['uuid']}",
            )

        return invoice, payment

    def _book_revenue(
        self,
        *,
        customer_rfc: str,
        issue_date: date,
        subtotal: float,
        concepto_text: str,
        approver: str,
        status: str = "vigente",
        cost_center: str | None = None,
    ) -> dict[str, Any]:
        invoice = self._add_invoice(
            issuer_rfc=COMPANY_RFC,
            receiver_rfc=customer_rfc,
            issue_date=issue_date,
            subtotal=subtotal,
            concepto_text=concepto_text,
            status=status,
        )
        self._add_ledger_pair(
            entry_date=issue_date,
            debit_account=_RECEIVABLE_ACCOUNT,
            credit_account=_REVENUE_ACCOUNT,
            value=invoice["total"],
            description=f"Reconocimiento de ingreso {invoice['uuid']}",
            invoice_uuid=invoice["uuid"],
            cost_center=cost_center or "CC-300 Ventas",
            approver=approver,
        )
        return invoice

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    def build(self) -> None:
        self._build_employees()
        self._build_honest_vendors()
        self._build_schemes()
        self._build_decoys()

    def _build_employees(self) -> None:
        for index in range(self.spec.n_employees):
            first = _FIRST_NAMES[index % len(_FIRST_NAMES)]
            last = _SURNAMES[(index * 3 + 1) % len(_SURNAMES)]
            name = f"{first} {last}"
            suffix = 2
            while name in self._used_names:
                name = f"{first} {last} {suffix}"
                suffix += 1
            self._used_names.add(name)

            self.employees.append(
                {
                    "emp_id": f"{index + 1:04d}",
                    "name": name,
                    "role": _ROLES[index % len(_ROLES)],
                    "bank_clabe": self._new_clabe(),
                    "hire_date": (
                        PERIOD_START
                        - timedelta(days=self.rng.randint(400, 3000))
                    ).isoformat(),
                }
            )

    def _build_honest_vendors(self) -> None:
        for index in range(self.spec.n_honest_vendors):
            vendor = self._new_vendor(
                registered_date=PERIOD_START
                - timedelta(days=self.rng.randint(400, 2200)),
            )

            n_invoices = self.rng.randint(1, 3)
            issue_date = PERIOD_START + timedelta(days=self.rng.randint(5, 60))
            requester = self._random_employee()
            approver = self._employee_at(1)
            cost_center = self._cost_center()

            invoices: list[dict[str, Any]] = []
            for _ in range(n_invoices):
                subtotal = round(self.rng.uniform(15_000.0, 380_000.0), 2)
                invoice, _payment = self._book_purchase(
                    vendor=vendor,
                    issue_date=issue_date,
                    subtotal=subtotal,
                    concepto_text=self.rng.choice(_CONCEPTOS),
                    approver=approver["name"],
                    cost_center=cost_center,
                    pay_after_days=self.rng.randint(12, 40),
                )
                invoices.append(invoice)
                # Keep a vendor's invoices more than one detector window apart
                # so an honest vendor never looks like a split-purchase burst.
                issue_date = issue_date + timedelta(
                    days=self.rng.randint(25, 70)
                )

            if index % 2 == 0:
                self._add_contract(
                    vendor_rfc=vendor["rfc"],
                    start_date=PERIOD_START
                    - timedelta(days=self.rng.randint(30, 300)),
                    value=round(
                        sum(invoice["total"] for invoice in invoices), 2
                    ),
                    scope_text=(
                        f"Contrato de {vendor['category'].lower()} con alcance "
                        "y tarifas anexas."
                    ),
                )
            else:
                for invoice in invoices:
                    self._add_purchase_order(
                        vendor_rfc=vendor["rfc"],
                        po_date=date.fromisoformat(invoice["issue_date"])
                        - timedelta(days=self.rng.randint(4, 20)),
                        amount=invoice["total"],
                        requester=requester["name"],
                        approver=approver["name"],
                        description=invoice["concepto_text"],
                    )

    # ------------------------------------------------------------------
    # schemes
    # ------------------------------------------------------------------

    def _build_schemes(self) -> None:
        builders: dict[str, Callable[[str], dict[str, Any]]] = {
            "phantom_vendor": self._scheme_phantom_vendor,
            "kickback": self._scheme_kickback,
            "round_tripping": self._scheme_round_tripping,
            "threshold_splitting": self._scheme_threshold_splitting,
            "revenue_inflation": self._scheme_revenue_inflation,
        }
        for position, scheme_type in enumerate(self.spec.schemes, start=1):
            scheme_id = f"S{position}_{scheme_type}_1"
            self.schemes.append(builders[scheme_type](scheme_id))

    def _scheme_phantom_vendor(self, scheme_id: str) -> dict[str, Any]:
        vendor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=21),
            category="Consultoria",
        )
        publication_date = PERIOD_START + timedelta(days=30)
        self._add_efos(
            rfc=vendor["rfc"],
            legal_name=vendor["legal_name"],
            status="definitivo",
            publication_date=publication_date,
        )

        vague = (
            "Servicios profesionales diversos",
            "Asesoria general",
            "Apoyo administrativo",
        )

        invoices: list[dict[str, Any]] = []
        payments: list[dict[str, Any]] = []
        for step in range(3):
            invoice, payment = self._book_purchase(
                vendor=vendor,
                issue_date=publication_date + timedelta(days=25 + 35 * step),
                subtotal=round(self.rng.uniform(210_000.0, 330_000.0), 2),
                concepto_text=vague[step],
                approver=_NO_APPROVER,
                cost_center="CC-200 Administracion",
                pay_after_days=self.rng.randint(5, 9),
            )
            invoices.append(invoice)
            assert payment is not None
            payments.append(payment)

        return {
            "scheme_id": scheme_id,
            "type": "phantom_vendor",
            "entities": [f"RFC:{vendor['rfc']}"],
            "supporting_invoices": [invoice["uuid"] for invoice in invoices],
            "supporting_txns": [payment["txn_id"] for payment in payments],
            "peso_amount": round(
                sum(invoice["total"] for invoice in invoices), 2
            ),
            "difficulty": "easy",
        }

    def _scheme_kickback(self, scheme_id: str) -> dict[str, Any]:
        vendor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=180),
            category="Refacciones",
        )
        employee = self._employee_at(1)
        requester = self._employee_at(5)

        invoices: list[dict[str, Any]] = []
        txns: list[dict[str, Any]] = []
        issue_date = PERIOD_START + timedelta(days=40)

        for _ in range(3):
            subtotal = round(self.rng.uniform(420_000.0, 560_000.0), 2)
            invoice, payment = self._book_purchase(
                vendor=vendor,
                issue_date=issue_date,
                subtotal=subtotal,
                concepto_text="Suministro de refacciones industriales",
                approver=employee["name"],
                cost_center="CC-100 Produccion",
                pay_after_days=self.rng.randint(10, 18),
            )
            assert payment is not None
            self._add_purchase_order(
                vendor_rfc=vendor["rfc"],
                po_date=issue_date - timedelta(days=self.rng.randint(5, 12)),
                amount=invoice["total"],
                requester=requester["name"],
                approver=employee["name"],
                description="Suministro de refacciones industriales",
            )
            kickback = self._add_bank_txn(
                txn_date=date.fromisoformat(payment["date"])
                + timedelta(days=self.rng.randint(2, 4)),
                from_clabe=vendor["bank_clabe"],
                to_clabe=employee["bank_clabe"],
                amount=round(invoice["total"] * 0.12, 2),
                reference="Transferencia SPEI",
            )

            invoices.append(invoice)
            txns.extend([payment, kickback])
            issue_date = issue_date + timedelta(days=self.rng.randint(20, 35))

        return {
            "scheme_id": scheme_id,
            "type": "kickback",
            "entities": [
                f"RFC:{vendor['rfc']}",
                f"EMP:{employee['emp_id']}",
            ],
            "supporting_invoices": [invoice["uuid"] for invoice in invoices],
            "supporting_txns": sorted(txn["txn_id"] for txn in txns),
            "peso_amount": round(
                sum(invoice["total"] for invoice in invoices), 2
            ),
            "difficulty": "medium",
        }

    def _scheme_round_tripping(self, scheme_id: str) -> dict[str, Any]:
        n_accounts = self.rng.choice((3, 4))
        n_conduits = n_accounts - 1

        conduits = [
            self._new_vendor(
                registered_date=PERIOD_START
                - timedelta(days=self.rng.randint(40, 120)),
                category="Consultoria",
            )
            for _ in range(n_conduits)
        ]

        approver = self._employee_at(0)
        issue_date = PERIOD_START + timedelta(days=self.rng.randint(90, 150))
        cover_invoice, outbound = self._book_purchase(
            vendor=conduits[0],
            issue_date=issue_date,
            subtotal=round(self.rng.uniform(1_100_000.0, 1_400_000.0), 2),
            concepto_text="Servicios de integracion de proyecto",
            approver=approver["name"],
            cost_center="CC-500 Sistemas",
            pay_after_days=self.rng.randint(6, 12),
        )
        assert outbound is not None
        self._add_purchase_order(
            vendor_rfc=conduits[0]["rfc"],
            po_date=issue_date - timedelta(days=7),
            amount=cover_invoice["total"],
            requester=approver["name"],
            approver=approver["name"],
            description="Servicios de integracion de proyecto",
        )

        cycle_txns = [outbound]
        amount = outbound["amount"]
        txn_date = date.fromisoformat(outbound["date"])
        path = [conduit["bank_clabe"] for conduit in conduits] + [COMPANY_CLABE]

        for hop in range(len(path) - 1):
            amount = round(amount * 0.985, 2)
            txn_date = txn_date + timedelta(days=self.rng.randint(2, 5))
            cycle_txns.append(
                self._add_bank_txn(
                    txn_date=txn_date,
                    from_clabe=path[hop],
                    to_clabe=path[hop + 1],
                    amount=amount,
                    reference="Traspaso entre cuentas",
                )
            )

        return {
            "scheme_id": scheme_id,
            "type": "round_tripping",
            "entities": [f"RFC:{conduit['rfc']}" for conduit in conduits],
            "supporting_invoices": [cover_invoice["uuid"]],
            "supporting_txns": [txn["txn_id"] for txn in cycle_txns],
            "peso_amount": round(outbound["amount"], 2),
            "difficulty": "hard",
        }

    def _scheme_threshold_splitting(self, scheme_id: str) -> dict[str, Any]:
        vendor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=95),
            category="Mantenimiento",
        )
        approver = self._employee_at(2)
        requester = self._employee_at(6)

        first_date = PERIOD_START + timedelta(days=self.rng.randint(170, 220))
        day_offsets = (0, 1, 3, 4, 6)
        # Each subtotal keeps the invoice total just under the internal limit
        # while the five totals stay within the similarity window a cluster
        # detector uses.
        subtotals = (83_500.00, 84_100.00, 83_900.00, 84_400.00, 83_700.00)

        invoices: list[dict[str, Any]] = []
        payments: list[dict[str, Any]] = []
        for offset, subtotal in zip(day_offsets, subtotals):
            issue_date = first_date + timedelta(days=offset)
            invoice, payment = self._book_purchase(
                vendor=vendor,
                issue_date=issue_date,
                subtotal=subtotal,
                concepto_text="Mantenimiento correctivo de linea de produccion",
                approver=approver["name"],
                cost_center="CC-400 Mantenimiento",
                pay_after_days=self.rng.randint(9, 16),
            )
            assert payment is not None
            if invoice["total"] >= INTERNAL_APPROVAL_THRESHOLD_MXN:
                raise AssertionError(
                    "A split invoice must stay under the internal limit."
                )
            self._add_purchase_order(
                vendor_rfc=vendor["rfc"],
                po_date=issue_date - timedelta(days=2),
                amount=invoice["total"],
                requester=requester["name"],
                approver=approver["name"],
                description="Mantenimiento correctivo de linea de produccion",
            )
            invoices.append(invoice)
            payments.append(payment)

        return {
            "scheme_id": scheme_id,
            "type": "threshold_splitting",
            "entities": [
                f"RFC:{vendor['rfc']}",
                f"EMP:{approver['emp_id']}",
            ],
            "supporting_invoices": [invoice["uuid"] for invoice in invoices],
            "supporting_txns": [payment["txn_id"] for payment in payments],
            "peso_amount": round(
                sum(invoice["total"] for invoice in invoices), 2
            ),
            "difficulty": "medium",
            "notes": (
                "Internal purchase-order approval limit defined by this "
                f"generator: MXN {INTERNAL_APPROVAL_THRESHOLD_MXN:,.2f} per "
                "purchase order. It is an internal control limit, not a legal "
                "threshold."
            ),
        }

    def _scheme_revenue_inflation(self, scheme_id: str) -> dict[str, Any]:
        approver = self._employee_at(0)
        customers = [self._new_rfc() for _ in range(2)]

        invoices: list[dict[str, Any]] = []
        # Two invoices are cancelled after the close but stay booked as revenue;
        # two remain vigente with no bank receipt anywhere in the estate.
        plan = (
            (customers[0], "cancelado", 5),
            (customers[1], "cancelado", 3),
            (customers[0], "vigente", 2),
            (customers[1], "vigente", 1),
        )
        for customer_rfc, status, days_before_close in plan:
            invoices.append(
                self._book_revenue(
                    customer_rfc=customer_rfc,
                    issue_date=PERIOD_CLOSE - timedelta(days=days_before_close),
                    subtotal=round(self.rng.uniform(650_000.0, 900_000.0), 2),
                    concepto_text="Venta de producto terminado",
                    approver=approver["name"],
                    status=status,
                )
            )

        return {
            "scheme_id": scheme_id,
            "type": "revenue_inflation",
            "entities": [
                f"RFC:{COMPANY_RFC}",
                f"EMP:{approver['emp_id']}",
            ],
            "supporting_invoices": [invoice["uuid"] for invoice in invoices],
            # Deliberately empty: the absence of any bank receipt for these
            # invoices is the evidence.
            "supporting_txns": [],
            "peso_amount": round(
                sum(invoice["total"] for invoice in invoices), 2
            ),
            "difficulty": "hard",
        }

    # ------------------------------------------------------------------
    # decoys
    # ------------------------------------------------------------------

    def _build_decoys(self) -> None:
        builders: tuple[Callable[[], dict[str, Any]], ...] = (
            self._decoy_efos_presunto_before_publication,
            self._decoy_same_bank_different_account,
            self._decoy_refund_two_cycle,
            self._decoy_framework_contract_cluster,
            self._decoy_cancelled_and_reissued,
            self._decoy_group_shared_treasury_account,
            self._decoy_employee_expense_reimbursement,
            self._decoy_volume_rebate_cycle,
            self._decoy_efos_vendor_without_activity,
            self._decoy_individually_approved_cluster,
        )
        for index in range(self.spec.n_decoys):
            self.decoys.append(builders[index % len(builders)]())

    def _decoy_efos_presunto_before_publication(self) -> dict[str, Any]:
        vendor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=900),
            category="Logistica",
        )
        publication_date = PERIOD_START + timedelta(days=300)

        contract = self._add_contract(
            vendor_rfc=vendor["rfc"],
            start_date=PERIOD_START - timedelta(days=120),
            value=1_000_000.00,
            scope_text="Contrato anual de transporte de carga por ruta.",
        )
        invoices = [
            self._book_purchase(
                vendor=vendor,
                issue_date=PERIOD_START + timedelta(days=offset),
                subtotal=round(self.rng.uniform(90_000.0, 160_000.0), 2),
                concepto_text="Servicio de transporte de carga",
                approver=self._employee_at(1)["name"],
                cost_center="CC-100 Produccion",
                pay_after_days=self.rng.randint(15, 30),
            )[0]
            for offset in (30, 70)
        ]
        self._add_efos(
            rfc=vendor["rfc"],
            legal_name=vendor["legal_name"],
            status="presunto",
            publication_date=publication_date,
        )

        return {
            "entity": f"RFC:{vendor['rfc']}",
            "signal": "vendor_efos_record_match",
            "why_innocent": (
                f"Contract {contract['contract_id']} and both invoices are "
                f"dated before the EFOS publication date "
                f"{publication_date.isoformat()}, and no invoice or transfer "
                "for this vendor after that date was found in the supplied "
                "estate."
            ),
            "invoices": [invoice["uuid"] for invoice in invoices],
        }

    def _decoy_same_bank_different_account(self) -> dict[str, Any]:
        employee = self._employee_at(3)
        vendor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=700),
            category="Papeleria",
            bank_code=employee["bank_clabe"][:3],
        )
        self._add_contract(
            vendor_rfc=vendor["rfc"],
            start_date=PERIOD_START - timedelta(days=200),
            value=240_000.00,
            scope_text="Suministro de papeleria con precios de catalogo.",
        )
        invoices = [
            self._book_purchase(
                vendor=vendor,
                issue_date=PERIOD_START + timedelta(days=offset),
                subtotal=round(self.rng.uniform(20_000.0, 45_000.0), 2),
                concepto_text="Papeleria y consumibles de oficina",
                approver=self._employee_at(1)["name"],
                cost_center="CC-200 Administracion",
                pay_after_days=self.rng.randint(14, 28),
            )[0]
            for offset in (55, 140)
        ]

        return {
            "entity": f"RFC:{vendor['rfc']}",
            "signal": "vendor_employee_shared_bank_prefix",
            "why_innocent": (
                f"The vendor and employee {employee['emp_id']} only share the "
                "three-digit bank code; their full 18-digit CLABEs are "
                "different accounts, and no transfer between them was found "
                "in the supplied estate."
            ),
            "invoices": [invoice["uuid"] for invoice in invoices],
        }

    def _decoy_refund_two_cycle(self) -> dict[str, Any]:
        vendor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=500),
            category="Insumos",
        )
        issue_date = PERIOD_START + timedelta(days=self.rng.randint(60, 110))
        invoice, payment = self._book_purchase(
            vendor=vendor,
            issue_date=issue_date,
            subtotal=round(self.rng.uniform(120_000.0, 180_000.0), 2),
            concepto_text="Material de limpieza para planta",
            approver=self._employee_at(1)["name"],
            cost_center="CC-400 Mantenimiento",
            pay_after_days=11,
        )
        assert payment is not None
        purchase_order = self._add_purchase_order(
            vendor_rfc=vendor["rfc"],
            po_date=issue_date - timedelta(days=6),
            amount=invoice["total"],
            requester=self._employee_at(5)["name"],
            approver=self._employee_at(1)["name"],
            description="Material de limpieza para planta",
        )
        overpayment = round(invoice["total"] * 0.10, 2)
        extra = self._add_bank_txn(
            txn_date=date.fromisoformat(payment["date"]),
            from_clabe=COMPANY_CLABE,
            to_clabe=vendor["bank_clabe"],
            amount=overpayment,
            reference=f"Pago adicional factura {invoice['uuid']}",
        )
        refund = self._add_bank_txn(
            txn_date=date.fromisoformat(extra["date"]) + timedelta(days=4),
            from_clabe=vendor["bank_clabe"],
            to_clabe=COMPANY_CLABE,
            amount=overpayment,
            reference=f"Devolucion de sobrepago factura {invoice['uuid']}",
        )

        return {
            "entity": f"RFC:{vendor['rfc']}",
            "signal": "directed_bank_transfer_cycle",
            "why_innocent": (
                f"Transfer {refund['txn_id']} returns to the company the exact "
                f"overpayment sent in {extra['txn_id']}, which exceeded "
                f"purchase order {purchase_order['po_id']} and invoice "
                f"{invoice['uuid']}."
            ),
            "invoices": [invoice["uuid"]],
        }

    def _decoy_framework_contract_cluster(self) -> dict[str, Any]:
        vendor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=1100),
            category="Limpieza",
        )
        monthly_fee = 38_000.00
        contract = self._add_contract(
            vendor_rfc=vendor["rfc"],
            start_date=PERIOD_START - timedelta(days=365),
            value=round(monthly_fee * 1.16 * 48, 2),
            scope_text=(
                "Contrato marco de limpieza: cuota mensual fija de "
                f"MXN {monthly_fee:,.2f} por cada uno de los cuatro sitios."
            ),
        )
        first_date = PERIOD_START + timedelta(days=self.rng.randint(200, 240))
        invoices = [
            self._book_purchase(
                vendor=vendor,
                issue_date=first_date + timedelta(days=offset),
                subtotal=monthly_fee,
                concepto_text=f"Servicio de limpieza mensual sitio {site}",
                approver=self._employee_at(1)["name"],
                cost_center="CC-400 Mantenimiento",
                pay_after_days=self.rng.randint(12, 22),
            )[0]
            for site, offset in ((1, 0), (2, 1), (3, 2), (4, 4))
        ]

        return {
            "entity": f"RFC:{vendor['rfc']}",
            "signal": "short_window_similar_invoice_cluster",
            "why_innocent": (
                f"Contract {contract['contract_id']} fixes one identical "
                "monthly fee per site, so the four same-week invoices are the "
                "contracted fee for the four sites rather than a split "
                "purchase."
            ),
            "invoices": [invoice["uuid"] for invoice in invoices],
        }

    def _decoy_cancelled_and_reissued(self) -> dict[str, Any]:
        customer_rfc = self._new_rfc()
        customer_clabe = self._new_clabe()
        approver = self._employee_at(0)
        subtotal = round(self.rng.uniform(300_000.0, 420_000.0), 2)

        cancelled = self._book_revenue(
            customer_rfc=customer_rfc,
            issue_date=PERIOD_CLOSE - timedelta(days=2),
            subtotal=subtotal,
            concepto_text="Venta de producto terminado",
            approver=approver["name"],
            status="cancelado",
        )
        self._add_ledger_pair(
            entry_date=PERIOD_CLOSE - timedelta(days=1),
            debit_account=_REVENUE_ACCOUNT,
            credit_account=_RECEIVABLE_ACCOUNT,
            value=cancelled["total"],
            description=f"Cancelacion factura {cancelled['uuid']}",
            invoice_uuid=cancelled["uuid"],
            cost_center="CC-300 Ventas",
            approver=approver["name"],
        )
        reissued = self._book_revenue(
            customer_rfc=customer_rfc,
            issue_date=PERIOD_CLOSE - timedelta(days=1),
            subtotal=subtotal,
            concepto_text="Venta de producto terminado (refacturacion)",
            approver=approver["name"],
        )
        receipt = self._add_bank_txn(
            txn_date=PERIOD_CLOSE + timedelta(days=18),
            from_clabe=customer_clabe,
            to_clabe=COMPANY_CLABE,
            amount=reissued["total"],
            reference=f"Cobro factura {reissued['uuid']}",
        )

        return {
            "entity": f"RFC:{COMPANY_RFC}",
            "signal": "period_end_cancelled_revenue_invoice",
            "why_innocent": (
                f"Invoice {cancelled['uuid']} was cancelled and reissued the "
                f"next day as {reissued['uuid']} for the same total, the "
                "ledger reverses the cancelled entry, and receipt "
                f"{receipt['txn_id']} shows the reissued invoice was "
                "collected."
            ),
            "invoices": [cancelled["uuid"], reissued["uuid"]],
        }

    def _decoy_group_shared_treasury_account(self) -> dict[str, Any]:
        shared_clabe = self._new_clabe()
        parent = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=1500),
            category="Tecnologia",
            bank_clabe=shared_clabe,
        )
        subsidiary = self._add_vendor(
            rfc=self._new_rfc(),
            legal_name=self._new_legal_name(),
            registered_date=PERIOD_START - timedelta(days=800),
            bank_clabe=shared_clabe,
            category="Tecnologia",
        )
        parent_contract = self._add_contract(
            vendor_rfc=parent["rfc"],
            start_date=PERIOD_START - timedelta(days=400),
            value=900_000.00,
            scope_text=(
                "Contrato de licenciamiento; la tesoreria centralizada del "
                f"grupo cobra por la cuenta compartida con {subsidiary['legal_name']}."
            ),
        )
        subsidiary_contract = self._add_contract(
            vendor_rfc=subsidiary["rfc"],
            start_date=PERIOD_START - timedelta(days=380),
            value=450_000.00,
            scope_text=(
                "Contrato de soporte; la cobranza se concentra en la cuenta de "
                f"tesoreria del grupo encabezado por {parent['legal_name']}."
            ),
        )
        invoices = [
            self._book_purchase(
                vendor=vendor,
                issue_date=PERIOD_START + timedelta(days=offset),
                subtotal=round(self.rng.uniform(70_000.0, 210_000.0), 2),
                concepto_text="Licenciamiento de software administrativo",
                approver=self._employee_at(1)["name"],
                cost_center="CC-500 Sistemas",
                pay_after_days=self.rng.randint(15, 30),
            )[0]
            for vendor, offset in ((parent, 65), (subsidiary, 150))
        ]

        return {
            "entity": f"RFC:{parent['rfc']}",
            "signal": "shared_vendor_clabe",
            "why_innocent": (
                f"Contracts {parent_contract['contract_id']} and "
                f"{subsidiary_contract['contract_id']} state that this vendor "
                f"and {subsidiary['rfc']} belong to one group that collects "
                "through a single centralised treasury account."
            ),
            "invoices": [invoice["uuid"] for invoice in invoices],
        }

    def _decoy_employee_expense_reimbursement(self) -> dict[str, Any]:
        vendor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=650),
            category="Capacitacion",
        )
        employee = self._employee_at(6)
        issue_date = PERIOD_START + timedelta(days=self.rng.randint(120, 170))
        invoice, payment = self._book_purchase(
            vendor=vendor,
            issue_date=issue_date,
            subtotal=round(self.rng.uniform(60_000.0, 95_000.0), 2),
            concepto_text="Capacitacion tecnica para operadores",
            approver=self._employee_at(1)["name"],
            cost_center="CC-200 Administracion",
            pay_after_days=14,
        )
        assert payment is not None
        advance = round(invoice["total"] * 0.15, 2)
        purchase_order = self._add_purchase_order(
            vendor_rfc=vendor["rfc"],
            po_date=issue_date - timedelta(days=9),
            amount=invoice["total"],
            requester=employee["name"],
            approver=self._employee_at(1)["name"],
            description=(
                f"Capacitacion tecnica; el empleado {employee['emp_id']} cubrio "
                f"el anticipo de MXN {advance:,.2f} y el proveedor lo reembolsa."
            ),
        )
        reimbursement = self._add_bank_txn(
            txn_date=date.fromisoformat(payment["date"]) + timedelta(days=6),
            from_clabe=vendor["bank_clabe"],
            to_clabe=employee["bank_clabe"],
            amount=advance,
            reference=f"Reembolso anticipo {purchase_order['po_id']}",
        )

        return {
            "entity": f"RFC:{vendor['rfc']}",
            "signal": "vendor_to_employee_bank_transfer",
            "why_innocent": (
                f"Purchase order {purchase_order['po_id']} records that "
                f"employee {employee['emp_id']} paid the advance personally, "
                f"and transfer {reimbursement['txn_id']} returns exactly that "
                "advance to them."
            ),
            "invoices": [invoice["uuid"]],
        }

    def _decoy_volume_rebate_cycle(self) -> dict[str, Any]:
        distributor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=1300),
            category="Insumos",
        )
        subcontractor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=1000),
            category="Logistica",
        )
        issue_date = PERIOD_START + timedelta(days=self.rng.randint(80, 120))
        invoice, payment = self._book_purchase(
            vendor=distributor,
            issue_date=issue_date,
            subtotal=round(self.rng.uniform(500_000.0, 700_000.0), 2),
            concepto_text="Suministro anual de insumos",
            approver=self._employee_at(1)["name"],
            cost_center="CC-100 Produccion",
            pay_after_days=12,
        )
        assert payment is not None
        contract = self._add_contract(
            vendor_rfc=distributor["rfc"],
            start_date=PERIOD_START - timedelta(days=200),
            value=invoice["total"],
            scope_text=(
                "Contrato de suministro, clausula 9: bonificacion por volumen "
                "del 4% pagadera al cliente al cierre del periodo; la entrega "
                f"se subcontrata con {subcontractor['legal_name']}."
            ),
        )
        freight = self._add_bank_txn(
            txn_date=date.fromisoformat(payment["date"]) + timedelta(days=3),
            from_clabe=distributor["bank_clabe"],
            to_clabe=subcontractor["bank_clabe"],
            amount=round(invoice["total"] * 0.18, 2),
            reference="Pago de flete subcontratado",
        )
        rebate = self._add_bank_txn(
            txn_date=date.fromisoformat(freight["date"]) + timedelta(days=9),
            from_clabe=subcontractor["bank_clabe"],
            to_clabe=COMPANY_CLABE,
            amount=round(invoice["total"] * 0.04, 2),
            reference=f"Bonificacion por volumen clausula 9 {contract['contract_id']}",
        )

        return {
            "entity": f"RFC:{distributor['rfc']}",
            "signal": "directed_bank_transfer_cycle",
            "why_innocent": (
                f"Clause 9 of contract {contract['contract_id']} provides the "
                f"4% volume rebate paid back in {rebate['txn_id']}, and "
                f"{freight['txn_id']} is the subcontracted freight the same "
                "contract names."
            ),
            "invoices": [invoice["uuid"]],
        }

    def _decoy_efos_vendor_without_activity(self) -> dict[str, Any]:
        vendor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=1600),
            category="Seguridad",
        )
        self._add_efos(
            rfc=vendor["rfc"],
            legal_name=vendor["legal_name"],
            status="definitivo",
            publication_date=PERIOD_START + timedelta(days=150),
        )

        return {
            "entity": f"RFC:{vendor['rfc']}",
            "signal": "vendor_efos_record_match",
            "why_innocent": (
                "No invoice, purchase order, contract or bank transfer for "
                "this vendor was found in the supplied estate, so the estate "
                "records no exposure to it."
            ),
            "invoices": [],
        }

    def _decoy_individually_approved_cluster(self) -> dict[str, Any]:
        vendor = self._new_vendor(
            registered_date=PERIOD_START - timedelta(days=950),
            category="Ingenieria",
        )
        director = self._employee_at(0)
        requester = self._employee_at(5)
        first_date = PERIOD_START + timedelta(days=self.rng.randint(250, 290))
        scopes = (
            "Ingenieria de detalle nave 1",
            "Ingenieria de detalle nave 2",
            "Ingenieria de detalle subestacion",
        )

        invoices: list[dict[str, Any]] = []
        purchase_orders: list[dict[str, Any]] = []
        for index, scope in enumerate(scopes):
            issue_date = first_date + timedelta(days=index * 2)
            invoice, _payment = self._book_purchase(
                vendor=vendor,
                issue_date=issue_date,
                subtotal=240_000.00 + index * 1_500.00,
                concepto_text=scope,
                approver=director["name"],
                cost_center="CC-500 Sistemas",
                pay_after_days=self.rng.randint(16, 26),
            )
            purchase_orders.append(
                self._add_purchase_order(
                    vendor_rfc=vendor["rfc"],
                    po_date=issue_date - timedelta(days=15 - index),
                    amount=invoice["total"],
                    requester=requester["name"],
                    approver=director["name"],
                    description=scope,
                )
            )
            invoices.append(invoice)

        po_ids = ", ".join(
            purchase_order["po_id"] for purchase_order in purchase_orders
        )
        return {
            "entity": f"RFC:{vendor['rfc']}",
            "signal": "short_window_similar_invoice_cluster",
            "why_innocent": (
                f"Purchase orders {po_ids} cover three distinct scopes and each "
                "one already exceeds the internal approval limit of MXN "
                f"{INTERNAL_APPROVAL_THRESHOLD_MXN:,.2f}, so splitting them "
                "would not have avoided any approval."
            ),
            "invoices": [invoice["uuid"] for invoice in invoices],
        }

    # ------------------------------------------------------------------
    # output
    # ------------------------------------------------------------------

    def _rows(self) -> list[tuple[str, list[dict[str, Any]]]]:
        return [
            ("vendors", self.vendors),
            ("invoices", self.invoices),
            ("ledger", self.ledger),
            ("bank_txns", self.bank_txns),
            ("purchase_orders", self.purchase_orders),
            ("contracts", self.contracts),
            ("employees", self.employees),
            ("efos_list", self.efos_list),
        ]

    def write(self, db_path: str | Path) -> Path:
        path = Path(db_path)
        if path.parent != Path(""):
            path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

        conn = sqlite3.connect(path)
        try:
            conn.executescript(ESTATE_DDL)
            for table, rows in self._rows():
                if not rows:
                    continue
                columns = TABLE_COLUMNS[table]
                placeholders = ", ".join("?" for _ in columns)
                conn.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({placeholders})",
                    [tuple(row[column] for column in columns) for row in rows],
                )
            conn.commit()
        finally:
            conn.close()

        return path

    def answer_key(self) -> dict[str, Any]:
        return {
            "seed": self.spec.seed,
            "company_rfc": COMPANY_RFC,
            "schemes": self.schemes,
            "decoys": self.decoys,
        }
