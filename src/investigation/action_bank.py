from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field

from src.core.estate import EstateRepository

from src.core.models import EvidenceRef

class ActionDefinition(BaseModel):
    """
    Describe una herramienta disponible para el Investigator.

    No ejecuta nada por sí misma.
    Es el catálogo que el agente puede consultar.
    """

    name: str
    purpose: str

    required_arguments: list[str] = Field(
        default_factory=list
    )

    returns: str

    does_not_prove: list[str] = Field(
        default_factory=list
    )

    legitimate_exceptions: list[str] = Field(
        default_factory=list
    )

    cost: str = "low"


class ActionResult(BaseModel):
    """
    Resultado estructurado de una herramienta.

    data:
        información que utilizará la investigación.

    evidence_refs:
        registros exactos del estate que respaldan
        ese resultado.
    """

    action_name: str

    success: bool

    data: Any = None

    evidence_refs: list[EvidenceRef] = Field(
        default_factory=list
    )

    summary: str = ""

    errors: list[str] = Field(
        default_factory=list
    )

# ============================================================
# ACCIONES REALES SOBRE EL ESTATE
# ============================================================

def action_get_vendor(
    estate: EstateRepository,
    vendor_rfc: str,
) -> ActionResult:

    vendor = estate.get_vendor(vendor_rfc)

    if vendor is None:
        return ActionResult(
            action_name="get_vendor",
            success=False,
            summary=f"No se encontró vendor {vendor_rfc}.",
        )

    return ActionResult(
        action_name="get_vendor",
        success=True,
        data=vendor,
        evidence_refs=[
            EvidenceRef(
                source_table="vendors",
                record_id=str(vendor["rfc"]),
            )
        ],
        summary=f"Se recuperó vendor {vendor_rfc}.",
    )

def action_get_employee(
    estate: EstateRepository,
    emp_id: str,
) -> ActionResult:

    employee = estate.get_employee(emp_id)

    if employee is None:
        return ActionResult(
            action_name="get_employee",
            success=False,
            summary=f"No se encontró employee {emp_id}.",
        )

    return ActionResult(
        action_name="get_employee",
        success=True,
        data=employee,
        evidence_refs=[
            EvidenceRef(
                source_table="employees",
                record_id=str(employee["emp_id"]),
            )
        ],
        summary=f"Se recuperó employee {emp_id}.",
    )

def action_get_vendor_contracts(
    estate: EstateRepository,
    vendor_rfc: str,
) -> ActionResult:

    contracts = estate.get_vendor_contracts(
        vendor_rfc
    )

    return ActionResult(
    action_name="get_vendor_contracts",
    success=True,
    data=contracts,
    evidence_refs=[
        EvidenceRef(
            source_table="contracts",
            record_id=str(contract["contract_id"]),
        )
        for contract in contracts
    ],
    summary=(
        f"Se encontraron {len(contracts)} "
        f"contrato(s) para {vendor_rfc}."
    ),
)


def action_get_vendor_purchase_orders(
    estate: EstateRepository,
    vendor_rfc: str,
) -> ActionResult:

    orders = estate.get_vendor_purchase_orders(
        vendor_rfc
    )

    return ActionResult(
    action_name="get_vendor_purchase_orders",
    success=True,
    data=orders,
    evidence_refs=[
        EvidenceRef(
            source_table="purchase_orders",
            record_id=str(order["po_id"]),
        )
        for order in orders
    ],
    summary=(
        f"Se encontraron {len(orders)} "
        f"PO(s) para {vendor_rfc}."
    ),
)



def action_get_vendor_invoices(
    estate: EstateRepository,
    vendor_rfc: str,
) -> ActionResult:

    invoices = estate.get_vendor_invoices(
        vendor_rfc
    )

    return ActionResult(
    action_name="get_vendor_invoices",
    success=True,
    data=invoices,
    evidence_refs=[
        EvidenceRef(
            source_table="invoices",
            record_id=str(invoice["uuid"]),
        )
        for invoice in invoices
    ],
    summary=(
        f"Se encontraron {len(invoices)} "
        f"factura(s) para {vendor_rfc}."
    ),
)


def action_get_bank_transactions_for_clabe(
    estate: EstateRepository,
    clabe: str,
) -> ActionResult:

    transactions = (
        estate.get_bank_transactions_for_clabe(
            clabe
        )
    )

    return ActionResult(
    action_name="get_bank_transactions_for_clabe",
    success=True,
    data=transactions,
    evidence_refs=[
        EvidenceRef(
            source_table="bank_txns",
            record_id=str(txn["txn_id"]),
        )
        for txn in transactions
    ],
    summary=(
        f"Se encontraron {len(transactions)} "
        "movimiento(s) para la CLABE."
    ),
)


def action_check_efos(
    estate: EstateRepository,
    vendor_rfc: str,
) -> ActionResult:

    record = estate.get_efos_record(
        vendor_rfc
    )

    if record is None:
        return ActionResult(
            action_name="check_efos",
            success=True,
            data=None,
            summary=(
                f"No se encontró registro EFOS "
                f"para {vendor_rfc}."
            ),
        )

    return ActionResult(
    action_name="check_efos",
    success=True,
    data=record,
    evidence_refs=[
        EvidenceRef(
            source_table="efos_list",
            record_id=str(record["rfc"]),
        )
    ],
    summary=(
        f"Se encontró registro EFOS para "
        f"{vendor_rfc} con status "
        f"{record.get('status')}."
    ),
)


# ============================================================
# CATÁLOGO
# ============================================================

ACTION_DEFINITIONS = {

    "get_vendor": ActionDefinition(
        name="get_vendor",
        purpose="Consultar información de un proveedor.",
        required_arguments=["vendor_rfc"],
        returns="vendor record",
        does_not_prove=[
            "que el proveedor sea legítimo",
            "que el proveedor sea fraudulento",
        ],
    ),

    "get_employee": ActionDefinition(
        name="get_employee",
        purpose="Consultar información de un empleado.",
        required_arguments=["emp_id"],
        returns="employee record",
        does_not_prove=[
            "que exista una relación indebida con un proveedor",
        ],
    ),

    "get_vendor_contracts": ActionDefinition(
        name="get_vendor_contracts",
        purpose=(
            "Buscar soporte contractual para "
            "operaciones del proveedor."
        ),
        required_arguments=["vendor_rfc"],
        returns="list of contracts",
        does_not_prove=[
            (
                "que una operación sea legítima "
                "sólo porque exista un contrato"
            )
        ],
        legitimate_exceptions=[
            (
                "una operación puede ser legítima aunque "
                "el estate no contenga un contrato"
            )
        ],
    ),

    "get_vendor_purchase_orders": ActionDefinition(
        name="get_vendor_purchase_orders",
        purpose="Consultar las órdenes de compra del proveedor.",
        required_arguments=["vendor_rfc"],
        returns="list of purchase orders",
        does_not_prove=[
            "threshold splitting",
            "kickback",
        ],
    ),

    "get_vendor_invoices": ActionDefinition(
        name="get_vendor_invoices",
        purpose="Consultar las facturas de un proveedor.",
        required_arguments=["vendor_rfc"],
        returns="list of invoices",
        does_not_prove=[
            "que los servicios facturados hayan ocurrido",
            "que una factura sea fraudulenta",
        ],
    ),

    "get_bank_transactions_for_clabe": ActionDefinition(
        name="get_bank_transactions_for_clabe",
        purpose=(
            "Observar los movimientos bancarios "
            "de una CLABE."
        ),
        required_arguments=["clabe"],
        returns="list of bank transactions",
        does_not_prove=[
            (
                "la naturaleza económica o jurídica "
                "de una transferencia"
            )
        ],
    ),

    "check_efos": ActionDefinition(
        name="check_efos",
        purpose=(
            "Consultar la información del RFC "
            "presente en efos_list."
        ),
        required_arguments=["vendor_rfc"],
        returns="EFOS record or None",
        does_not_prove=[
            "que una operación concreta sea simulada",
            "que toda factura del proveedor sea fraudulenta",
        ],
    ),
}


ACTION_FUNCTIONS: dict[
    str,
    Callable[..., ActionResult],
] = {

    "get_vendor":
        action_get_vendor,

    "get_employee":
        action_get_employee,

    "get_vendor_contracts":
        action_get_vendor_contracts,

    "get_vendor_purchase_orders":
        action_get_vendor_purchase_orders,

    "get_vendor_invoices":
        action_get_vendor_invoices,

    "get_bank_transactions_for_clabe":
        action_get_bank_transactions_for_clabe,

    "check_efos":
        action_check_efos,
}


def list_available_actions(
) -> list[ActionDefinition]:

    return list(
        ACTION_DEFINITIONS.values()
    )


def execute_action(
    action_name: str,
    estate: EstateRepository,
    arguments: dict[str, Any],
) -> ActionResult:
    """
    Punto único para ejecutar acciones.

    El LLM nunca llama funciones arbitrarias.

    Solicita una acción por nombre y Python
    decide si existe y la ejecuta.
    """

    function = ACTION_FUNCTIONS.get(
        action_name
    )

    if function is None:
        return ActionResult(
            action_name=action_name,
            success=False,
            errors=[
                "La acción no existe en el Action Bank."
            ],
        )

    try:
        return function(
            estate=estate,
            **arguments,
        )

    except TypeError as error:
        return ActionResult(
            action_name=action_name,
            success=False,
            errors=[
                f"Argumentos inválidos: {error}"
            ],
        )

    except Exception as error:
        return ActionResult(
            action_name=action_name,
            success=False,
            errors=[
                str(error)
            ],
        )