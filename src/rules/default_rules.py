"""Default formal rule registry for the audited pipeline.

The Evidence Gate and the FindingBuilder refuse to authorize a finding unless the
requested ``rule_id`` resolves to a rule that explicitly ``applies_to`` the
hypothesis' ``scheme_type``.  Until this module existed the production registry
was empty, so no case could ever be authorized.

Every rule here must be a real, citable norm.  Nothing in this file may invent a
statute, a threshold, or an internal policy: a rule that cannot be cited does not
belong in the registry, and a scheme without a registered rule simply cannot
produce a finding.

``does_not_prove`` is as load-bearing as ``supports``.  It is what keeps a cited
article from being read as proof of a specific fraudulent transaction.
"""

from __future__ import annotations

from src.rules.rule_registry import RuleDefinition, RuleRegistry


RULE_CFF_69B = RuleDefinition(
    rule_id="CFF-69B",
    title="Operaciones inexistentes (EFOS)",
    source="SAT",
    source_reference="Articulo 69-B",
    applies_to=["phantom_vendor"],
    supports=[
        (
            "El SAT publica en el listado del articulo 69-B del Codigo Fiscal de "
            "la Federacion a los contribuyentes respecto de los cuales presume "
            "que emitieron comprobantes sin contar con activos, personal, "
            "infraestructura o capacidad material para prestar los servicios o "
            "producir los bienes facturados."
        ),
        (
            "La presencia del RFC emisor de un comprobante en ese listado, junto "
            "con comprobantes efectivamente emitidos a nombre de la entidad "
            "auditada, es el nucleo probatorio documental de un esquema de "
            "proveedor fantasma."
        ),
    ],
    does_not_prove=[
        (
            "No prueba que una operacion concreta amparada por un comprobante "
            "especifico sea inexistente; la presuncion es sobre el emisor, no "
            "sobre cada transaccion."
        ),
        (
            "No prueba intencion, dolo ni responsabilidad penal de ninguna "
            "persona fisica."
        ),
        (
            "No prueba que el receptor de los comprobantes conociera la "
            "situacion del emisor."
        ),
        (
            "No acredita por si misma el monto del perjuicio: el monto debe "
            "reconciliar contra los registros citados."
        ),
    ],
    requirements=[
        (
            "El RFC emisor de al menos un comprobante citado debe coincidir "
            "exactamente con un RFC presente en la tabla efos_list suministrada "
            "con el estate."
        ),
        (
            "Cada afirmacion debe citar registros existentes del estate "
            "(source_table + record_id)."
        ),
        (
            "El monto reclamado debe reconciliar deterministicamente contra los "
            "registros citados."
        ),
    ],
    exceptions=[
        (
            "La tabla efos_list del estate solo admite dos estatus: 'definitivo' "
            "y 'presunto'. Un registro con estatus 'presunto' documenta una "
            "presuncion que el contribuyente aun puede desvirtuar en los terminos "
            "del propio articulo 69-B, y por si solo no sostiene una imputacion "
            "definitiva; unicamente el estatus 'definitivo' la sostiene."
        ),
        (
            "La presuncion opera a partir de la publicacion definitiva; "
            "comprobantes emitidos en un periodo ajeno al listado requieren "
            "analisis temporal especifico."
        ),
        (
            "El receptor de los comprobantes puede acreditar la materialidad de "
            "la operacion en los terminos del propio articulo."
        ),
    ],
)


RULE_SOD_PROCUREMENT = RuleDefinition(
    rule_id="SOD-PROC-01",
    title="Segregacion de funciones en compras",
    source="Control interno de la empresa",
    source_reference="Politica de compras",
    applies_to=["kickback"],
    supports=[
        (
            "Quien autoriza una orden de compra no puede tener un interes economico "
            "en el proveedor autorizado. Un flujo de fondos desde la cuenta del "
            "proveedor hacia la cuenta personal de quien aprueba sus ordenes de "
            "compra es la forma documental de ese conflicto."
        ),
    ],
    does_not_prove=[
        (
            "No prueba que el pago haya sido la causa de la autorizacion; establece "
            "una concurrencia documental, no una relacion causal."
        ),
        (
            "No prueba intencion, dolo ni responsabilidad penal de ninguna persona "
            "fisica."
        ),
        (
            "No prueba que los bienes o servicios facturados no se hayan prestado."
        ),
        (
            "No es una norma legal: es una politica de control interno de la propia "
            "empresa y se cita como tal."
        ),
    ],
    requirements=[
        (
            "Una transferencia citada debe correr desde la cuenta del proveedor hacia "
            "la del empleado, entre cuentas DISTINTAS."
        ),
        (
            "Ese mismo empleado debe figurar como approver en al menos una orden de "
            "compra citada de ese proveedor."
        ),
    ],
    exceptions=[
        (
            "Un proveedor persona fisica con actividad empresarial que ES el empleado "
            "comparte cuenta legitimamente; en ese caso no hay dos partes y no hay "
            "transferencia entre cuentas distintas."
        ),
        (
            "Un reembolso de gastos documentado del proveedor al empleado no "
            "establece el conflicto si ese empleado no aprueba sus ordenes de compra."
        ),
    ],
)


DEFAULT_RULES: tuple[RuleDefinition, ...] = (RULE_CFF_69B, RULE_SOD_PROCUREMENT)


def build_default_registry() -> RuleRegistry:
    """Return a registry populated with the rules this pipeline can actually cite.

    Deterministic: the same rules in the same order on every run.
    """

    registry = RuleRegistry()
    for rule in DEFAULT_RULES:
        registry.register(rule)
    return registry


def default_rule_id_for_scheme(scheme_type: str) -> str | None:
    """Return the single registered rule_id for ``scheme_type``, if exactly one exists.

    Returns ``None`` when no rule applies, and also when more than one does: an
    ambiguous rule selection is a decision for the caller to make explicitly, not
    something to resolve silently here.
    """

    matches = [
        rule.rule_id
        for rule in DEFAULT_RULES
        if scheme_type in rule.applies_to
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def rule_context_for_scheme(scheme_type: str) -> list[dict]:
    """Serialize the applicable rules as Method Critic ``rule_context``.

    Supplying this context is what lets the Method Critic evaluate rule misuse at
    all.  It is *not* a rule selection: ``requested_rule_id`` stays explicit.
    """

    return [
        rule.model_dump(mode="json")
        for rule in DEFAULT_RULES
        if scheme_type in rule.applies_to
    ]
