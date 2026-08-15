"""DecisionAgent — deterministic business logic, no model call.

The counterpart to AITaskAgent. Where the AI Task turns unstructured content
into typed facts, the Decision node turns typed facts into business conclusions,
using rules a person wrote and can audit:

    IF  confidence < 0.80                 THEN human_review = true
    IF  intent = "complaint"              THEN human_review = true
    IF  production_stopped = true         THEN urgency = "critical"

Zero tokens, same answer every time, and every run records which conditions were
checked and what value each one saw — which is the difference between a business
process you can defend and a prompt you have to trust.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.nodes.base import NodeType
from app.nodes.registry import NodeRegistry
from app.runtime.rules import (
    OPERATORS_BY_TYPE,
    Rule,
    RuleTrace,
    evaluate_rules,
)


#: Rule-set presets for the Builder (§32). Each is ordinary configuration the
#: author can then edit — none of them is a distinct backend node type.
DECISION_PRESETS: list[dict[str, Any]] = [
    {
        "id": "confidence_gate",
        "label": "Confidence Gate",
        "summary": "Send low-confidence AI output to a human instead of acting on it.",
        "rules": [
            {
                "name": "Low confidence needs a human",
                "when": {
                    "operator": "and",
                    "conditions": [
                        {
                            "field": "outputs.<ai_step>.confidence",
                            "operator": "less_than",
                            "value": 0.8,
                        }
                    ],
                },
                "then": [
                    {"field": "human_review", "operation": "set", "value": True},
                    {
                        "field": "escalation_reason",
                        "operation": "set",
                        "value": "The AI step was not confident enough to act automatically.",
                    },
                ],
            }
        ],
    },
    {
        "id": "required_fields",
        "label": "Required Fields Check",
        "summary": "Flag requests that are missing information we need to proceed.",
        "rules": [
            {
                "name": "Missing information needs clarification",
                "when": {
                    "operator": "and",
                    "conditions": [
                        {
                            "field": "outputs.<ai_step>.result.missing_information",
                            "operator": "is_not_empty",
                        }
                    ],
                },
                "then": [
                    {
                        "field": "clarification_required",
                        "operation": "set",
                        "value": True,
                    }
                ],
            }
        ],
    },
    {
        "id": "priority_rules",
        "label": "Priority Rules",
        "summary": "Derive urgency from business facts rather than from tone.",
        "rules": [
            {
                "name": "Stopped production is critical",
                "when": {
                    "operator": "and",
                    "conditions": [
                        {
                            "field": "outputs.<ai_step>.result.production_stopped",
                            "operator": "is_true",
                        }
                    ],
                },
                "then": [
                    {"field": "urgency", "operation": "set", "value": "critical"}
                ],
            }
        ],
    },
    {
        "id": "custom",
        "label": "Custom Rules",
        "summary": "Start from an empty rule set.",
        "rules": [],
    },
]


class DecisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[Rule] = Field(
        default_factory=list,
        description="Business rules evaluated in order — the first matching rule's conclusions win.",
    )
    #: Values written before any rule runs. Two jobs: they document the
    #: node's full output contract (so preflight can authorise references to a
    #: field no rule happened to set on this run), and they give every field a
    #: safe baseline — `human_review: false` means a downstream gate reads false
    #: rather than a missing path when no escalation rule fired.
    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description="What each conclusion is before any rule runs — guarantees a downstream step always has a value to read, even when no rule fires.",
    )
    #: Fields the node guarantees to emit even when no rule and no default sets
    #: them. Listed explicitly rather than inferred so the contract is visible
    #: in the Builder's Outputs tab.
    declared_fields: list[str] = Field(
        default_factory=list,
        description="Extra output field names this step promises to produce, beyond what defaults/rules already declare.",
    )

    def output_field_names(self) -> set[str]:
        names = set(self.defaults) | set(self.declared_fields)
        for rule in self.rules:
            names.update(action.field for action in rule.then)
        return names


class DecisionInput(BaseModel):
    pass


class DecisionOutput(BaseModel):
    """Facts the rules established, plus the reasoning behind them."""

    decisions: dict[str, Any] = Field(default_factory=dict)
    matched_rules: list[str] = Field(default_factory=list)
    #: Full per-condition trace. This is what the Builder's "Explain the route"
    #: view and the Cockpit run trace render — the logic is shown, not asserted.
    explanation: list[RuleTrace] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)


@NodeRegistry.register
class DecisionAgent(NodeType):
    type_name = "DecisionAgent"
    description = (
        "Deterministic business rules: IF/THEN over typed fields, with nested "
        "AND/OR/NOT. No model call."
    )
    input_schema = DecisionInput
    output_schema = DecisionOutput
    config_schema = DecisionConfig

    family: ClassVar[str] = "core"
    execution_kind: ClassVar[str] = "deterministic"
    about: ClassVar[dict[str, Any]] = {
        "what": (
            "Evaluates business rules against upstream values and writes named "
            "conclusions such as human_review, urgency or clarification_required."
        ),
        "why": (
            "Business policy should be inspectable and repeatable. Rules cost no "
            "tokens, never drift, and record exactly why each conclusion held."
        ),
        "receives": "Typed fields from an AI Task, an Input node, or another Decision.",
        "produces": "decisions.<field> values, the matched rule list, and a condition-level trace.",
        "uses_ai": False,
        "external_action": False,
        "presets": DECISION_PRESETS,
        "operators": {key: list(value) for key, value in OPERATORS_BY_TYPE.items()},
    }

    @classmethod
    def required_services(cls, config: dict[str, Any]) -> set[str]:
        return set()

    @classmethod
    def preflight_output_fields(cls, config: dict[str, Any]) -> set[str]:
        """Authorise `decisions.<field>` for every field the rules can set."""
        declared = set(DecisionOutput.model_fields)
        try:
            names = DecisionConfig(**config).output_field_names()
        except Exception:
            return declared | {"decisions"}
        return declared | {"decisions"} | {f"decisions.{name}" for name in names}

    @classmethod
    def preflight_static_output_values(cls, config: dict[str, Any]) -> dict[str, Any]:
        if not (config.get("rules") or []) and not (config.get("defaults") or {}):
            return {"decisions": {}}
        return {}

    async def run(self, state, resolved_config: dict[str, Any]) -> dict[str, Any]:
        cfg = DecisionConfig(**resolved_config)
        evaluation = evaluate_rules(
            cfg.rules, dict(state), initial=dict(cfg.defaults)
        )

        # Every declared field is present in the output even when nothing set
        # it. A downstream reference to a field only one branch writes must not
        # fail with "template path not resolvable" on the branches that don't.
        decisions = dict(evaluation.values)
        for name in cfg.declared_fields:
            decisions.setdefault(name, None)

        return {
            "decisions": decisions,
            "matched_rules": evaluation.matched_rules,
            "explanation": [item.model_dump() for item in evaluation.rules],
            "summary": evaluation.explanation_lines(),
        }
