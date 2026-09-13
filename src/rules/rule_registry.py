from __future__ import annotations
from pydantic import BaseModel, Field

class RuleDefinition(BaseModel):
    rule_id: str
    title: str
    source: str
    source_reference: str
    applies_to: list[str] = Field(default_factory=list)
    supports: list[str] = Field(default_factory=list)
    does_not_prove: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)

class RuleRegistry:
    def __init__(self):
        self._rules: dict[str, RuleDefinition] = {}

    def register(self, rule: RuleDefinition) -> None:
        self._rules[rule.rule_id] = rule

    def get_rule(self, rule_id: str) -> RuleDefinition | None:
        return self._rules.get(rule_id)

    def is_valid(self, rule_id: str) -> bool:
        return rule_id in self._rules

    def list_rules(self) -> list[RuleDefinition]:
        return list(self._rules.values())
