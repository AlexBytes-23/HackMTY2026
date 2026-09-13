from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SchemeType = Literal[
    "phantom_vendor",
    "kickback",
    "round_tripping",
    "threshold_splitting",
    "revenue_inflation",
]

SourceTable = Literal[
    "ledger",
    "invoices",
    "bank_txns",
    "vendors",
    "efos_list",
    "purchase_orders",
    "contracts",
    "employees",
]


class EvidenceRef(BaseModel):
    source_table: SourceTable
    record_id: str
    note: str = ""


class Observation(BaseModel):
    """
    Algo objetivo que encontró un detector.
    No equivale a fraude.
    """

    observation_id: str
    detector_name: str
    signal_type: str

    entities: list[str] = Field(default_factory=list)

    statement: str

    evidence: list[EvidenceRef] = Field(default_factory=list)

    facts: dict[str, Any] = Field(default_factory=dict)

    score: float | None = None
    score_semantics: str | None = None

    limitations: list[str] = Field(default_factory=list)

    legitimate_alternatives: list[str] = Field(default_factory=list)

    recommended_checks: list[str] = Field(default_factory=list)


class Lead(BaseModel):
    """
    Algo que merece investigación.

    Todavía NO obligamos a clasificarlo
    como uno de los cinco fraudes.
    """

    lead_id: str

    subject_entities: list[str] = Field(default_factory=list)

    observation_ids: list[str] = Field(default_factory=list)

    reason_opened: str

    open_questions: list[str] = Field(default_factory=list)

    status: Literal[
        "open",
        "investigating",
        "closed",
    ] = "open"


class Hypothesis(BaseModel):
    hypothesis_id: str

    statement: str

    scheme_type: SchemeType | None = None

    status: Literal[
        "open",
        "supported",
        "rejected",
        "inconclusive",
    ] = "open"

    supporting_evidence_ids: list[str] = Field(default_factory=list)

    counter_evidence_ids: list[str] = Field(default_factory=list)

    unresolved_questions: list[str] = Field(default_factory=list)


class CaseEvidence(BaseModel):
    evidence_id: str

    statement: str

    direction: Literal[
        "for",
        "against",
        "neutral",
    ]

    source_refs: list[EvidenceRef] = Field(default_factory=list)

    produced_by: str

    observation_id: str | None = None


class ActionRecord(BaseModel):
    """
    Registro auditable de una acción ejecutada.
    """

    step: int

    action_name: str

    arguments: dict[str, Any] = Field(
        default_factory=dict
    )

    reason: str

    question_resolved: str | None = None

    result_summary: str

    result_data: Any = None

    success: bool = True

    errors: list[str] = Field(
        default_factory=list
    )

    produced_evidence_ids: list[str] = Field(
        default_factory=list
    )

    duration_ms: float | None = None


class CaseState(BaseModel):
    """
    Memoria completa y auditable de una investigación.

    Conserva tanto el origen del caso como todo
    lo que se descubre después.
    """

    case_id: str
    lead_id: str

    # Por qué abrimos originalmente este caso.
    lead_reason: str = ""

    subject_entities: list[str] = Field(
        default_factory=list
    )

    # Observaciones originales de Alex/Daniel/etc.
    # No queremos perderlas al convertirlas en un Lead.
    observations: list[Observation] = Field(
        default_factory=list
    )

    hypotheses: list[Hypothesis] = Field(
        default_factory=list
    )

    evidence: list[CaseEvidence] = Field(
        default_factory=list
    )

    unknowns: list[str] = Field(
        default_factory=list
    )

    actions_taken: list[ActionRecord] = Field(
        default_factory=list
    )

    status: Literal[
        "open",
        "investigating",
        "ready_for_review",
        "ready_for_verification",
        "closed",
    ] = "open"

class ChallengerReview(BaseModel):
    strongest_legitimate_alternative: str | None = None

    unsupported_claims: list[str] = Field(default_factory=list)

    missing_counterevidence: list[str] = Field(default_factory=list)

    contradictions: list[str] = Field(default_factory=list)

    proposed_actions: list[str] = Field(default_factory=list)

    survives_challenge: bool


class MethodReview(BaseModel):
    problematic_assumptions: list[str] = Field(default_factory=list)

    dependent_evidence: list[str] = Field(default_factory=list)

    confirmation_bias_risks: list[str] = Field(default_factory=list)

    rule_misuse: list[str] = Field(default_factory=list)

    missing_alternative_methods: list[str] = Field(default_factory=list)

    recommendations: list[str] = Field(default_factory=list)


class VerifiedFact(BaseModel):
    fact_id: str

    statement: str

    verified: bool

    evidence: list[EvidenceRef] = Field(default_factory=list)

    calculation: str | None = None

    errors: list[str] = Field(default_factory=list)

# ============================================================
# DECISIÓN DEL INVESTIGATOR
# ============================================================

class ProposedAction(BaseModel):
    """
    Acción concreta que el Investigator quiere ejecutar.

    action_name debe corresponder a una acción existente
    en el Action Bank.
    """

    action_name: str

    arguments: dict[str, Any] = Field(
        default_factory=dict
    )

    reason: str

    question_resolved: str


class InvestigatorDecision(BaseModel):
    """
    Una iteración de razonamiento del Investigator.

    El Investigator NO devuelve una acusación final.

    Decide qué hacer a continuación.
    """

    decision: Literal[
        "investigate",
        "request_review",
        "ready_for_verification",
        "close_inconclusive",
    ]

    current_assessment: str

    hypotheses: list[Hypothesis] = Field(
        default_factory=list
    )

    next_action: ProposedAction | None = None

    new_unknowns: list[str] = Field(
        default_factory=list
    )

    resolved_unknowns: list[str] = Field(
    default_factory=list
    )

    reason: str

class InvestigationLoopResult(BaseModel):
    """
    Resultado completo de ejecutar varias vueltas
    del Investigator.

    No sólo devuelve el CaseState final:
    conserva también las decisiones que llevaron hasta él.
    """

    case_state: CaseState

    decisions: list[InvestigatorDecision] = Field(
        default_factory=list
    )

    iterations: int

    stop_reason: Literal[
        "request_review",
        "ready_for_verification",
        "close_inconclusive",
        "max_steps_reached",
        "repeated_action",
    ]