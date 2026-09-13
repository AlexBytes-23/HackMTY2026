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
            "El propio articulo 69-B preve que el contribuyente desvirtue la "
            "presuncion; un registro con estatus de desvirtuado o con sentencia "
            "favorable no sostiene la imputacion."
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


DEFAULT_RULES: tuple[RuleDefinition, ...] = (RULE_CFF_69B,)


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
