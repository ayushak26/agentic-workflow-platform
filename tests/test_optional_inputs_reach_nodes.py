"""Regression coverage for the "an optional input was not supplied" bug class.

A workflow declares `subject` as `required: false`, the person running it
pastes only a message body, and the run dies before doing any work:

    ValidationError: 1 validation error for AITaskConfig
    context.Subject
      Input should be a valid string [input_value=None]

or, one node earlier:

    KeyError: Template path not resolvable: inputs.subject

Both are the same mistake seen from two sides — "not supplied" being modelled
as "absent" rather than as "empty". Three things closed it:

1.  app/runtime/executor.py seeds every DECLARED input the caller omitted with
    None, so `{{inputs.subject}}` resolves to nothing instead of raising. A
    reference to an input the workflow never declared still raises, which is
    the real authoring mistake the check exists for.
2.  app/runtime/templating.py:prune_absent drops a resolved-to-None value whose
    config field cannot hold None, so the field's own default applies — the
    same result as the author having left it out.
3.  app/nodes/mcp_tool.py skips a call only when a REQUIRED argument came up
    empty; an empty optional one is dropped from the call instead.

Every workflow with an optional input was affected: the CRM triage workflows
(`context: {Subject: '{{...data.subject}}'}`), the pump case-routing workflow
(`{{inputs.subject}}` inside a prompt), lead enrichment (`{{inputs.job_title}}`).
"""
from __future__ import annotations

import pytest

import app.nodes  # noqa: F401 - populates the registry
from app.llm.base import LLMResponse
from app.nodes.mcp_tool import MCPToolAgent
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string
from app.runtime.templating import prune_absent


class _StubLLM:
    def __init__(self):
        self.prompts: list[str] = []

    async def complete(self, *, model=None, system=None, user=None, **_):
        self.prompts.append(user or "")
        return LLMResponse(text="ok", model="stub", input_tokens=1, output_tokens=1)


OPTIONAL_INPUT_WORKFLOW = """
name: optional_input_workflow
entry: draft
inputs:
  message:
    type: text
    required: true
  subject:
    type: text
    required: false
nodes:
  - id: draft
    type: AITaskAgent
    config:
      task: draft_response
      instruction: Reply to this.
      input: "{{inputs.message}}"
      context:
        Subject: "{{inputs.subject}}"
exit: draft
"""


async def test_an_unsupplied_optional_input_does_not_fail_config_validation():
    """The reported failure, end to end: no subject given, run completes."""
    spec = load_workflow_from_string(OPTIONAL_INPUT_WORKFLOW)
    llm = _StubLLM()
    result = await run_workflow(
        spec, {"message": "Hello"}, services={"llm": llm, "cost_ledger": None},
        run_id="optional-input-1",
    )

    assert result["status"] == "completed"
    # The empty context block is omitted rather than rendered as "None".
    assert "None" not in llm.prompts[0]
    assert "# Subject" not in llm.prompts[0]


async def test_a_supplied_optional_input_still_reaches_the_prompt():
    spec = load_workflow_from_string(OPTIONAL_INPUT_WORKFLOW)
    llm = _StubLLM()
    result = await run_workflow(
        spec, {"message": "Hello", "subject": "Pump enquiry"},
        services={"llm": llm, "cost_ledger": None}, run_id="optional-input-2",
    )

    assert result["status"] == "completed"
    assert "# Subject\nPump enquiry" in llm.prompts[0]


UNDECLARED_INPUT_WORKFLOW = OPTIONAL_INPUT_WORKFLOW.replace(
    '"{{inputs.subject}}"', '"{{inputs.not_an_input}}"'
)


async def test_a_reference_to_an_undeclared_input_is_still_an_error():
    """Seeding must not turn a typo into silence — only DECLARED inputs are
    seeded, so a reference to something the workflow never declared is still
    rejected, here by preflight before a single token is spent."""
    spec = load_workflow_from_string(UNDECLARED_INPUT_WORKFLOW)
    with pytest.raises(Exception, match="unknown input"):
        await run_workflow(
            spec, {"message": "Hello"},
            services={"llm": _StubLLM(), "cost_ledger": None},
            run_id="optional-input-3",
        )


# ── prune_absent, directly ──────────────────────────────────────────────────


def test_prune_absent_drops_none_a_field_cannot_hold():
    from app.nodes.ai_task import AITaskConfig

    cleaned = prune_absent(
        {"instruction": None, "context": {"Subject": None, "Sender": "a@b.example"}},
        AITaskConfig,
    )

    assert "instruction" not in cleaned          # falls back to its default
    assert cleaned["context"] == {"Sender": "a@b.example"}


def test_prune_absent_keeps_none_where_none_is_the_answer():
    """MCPToolAgent reads a null argument as "there was nothing to look up".
    Pruning it would turn a deliberate skip into a call with the argument
    silently missing."""
    from app.nodes.mcp_tool import MCPToolConfig

    cleaned = prune_absent(
        {"server_id": "crm", "tool": "find_account",
         "arguments": {"account_id": None}, "timeout_seconds": None},
        MCPToolConfig,
    )

    assert cleaned["arguments"] == {"account_id": None}
    assert cleaned["timeout_seconds"] is None


# ── MCP: required vs. optional arguments ────────────────────────────────────


class _Service:
    """Only the two methods the node calls."""

    def __init__(self, required: list[str]):
        self._required = required
        self.calls: list[dict] = []

    async def find_tool(self, server_id, tool_name):
        return {
            "name": tool_name,
            "input_schema": {"type": "object", "required": self._required},
        }

    async def call(self, *, arguments, **_):
        self.calls.append(dict(arguments))
        return {
            "server": "erp", "tool": "get_quote", "operation": "read",
            "data": {"quotes": []}, "text": "", "is_structured": True,
            "mode": "structured", "duration_s": 0.0, "deduplicated": False,
        }


async def _run_tool(service, arguments):
    node = MCPToolAgent("get_quote", {
        "server_id": "erp", "tool": "get_quote", "arguments": arguments,
    })
    node.services = {"mcp": service}
    state = {"inputs": {"SYSTEM.run_id": "r"}, "node_outputs": {}}
    return await node.run(state, node.config.model_dump())


async def test_an_empty_optional_argument_is_dropped_rather_than_cancelling_the_call():
    """The pump workflow's quote lookup maps three arguments; a customer who
    quotes no PO number must not stop the quote from being fetched."""
    service = _Service(required=["quotation_reference", "account_id"])
    output = await _run_tool(service, {
        "quotation_reference": "QUO-1", "account_id": "A-1",
        "customer_po_reference": "",
    })

    assert output["status"] == "ok"
    assert service.calls == [{"quotation_reference": "QUO-1", "account_id": "A-1"}]


async def test_an_empty_required_argument_still_skips_the_call():
    service = _Service(required=["quotation_reference", "account_id"])
    output = await _run_tool(service, {
        "quotation_reference": "QUO-1", "account_id": None,
    })

    assert output["status"] == "skipped"
    assert output["found"] is False
    assert service.calls == []
