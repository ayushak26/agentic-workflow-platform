"""The reusable primitive node set.

The claim these tests defend is the product claim: a new business behaviour is
configuration, not a new node type. So each test configures a primitive to do
something a specialized agent would traditionally have been written for, and
checks it does it — including the failure modes that decide whether the workflow
is safe to run unattended.
"""
from __future__ import annotations


from typing import Any, Type

import pytest
from pydantic import BaseModel

from app.llm.errors import LLMProviderUnavailableError, StructuredOutputError
from app.nodes.ai_task import AITaskAgent, effective_fields
from app.nodes.data_transform import DataTransformAgent
from app.nodes.decision import DecisionAgent
from app.nodes.router import RouterAgent
from app.nodes.workflow_input import WorkflowInputAgent, WorkflowInputConfig
from app.runtime.field_schema import field_paths


class ScriptedStructuredLLM:
    """A gateway that returns queued structured payloads, or raises queued errors.

    Records the prompts it was given, because several tests are about *what the
    node asked for* (the schema, the language policy) rather than what came back.
    """

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_model: Type[BaseModel],
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ):
        self.calls.append({"system": system, "user": user, "model": model})
        if not self.script:
            raise AssertionError("the LLM was called more times than scripted")
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return response_model.model_validate(nxt)

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ):
        from app.llm.base import LLMResponse

        self.calls.append({"system": system, "user": user, "model": model})
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return LLMResponse(
            text=str(nxt), model=model, input_tokens=0, output_tokens=0
        )


TRIAGE_FIELDS: list[dict[str, Any]] = [
    {"name": "language", "type": "string"},
    {
        "name": "intent",
        "type": "enum",
        "enum_values": ["technical_support", "quotation_request", "other"],
    },
    {
        "name": "equipment",
        "type": "object",
        "required": False,
        "fields": [
            {"name": "model", "type": "string", "required": False, "nullable": True}
        ],
    },
    {"name": "production_stopped", "type": "boolean"},
    {"name": "missing_information", "type": "list", "item_type": "string"},
]

GOOD_RESULT = {
    "language": "de",
    "intent": "technical_support",
    "equipment": {"model": "Dura 15"},
    "production_stopped": True,
    "missing_information": [],
    "confidence": 0.93,
    "reasoning": "The customer states the pump has failed and production is down.",
}


def ai_task(config: dict[str, Any], llm: Any, node_id: str = "understand") -> AITaskAgent:
    node = AITaskAgent(node_id, config)
    node.services = {"llm": llm}
    return node


async def run(node: Any, state: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = node.config.model_dump()
    return await node.run(state or {"inputs": {}, "node_outputs": {}}, resolved)


# --------------------------------------------------------------------------
# AI Task
# --------------------------------------------------------------------------

class TestAITaskStructuredOutput:
    @pytest.mark.asyncio
    async def test_valid_structured_output_becomes_a_typed_result(self):
        llm = ScriptedStructuredLLM([GOOD_RESULT])
        output = await run(
            ai_task(
                {
                    "task": "extract",
                    "instruction": "Understand the request.",
                    "input": "Unsere Dura 15 Pumpe ist ausgefallen.",
                    "output_fields": TRIAGE_FIELDS,
                },
                llm,
            )
        )
        assert output["status"] == "ok"
        assert output["result"]["intent"] == "technical_support"
        assert output["result"]["equipment"]["model"] == "Dura 15"
        assert output["attempts"] == 1

    @pytest.mark.asyncio
    async def test_confidence_and_language_are_promoted_to_runtime_fields(self):
        """A confidence gate should read `outputs.step.confidence` regardless of
        what the author named things inside their schema."""
        llm = ScriptedStructuredLLM([GOOD_RESULT])
        output = await run(
            ai_task(
                {"task": "extract", "input": "x", "output_fields": TRIAGE_FIELDS}, llm
            )
        )
        assert output["confidence"] == 0.93
        assert output["detected_language"] == "de"
        assert "production is down" in output["reasoning"]

    @pytest.mark.asyncio
    async def test_a_confidence_field_is_added_without_the_author_asking(self):
        fields = {item.name for item in effective_fields(
            {"output_fields": TRIAGE_FIELDS, "include_confidence": True}
        )}
        assert "confidence" in fields
        assert "reasoning" in fields

    @pytest.mark.asyncio
    async def test_an_author_s_own_confidence_field_is_not_shadowed(self):
        fields = effective_fields(
            {
                "output_fields": [
                    {"name": "confidence", "type": "integer", "description": "mine"}
                ],
                "include_confidence": True,
            }
        )
        matching = [item for item in fields if item.name == "confidence"]
        assert len(matching) == 1
        assert matching[0].type == "integer"

    @pytest.mark.asyncio
    async def test_invalid_output_is_retried_with_a_correction(self):
        llm = ScriptedStructuredLLM(
            [StructuredOutputError("intent was not one of the allowed values"), GOOD_RESULT]
        )
        output = await run(
            ai_task(
                {
                    "task": "extract",
                    "input": "x",
                    "output_fields": TRIAGE_FIELDS,
                    "max_retries": 1,
                },
                llm,
            )
        )
        assert output["status"] == "ok"
        assert output["attempts"] == 2
        assert "Correction required" in llm.calls[1]["user"]

    @pytest.mark.asyncio
    async def test_exhausted_retries_report_invalid_output_rather_than_raising(self):
        """With fail_on_error off, an unusable AI step becomes a *routable fact*
        so the workflow can send it to a person instead of dying."""
        llm = ScriptedStructuredLLM(
            [StructuredOutputError("bad"), StructuredOutputError("bad again")]
        )
        output = await run(
            ai_task(
                {
                    "task": "extract",
                    "input": "x",
                    "output_fields": TRIAGE_FIELDS,
                    "max_retries": 1,
                    "fail_on_error": False,
                },
                llm,
            )
        )
        assert output["status"] == "invalid_output"
        assert output["result"] == {}
        assert output["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_a_refusal_is_reported_distinctly_from_a_schema_failure(self):
        llm = ScriptedStructuredLLM(
            [RuntimeError("the model refused to answer this request")]
        )
        output = await run(
            ai_task(
                {
                    "task": "extract",
                    "input": "x",
                    "output_fields": TRIAGE_FIELDS,
                    "max_retries": 0,
                    "fail_on_error": False,
                },
                llm,
            )
        )
        assert output["status"] == "refused"

    @pytest.mark.asyncio
    async def test_provider_unavailability_does_not_burn_the_retry_budget(self):
        """Retrying a prompt no provider can serve is a certain failure; the
        second attempt would only add latency."""
        llm = ScriptedStructuredLLM(
            [LLMProviderUnavailableError("no configured provider")]
        )
        output = await run(
            ai_task(
                {
                    "task": "extract",
                    "input": "x",
                    "output_fields": TRIAGE_FIELDS,
                    "max_retries": 2,
                    "fail_on_error": False,
                },
                llm,
            )
        )
        assert output["status"] == "provider_error"
        assert output["attempts"] == 1
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_fail_on_error_raises_when_the_author_wants_a_hard_stop(self):
        llm = ScriptedStructuredLLM([StructuredOutputError("bad")])
        with pytest.raises(RuntimeError, match="invalid_output"):
            await run(
                ai_task(
                    {
                        "task": "extract",
                        "input": "x",
                        "output_fields": TRIAGE_FIELDS,
                        "max_retries": 0,
                        "fail_on_error": True,
                    },
                    llm,
                )
            )

    @pytest.mark.asyncio
    async def test_confidence_zero_on_failure_makes_a_gate_escalate(self):
        """None would make `confidence < 0.8` silently False, which reads as
        "the model was sure" — the opposite of the truth."""
        from app.runtime.rules import Condition, evaluate_condition

        llm = ScriptedStructuredLLM([StructuredOutputError("bad")])
        output = await run(
            ai_task(
                {
                    "task": "extract",
                    "input": "x",
                    "output_fields": TRIAGE_FIELDS,
                    "max_retries": 0,
                    "fail_on_error": False,
                },
                llm,
            )
        )
        gate = evaluate_condition(
            Condition(
                field="outputs.understand.confidence",
                operator="less_than",
                value=0.8,
            ),
            {"node_outputs": {"understand": output}},
        )
        assert gate.matched is True


class TestAITaskPrompting:
    @pytest.mark.asyncio
    async def test_language_policy_reaches_the_system_prompt(self):
        """Multilingual behaviour is configuration on this node, not a chain of
        translate-then-extract calls."""
        llm = ScriptedStructuredLLM([GOOD_RESULT])
        await run(
            ai_task(
                {
                    "task": "extract",
                    "input": "Bonjour",
                    "output_fields": TRIAGE_FIELDS,
                    "language": {
                        "input_language": "auto",
                        "process_in_original_language": True,
                        "output_language": "en",
                        "preserve_original": True,
                    },
                },
                llm,
            )
        )
        system = llm.calls[0]["system"]
        assert "Detect the language yourself" in system
        assert "Reason over the original text" in system
        assert "Write your output in en" in system

    @pytest.mark.asyncio
    async def test_one_call_covers_detection_classification_and_extraction(self):
        llm = ScriptedStructuredLLM([GOOD_RESULT])
        await run(
            ai_task(
                {"task": "extract", "input": "x", "output_fields": TRIAGE_FIELDS}, llm
            )
        )
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_field_descriptions_are_sent_as_the_contract(self):
        llm = ScriptedStructuredLLM([GOOD_RESULT])
        await run(
            ai_task(
                {
                    "task": "extract",
                    "input": "x",
                    "output_fields": [
                        {
                            "name": "serial_number",
                            "type": "string",
                            "required": False,
                            "description": "Exactly as written; never normalise.",
                        },
                        *TRIAGE_FIELDS,
                    ],
                },
                llm,
            )
        )
        assert "never normalise" in llm.calls[0]["system"]

    @pytest.mark.asyncio
    async def test_extract_task_forbids_inventing_values(self):
        llm = ScriptedStructuredLLM([GOOD_RESULT])
        await run(
            ai_task(
                {"task": "extract", "input": "x", "output_fields": TRIAGE_FIELDS}, llm
            )
        )
        assert "never" in llm.calls[0]["system"].lower()
        assert "guess" in llm.calls[0]["system"].lower()

    @pytest.mark.asyncio
    async def test_examples_and_context_blocks_appear_in_the_user_prompt(self):
        llm = ScriptedStructuredLLM([GOOD_RESULT])
        await run(
            ai_task(
                {
                    "task": "extract",
                    "input": "the message",
                    "context": {"Subject": "Pump failure"},
                    "examples": [{"input": "example in", "output": {"intent": "other"}}],
                    "output_fields": TRIAGE_FIELDS,
                },
                llm,
            )
        )
        user = llm.calls[0]["user"]
        assert "# Subject\nPump failure" in user
        assert "# Examples" in user
        assert "the message" in user

    @pytest.mark.asyncio
    async def test_a_task_without_a_schema_returns_free_text(self):
        llm = ScriptedStructuredLLM(["Dear customer, we received your request."])
        output = await run(
            ai_task(
                {
                    "task": "draft_response",
                    "instruction": "Draft a reply.",
                    "input": "x",
                    "include_confidence": False,
                },
                llm,
            )
        )
        assert output["status"] == "ok"
        assert output["text"].startswith("Dear customer")
        assert output["result"] == {}


class TestAITaskContract:
    def test_extract_without_a_schema_is_rejected_at_config_time(self):
        with pytest.raises(ValueError, match="needs an output schema"):
            AITaskAgent("x", {"task": "extract", "input": "y"})

    def test_preflight_authorises_the_authored_result_paths(self):
        """Zero-token proof that `{{understand.result.equipment.model}}` is real
        and `...custmoer.company` is not."""
        fields = AITaskAgent.preflight_output_fields(
            {"output_fields": TRIAGE_FIELDS, "include_confidence": True}
        )
        assert "result.equipment.model" in fields
        assert "result.intent" in fields
        assert "confidence" in fields
        assert "result.custmoer.company" not in fields

    def test_a_schemaless_task_declares_result_as_statically_empty(self):
        assert AITaskAgent.preflight_static_output_values({"output_fields": []}) == {
            "result": {}
        }

    def test_presets_are_configuration_not_node_types(self):
        from app.nodes.ai_task import TASK_PRESETS

        for preset in TASK_PRESETS:
            assert preset["task"] in {
                "extract",
                "classify",
                "translate",
                "summarize",
                "draft_response",
                "custom",
            }


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------

class TestDecision:
    @pytest.mark.asyncio
    async def test_rules_produce_facts_and_an_explanation(self):
        node = DecisionAgent(
            "safety",
            {
                "defaults": {"human_review": False, "urgency": "normal"},
                "rules": [
                    {
                        "name": "Stopped production is critical",
                        "when": {
                            "operator": "and",
                            "conditions": [
                                {
                                    "field": "outputs.understand.result.production_stopped",
                                    "operator": "is_true",
                                }
                            ],
                        },
                        "then": [{"field": "urgency", "value": "critical"}],
                    }
                ],
            },
        )
        output = await run(
            node,
            {"node_outputs": {"understand": {"result": {"production_stopped": True}}}},
        )
        assert output["decisions"]["urgency"] == "critical"
        assert output["decisions"]["human_review"] is False
        assert output["matched_rules"] == ["Stopped production is critical"]
        assert any("is true" in line for line in output["summary"])

    @pytest.mark.asyncio
    async def test_it_spends_no_tokens(self):
        """No `llm` in services at all: if the node ever reached for one this
        would raise, which is the assertion."""
        node = DecisionAgent("safety", {"defaults": {"a": 1}})
        node.services = {}
        output = await run(node, {"node_outputs": {}})
        assert output["decisions"] == {"a": 1}

    def test_it_declares_no_required_services(self):
        assert DecisionAgent.required_services({}) == set()

    @pytest.mark.asyncio
    async def test_declared_fields_are_always_present(self):
        """A downstream reference to a field only one rule writes must resolve on
        the runs where that rule did not fire."""
        node = DecisionAgent(
            "safety",
            {
                "declared_fields": ["escalation_reason"],
                "rules": [
                    {
                        "name": "never",
                        "when": {
                            "operator": "and",
                            "conditions": [
                                {"field": "outputs.nope.x", "operator": "exists"}
                            ],
                        },
                        "then": [{"field": "escalation_reason", "value": "because"}],
                    }
                ],
            },
        )
        output = await run(node, {"node_outputs": {}})
        assert output["decisions"]["escalation_reason"] is None

    def test_preflight_authorises_every_field_a_rule_can_set(self):
        fields = DecisionAgent.preflight_output_fields(
            {
                "defaults": {"human_review": False},
                "rules": [
                    {
                        "name": "r",
                        "default": True,
                        "then": [{"field": "urgency", "value": "critical"}],
                    }
                ],
            }
        )
        assert "decisions.human_review" in fields
        assert "decisions.urgency" in fields


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

class TestRouterFieldMode:
    @pytest.mark.asyncio
    async def test_it_routes_on_a_field_value_and_records_why(self):
        node = RouterAgent(
            "route",
            {
                "mode": "field",
                "route_field": "outputs.understand.result.intent",
                "branches": {
                    "technical_support": "support",
                    "quotation_request": "sales",
                },
                "fallback": "human_review",
            },
        )
        output = await run(
            node,
            {"node_outputs": {"understand": {"result": {"intent": "technical_support"}}}},
        )
        assert output["route"] == "support"
        assert output["route_value"] == "technical_support"
        assert output["used_fallback"] is False
        assert "technical_support" in output["matched_conditions"][0]

    @pytest.mark.asyncio
    async def test_an_unexpected_value_takes_the_fallback_visibly(self):
        node = RouterAgent(
            "route",
            {
                "mode": "field",
                "route_field": "outputs.understand.result.intent",
                "branches": {"technical_support": "support"},
                "fallback": "human_review",
            },
        )
        output = await run(
            node,
            {"node_outputs": {"understand": {"result": {"intent": "warranty_claim"}}}},
        )
        assert output["route"] == "human_review"
        assert output["used_fallback"] is True

    @pytest.mark.asyncio
    async def test_a_missing_value_with_no_fallback_fails_loudly(self):
        node = RouterAgent(
            "route",
            {
                "mode": "field",
                "route_field": "outputs.understand.result.intent",
                "branches": {"technical_support": "support"},
            },
        )
        with pytest.raises(ValueError, match="matches no branch"):
            await run(node, {"node_outputs": {}})

    @pytest.mark.asyncio
    async def test_a_boolean_field_can_drive_two_branches(self):
        node = RouterAgent(
            "gate",
            {
                "mode": "field",
                "route_field": "outputs.safety.decisions.human_review",
                "branches": {"true": "human_review", "false": "automatic"},
            },
        )
        output = await run(
            node, {"node_outputs": {"safety": {"decisions": {"human_review": True}}}}
        )
        assert output["route"] == "human_review"

    @pytest.mark.asyncio
    async def test_branch_matching_tolerates_case(self):
        node = RouterAgent(
            "route",
            {
                "mode": "field",
                "route_field": "outputs.understand.result.intent",
                "branches": {"technical_support": "support"},
                "fallback": "human_review",
            },
        )
        output = await run(
            node,
            {"node_outputs": {"understand": {"result": {"intent": "Technical_Support"}}}},
        )
        assert output["route"] == "support"


class TestRouterConditionsMode:
    @pytest.mark.asyncio
    async def test_the_first_matching_case_wins_and_explains_itself(self):
        node = RouterAgent(
            "route",
            {
                "mode": "conditions",
                "cases": [
                    {
                        "route": "priority_support",
                        "when": {
                            "operator": "and",
                            "conditions": [
                                {
                                    "field": "outputs.understand.result.intent",
                                    "operator": "equals",
                                    "value": "technical_support",
                                },
                                {
                                    "field": "outputs.understand.confidence",
                                    "operator": "greater_or_equal",
                                    "value": 0.8,
                                },
                            ],
                        },
                    },
                    {
                        "route": "support",
                        "when": {
                            "operator": "and",
                            "conditions": [
                                {
                                    "field": "outputs.understand.result.intent",
                                    "operator": "equals",
                                    "value": "technical_support",
                                }
                            ],
                        },
                    },
                ],
                "fallback": "human_review",
            },
        )
        state = {
            "node_outputs": {
                "understand": {
                    "result": {"intent": "technical_support"},
                    "confidence": 0.91,
                }
            }
        }
        output = await run(node, state)
        assert output["route"] == "priority_support"
        assert len(output["matched_conditions"]) == 2

        state["node_outputs"]["understand"]["confidence"] = 0.64
        downgraded = await run(node, state)
        assert downgraded["route"] == "support"

    @pytest.mark.asyncio
    async def test_no_matching_case_takes_the_fallback(self):
        node = RouterAgent(
            "route",
            {
                "mode": "conditions",
                "cases": [
                    {
                        "route": "support",
                        "when": {
                            "operator": "and",
                            "conditions": [
                                {"field": "outputs.nope.x", "operator": "exists"}
                            ],
                        },
                    }
                ],
                "fallback": "human_review",
            },
        )
        output = await run(node, {"node_outputs": {}})
        assert output["route"] == "human_review"
        assert output["used_fallback"] is True

    def test_route_names_cover_branches_and_the_fallback(self):
        node = RouterAgent(
            "route",
            {
                "mode": "field",
                "route_field": "x",
                "branches": {"a": "sales", "b": "sales", "c": "support"},
                "fallback": "human_review",
            },
        )
        assert node.config.route_names() == ["sales", "support", "human_review"]


class TestRouterBackwardCompatibility:
    """Existing workflows use the string-expression `rule` mode. It must keep
    behaving exactly as before."""

    @pytest.mark.asyncio
    async def test_legacy_rule_mode_still_routes(self):
        node = RouterAgent(
            "route",
            {
                "mode": "rule",
                "rules": [
                    {"name": "finance", "condition": "intel.parsed.industry == 'finance'"},
                    {"name": "other", "default": True},
                ],
            },
        )
        output = await run(
            node, {"node_outputs": {"intel": {"parsed": {"industry": "finance"}}}}
        )
        assert output["route"] == "finance"

    @pytest.mark.asyncio
    async def test_legacy_default_rule_still_applies(self):
        node = RouterAgent(
            "route",
            {
                "mode": "rule",
                "rules": [
                    {"name": "finance", "condition": "intel.parsed.industry == 'finance'"},
                    {"name": "other", "default": True},
                ],
            },
        )
        output = await run(
            node, {"node_outputs": {"intel": {"parsed": {"industry": "retail"}}}}
        )
        assert output["route"] == "other"

    def test_llm_mode_still_declares_the_llm_service(self):
        assert "llm" in RouterAgent.required_services({"mode": "llm"})
        assert RouterAgent.required_services({"mode": "field"}) == set()


# --------------------------------------------------------------------------
# Transform
# --------------------------------------------------------------------------

class TestDataTransform:
    @pytest.mark.asyncio
    async def test_deterministic_operations_build_an_object(self):
        node = DataTransformAgent(
            "handoff",
            {
                "operations": [
                    {"target": "department", "operation": "constant", "value": "Sales"},
                    {
                        "target": "company",
                        "operation": "coalesce",
                        "sources": [
                            "outputs.x.result.customer.company",
                            "inputs.sender",
                        ],
                        "default": "unknown",
                    },
                    {
                        "target": "title",
                        "operation": "format",
                        "value": "[{{outputs.x.result.urgency}}] {{inputs.subject}}",
                    },
                    {
                        "target": "part_count",
                        "operation": "count",
                        "source": "outputs.x.result.parts",
                    },
                    {
                        "target": "flow_l_per_h",
                        "operation": "number",
                        "source": "outputs.x.result.flow",
                        "multiply_by": 1000,
                    },
                    {
                        "target": "tags",
                        "operation": "object",
                        "value": {"source": "email", "intent": "$outputs.x.result.intent"},
                    },
                ]
            },
        )
        output = await run(
            node,
            {
                "inputs": {"subject": "Pump failure", "sender": "kunde@werke.de"},
                "node_outputs": {
                    "x": {
                        "result": {
                            "customer": {"company": None},
                            "urgency": "critical",
                            "parts": ["a", "b"],
                            "flow": 15,
                            "intent": "technical_support",
                        }
                    }
                },
            },
        )
        data = output["data"]
        assert data["department"] == "Sales"
        assert data["company"] == "kunde@werke.de"
        assert data["title"] == "[critical] Pump failure"
        assert data["part_count"] == 2
        assert data["flow_l_per_h"] == 15000
        assert data["tags"] == {"source": "email", "intent": "technical_support"}

    @pytest.mark.asyncio
    async def test_defaulted_targets_are_reported_not_silent(self):
        node = DataTransformAgent(
            "handoff",
            {
                "operations": [
                    {
                        "target": "model",
                        "operation": "copy",
                        "source": "outputs.x.result.model",
                        "default": "not stated",
                    }
                ]
            },
        )
        output = await run(node, {"node_outputs": {"x": {"result": {}}}})
        assert output["data"]["model"] == "not stated"
        assert output["defaulted"] == ["model"]

    @pytest.mark.asyncio
    async def test_number_parsing_handles_extracted_text(self):
        node = DataTransformAgent(
            "n",
            {
                "operations": [
                    {
                        "target": "flow",
                        "operation": "number",
                        "source": "inputs.raw",
                    }
                ]
            },
        )
        output = await run(node, {"inputs": {"raw": "approx. 20,5 m3/h"}, "node_outputs": {}})
        assert output["data"]["flow"] == 20.5

    def test_writing_one_target_twice_is_rejected(self):
        with pytest.raises(ValueError, match="more than once"):
            DataTransformAgent(
                "n",
                {
                    "operations": [
                        {"target": "a", "operation": "constant", "value": 1},
                        {"target": "a", "operation": "constant", "value": 2},
                    ]
                },
            )

    def test_an_operation_missing_its_source_is_rejected(self):
        with pytest.raises(ValueError, match="needs a source"):
            DataTransformAgent(
                "n", {"operations": [{"target": "a", "operation": "copy"}]}
            )

    def test_it_needs_no_services(self):
        assert DataTransformAgent.required_services({}) == set()

    def test_preflight_authorises_each_target(self):
        fields = DataTransformAgent.preflight_output_fields(
            {"operations": [{"target": "company", "operation": "constant", "value": 1}]}
        )
        assert "data.company" in fields


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

class TestWorkflowInput:
    @pytest.mark.asyncio
    async def test_declared_fields_are_read_from_workflow_inputs(self):
        node = WorkflowInputAgent(
            "intake",
            {
                "source": "email",
                "fields": [
                    {"name": "message", "source": "inputs.message", "required": True},
                    {"name": "subject", "source": "inputs.subject"},
                ],
            },
        )
        output = await run(
            node, {"inputs": {"message": "Hallo", "subject": "Anfrage"}, "node_outputs": {}}
        )
        assert output["data"] == {"message": "Hallo", "subject": "Anfrage"}
        assert output["source"] == "email"
        assert output["missing"] == []

    @pytest.mark.asyncio
    async def test_a_missing_required_field_is_reported_not_raised(self):
        """So a workflow can route "the customer sent nothing usable" instead of
        crashing on the first step."""
        node = WorkflowInputAgent(
            "intake",
            {"fields": [{"name": "message", "source": "inputs.message", "required": True}]},
        )
        output = await run(node, {"inputs": {}, "node_outputs": {}})
        assert output["missing"] == ["message"]

    @pytest.mark.asyncio
    async def test_the_sample_is_a_fallback_and_never_an_override(self):
        node = WorkflowInputAgent(
            "intake",
            {
                "fields": [{"name": "message", "source": "inputs.message"}],
                "sample": {"message": "sample text"},
            },
        )
        real = await run(node, {"inputs": {"message": "real text"}, "node_outputs": {}})
        assert real["data"]["message"] == "real text"

        empty = await run(node, {"inputs": {}, "node_outputs": {}})
        assert empty["data"]["message"] == "sample text"

    @pytest.mark.asyncio
    async def test_default_source_is_the_matching_workflow_input(self):
        node = WorkflowInputAgent("intake", {"fields": [{"name": "message"}]})
        output = await run(node, {"inputs": {"message": "hi"}, "node_outputs": {}})
        assert output["data"]["message"] == "hi"

    def test_preflight_authorises_each_declared_field(self):
        fields = WorkflowInputAgent.preflight_output_fields(
            {"fields": [{"name": "message"}]}
        )
        assert "data.message" in fields


class TestWorkflowInputListOfEnum:
    """An incoming input can declare `List<Enum>` — the same closed-set-of-
    values-in-an-array shape as a structured output field — so a value
    mapped straight from an upstream AI step's output is held to the same
    contract on the way in as it was on the way out."""

    RESPONSIBILITIES_FIELD = {
        "name": "responsibilities",
        "type": "list",
        "item_type": "enum",
        "item_enum_values": ["pump_application_selection", "quotation_management"],
        "required": True,
    }

    @pytest.mark.asyncio
    async def test_a_valid_list_of_enum_value_passes_through_as_an_array(self):
        node = WorkflowInputAgent("intake", {"fields": [self.RESPONSIBILITIES_FIELD]})
        output = await run(
            node,
            {
                "inputs": {
                    "responsibilities": [
                        "pump_application_selection",
                        "quotation_management",
                    ]
                },
                "node_outputs": {},
            },
        )
        # Direct path resolution, not string interpolation — the array must
        # survive as an array, not be joined, truncated, or stringified.
        assert output["data"]["responsibilities"] == [
            "pump_application_selection",
            "quotation_management",
        ]
        assert isinstance(output["data"]["responsibilities"], list)

    @pytest.mark.asyncio
    async def test_a_value_outside_the_allowed_set_is_rejected_before_downstream_runs(self):
        node = WorkflowInputAgent("intake", {"fields": [self.RESPONSIBILITIES_FIELD]})
        with pytest.raises(ValueError, match="declared shape"):
            await run(
                node,
                {
                    "inputs": {"responsibilities": ["some_random_value"]},
                    "node_outputs": {},
                },
            )

    def test_preflight_exposes_the_allowed_values(self):
        """What lets the mapping picker and rule editor offer the closed set
        instead of a free-text box for an incoming List<Enum> input."""
        specs = WorkflowInputConfig(fields=[self.RESPONSIBILITIES_FIELD]).as_field_specs()
        paths = {item.path: item for item in field_paths(specs)}
        assert paths["responsibilities"].item_type == "enum"
        assert paths["responsibilities"].enum_values == [
            "pump_application_selection",
            "quotation_management",
        ]

    def test_legacy_list_binding_without_item_type_degrades_instead_of_crashing(self):
        """A binding saved before item_type existed on this model must still
        load — degraded to a plain scalar — rather than fail to open the
        workflow at all."""
        cfg = WorkflowInputConfig(fields=[{"name": "tags", "type": "list"}])
        assert cfg.fields[0].type == "list"
        assert cfg.fields[0].item_type == "string"

    @pytest.mark.asyncio
    async def test_structured_output_list_of_enum_maps_straight_into_a_declared_input(self):
        """The acceptance scenario end to end: an upstream AI step's
        `responsibilities: List<Enum>` structured output, mapped directly
        into a downstream input declaring the same shape — no string
        conversion, no join, no dropped values, no FieldSpec error."""
        node = WorkflowInputAgent(
            "next_node_input",
            {
                "fields": [
                    {
                        **self.RESPONSIBILITIES_FIELD,
                        "source": "outputs.llm2.data.responsibilities",
                    }
                ]
            },
        )
        state = {
            "inputs": {},
            "node_outputs": {
                "llm2": {
                    "data": {
                        "responsibilities": [
                            "pump_application_selection",
                            "quotation_management",
                        ]
                    }
                }
            },
        }
        output = await run(node, state)
        assert output["data"]["responsibilities"] == [
            "pump_application_selection",
            "quotation_management",
        ]
        assert output["missing"] == []

    def test_save_reload_round_trip_preserves_allowed_values(self):
        """Configure once, dump to the wire format a saved workflow uses,
        reload — no allowed value may disappear."""
        cfg = WorkflowInputConfig(fields=[self.RESPONSIBILITIES_FIELD])
        dumped = cfg.model_dump(mode="json")
        reloaded = WorkflowInputConfig.model_validate(dumped)
        assert reloaded.fields[0].item_enum_values == [
            "pump_application_selection",
            "quotation_management",
        ]
        assert reloaded.fields[0].required is True


# --------------------------------------------------------------------------
# Palette metadata — what the Builder shows
# --------------------------------------------------------------------------

class TestPaletteMetadata:
    def test_the_core_vocabulary_is_small_and_complete(self):
        from app.nodes.registry import NodeRegistry

        core = {
            entry["type_name"]
            for entry in NodeRegistry.manifest()
            if entry["family"] == "core"
        }
        assert core == {
            "WorkflowInputAgent",
            "AITaskAgent",
            "DecisionAgent",
            "RouterAgent",
            "DataTransformAgent",
            "HumanInLoopAgent",
            "EmailAgent",
            "MCPToolAgent",
            "TextAssemblerAgent",
            "ExternalActionAgent",
        }

    def test_execution_kind_makes_the_automation_boundary_visible(self):
        from app.nodes.registry import NodeRegistry

        kinds = {
            entry["type_name"]: entry["execution_kind"]
            for entry in NodeRegistry.manifest()
        }
        assert kinds["AITaskAgent"] == "ai"
        assert kinds["DecisionAgent"] == "deterministic"
        assert kinds["RouterAgent"] == "deterministic"
        assert kinds["EmailAgent"] == "external"
        assert kinds["MCPToolAgent"] == "external"
        assert kinds["HumanInLoopAgent"] == "human"
        assert kinds["WorkflowInputAgent"] == "input"

    def test_every_core_node_explains_itself(self):
        from app.nodes.registry import NodeRegistry

        for entry in NodeRegistry.manifest():
            if entry["family"] != "core":
                continue
            about = entry["about"]
            for key in ("what", "why", "receives", "produces"):
                assert about.get(key), f"{entry['type_name']} is missing about.{key}"

    def test_uses_ai_is_derived_from_the_declared_services(self):
        """Derived rather than hand-listed, so it cannot go stale when a node
        starts or stops calling a model."""
        from app.nodes.registry import NodeRegistry

        entries = {entry["type_name"]: entry for entry in NodeRegistry.manifest()}
        assert entries["AITaskAgent"]["uses_ai"] is True
        assert entries["DecisionAgent"]["uses_ai"] is False
        assert entries["EmailAgent"]["external_action"] is True
