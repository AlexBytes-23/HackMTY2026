from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceRef(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        frozen=True,
    )

    file: str
    row: int | None = None
    xpath: str | None = None


class GraphPattern(BaseModel):
    pattern_type: str

    entity_ids: list[str]
    transaction_ids: list[str]

    transaction_amounts: dict[str, float] = Field(default_factory=dict)

    total_amount: float | None = None

    start_time: datetime | None = None
    end_time: datetime | None = None

    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    description: str


class GraphFeatures(BaseModel):
    entity_id: str

    incoming_transaction_count: int
    outgoing_transaction_count: int

    unique_senders: int
    unique_receivers: int
    unique_counterparties: int

    incoming_amount_total: float
    outgoing_amount_total: float

    average_incoming_amount: float
    average_outgoing_amount: float

    net_flow: float

    self_transaction_count: int

    cycle_count: int
    temporal_cycle_count: int

    fan_in_degree: int
    fan_out_degree: int


class GraphInput(BaseModel):
    case_id: str
    target_entity_id: str | None = None

    entities: list[dict[str, Any]]
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    transactions: list[dict[str, Any]]


class GraphResult(BaseModel):
    case_id: str

    patterns: list[GraphPattern] = Field(default_factory=list)

    suspicious_entities: list[str] = Field(default_factory=list)

    graph_features: dict[
        str,
        GraphFeatures,
    ] = Field(default_factory=dict)

    limitations: list[str] = Field(default_factory=list)

    gnn_score: float | None = None

    gnn_model_info: dict[str, Any] | None = None
