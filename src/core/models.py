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
    Guarda qué hizo el agente y por qué.
    """

    step: int

    action_name: str

    arguments: dict[str, Any] = Field(default_factory=dict)

    reason: str

    result_summary: str

    success: bool = True

    produced_evidence_ids: list[str] = Field(default_factory=list)


class CaseState(BaseModel):
    """
    La libreta completa del investigador.
    """

    case_id: str
    lead_id: str

    subject_entities: list[str] = Field(default_factory=list)

    hypotheses: list[Hypothesis] = Field(default_factory=list)

    evidence: list[CaseEvidence] = Field(default_factory=list)

    unknowns: list[str] = Field(default_factory=list)

    actions_taken: list[ActionRecord] = Field(default_factory=list)

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