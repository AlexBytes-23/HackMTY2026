from pydantic import BaseModel, Field
from typing import Literal

SchemeType = Literal[
    "phantom_vendor", 
    "kickback", 
    "round_tripping", 
    "threshold_splitting", 
    "revenue_inflation"
]

SourceTable = Literal[
    "ledger", "invoices", "bank_txns", "vendors", 
    "efos_list", "purchase_orders", "contracts", "employees"
]

Confidence = Literal["proven", "probable"]

ClosedBy = Literal["investigator", "challenger", "validator"]

class MoneyTrailStep(BaseModel):
    from_entity: str = Field(alias="from")
    to_entity: str = Field(alias="to")
    amount: float
    date: str
    exhibit_id: str

class SubmissionExhibit(BaseModel):
    exhibit_id: str
    source_table: SourceTable
    record_id: str
    note: str

class SubmissionFinding(BaseModel):
    scheme_type: SchemeType
    entities: list[str] = Field(min_length=1)
    narrative: str
    rule_broken: str
    peso_amount: float = Field(gt=0)
    money_trail: list[MoneyTrailStep] | None = None
    exhibits: list[SubmissionExhibit] = Field(min_length=3)
    confidence: Confidence

class LeadNotPursued(BaseModel):
    entity: str
    signal: str
    reason: str
    tool_calls_made: list[str] | None = None
    closed_by: ClosedBy | None = None

class RunMetadata(BaseModel):
    llm_calls: int
    mxn_cost: float
    wall_clock_seconds: float
    cost_by_role: dict[str, float] | None = None
    deterministic: bool | None = None

class Submission(BaseModel):
    seed: int
    findings: list[SubmissionFinding]
    leads_not_pursued: list[LeadNotPursued]
    run_metadata: RunMetadata
