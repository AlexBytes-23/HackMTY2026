from pydantic import BaseModel, Field, model_validator
from typing import Literal, Any

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
    from_entity: str = Field(alias="from", min_length=1)
    to_entity: str = Field(alias="to", min_length=1)
    amount: float
    date: str = Field(min_length=1)
    exhibit_id: str = Field(min_length=1)

class SubmissionExhibit(BaseModel):
    @model_validator(mode="after")
    def validate_exhibit(self) -> "SubmissionExhibit":
        if not self.note.strip():
            raise ValueError("note cannot be empty or whitespace only.")
        return self
    exhibit_id: str = Field(min_length=1)
    source_table: SourceTable
    record_id: str = Field(min_length=1)
    note: str = Field(min_length=1)

class SubmissionFinding(BaseModel):
    scheme_type: SchemeType
    entities: list[str] = Field(min_length=1)
    narrative: str = Field(min_length=1)
    rule_broken: str = Field(min_length=1)
    peso_amount: float = Field(gt=0)
    money_trail: list[MoneyTrailStep]
    exhibits: list[SubmissionExhibit] = Field(min_length=3)
    confidence: Confidence

    @model_validator(mode="after")
    def validate_finding(self) -> "SubmissionFinding":
        if not self.narrative.strip():
            raise ValueError("Narrative cannot be empty or whitespace only.")
        if not self.rule_broken.strip():
            raise ValueError("rule_broken cannot be empty or whitespace only.")

        for entity in self.entities:
            if not (entity.startswith("RFC:") or entity.startswith("EMP:")):
                raise ValueError(f"Entity '{entity}' must start with RFC: or EMP:")
            if entity.strip() in ("RFC:", "EMP:"):
                raise ValueError(f"Entity '{entity}' has empty payload.")

        words = self.narrative.split()
        if len(words) > 150:
            raise ValueError(f"Narrative exceeds 150 words (found {len(words)}).")

        exhibit_ids = [e.exhibit_id for e in self.exhibits]
        if len(set(exhibit_ids)) != len(exhibit_ids):
            raise ValueError("exhibit_id must be unique within a finding.")

        for step in self.money_trail:
            if step.exhibit_id not in exhibit_ids:
                raise ValueError(f"money_trail exhibit_id '{step.exhibit_id}' must belong to finding exhibits.")

        if len(self.money_trail) > 1:
            for i in range(len(self.money_trail) - 1):
                if self.money_trail[i].to_entity != self.money_trail[i+1].from_entity:
                    raise ValueError("Money trail continuity broken: previous.to != next.from")

        return self

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

def serialize_official_submission(sub: Submission) -> dict[str, Any]:
    return sub.model_dump(by_alias=True, exclude_none=True)
