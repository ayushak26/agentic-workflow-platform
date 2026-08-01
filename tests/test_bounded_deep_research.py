"""BoundedToolResearchService / BoundedDeepResearchAgent tests.

Replaces OpenAI's dedicated async Deep Research job API with a bounded
chat_with_tools gather loop plus one high-reasoning-effort synthesis call;
these tests stub both the `llm` and `web_search` services, following the
same stubbing pattern as test_mcp_agent.py."""
from __future__ import annotations

import app.nodes  # noqa: F401
from app.llm.base import LLMResponse, LLMToolUseResponse, ToolCall
from app.nodes.registry import NodeRegistry
from app.research.deep_research import BoundedToolResearchService, ResearchBrief
from app.tools.web_io import WebResult, WebSearchResponse


class StubLLM:
    def __init__(self, tool_responses, completion):
        self._tool_responses = tool_responses
        self._completion = completion
        self.chat_with_tools_calls: list[dict] = []
        self.complete_calls: list[dict] = []

    async def chat_with_tools(self, **kwargs):
        self.chat_with_tools_calls.append(kwargs)
        return self._tool_responses.pop(0)

    async def complete(self, **kwargs):
        self.complete_calls.append(kwargs)
        return self._completion


class StubWebSearch:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    async def search(self, query, *, top_k=8, **kwargs):
        self.calls.append({"query": query, "top_k": top_k})
        return self._response


class FakeSkillSelection:
    names: list[str] = []
    versions: dict[str, str] = {}


class FakeSkillCatalog:
    def select(self, **kwargs):
        return FakeSkillSelection()

    def prompt_bundle(self, selection):
        return "approved guidance"


async def test_bounded_tool_research_service_gathers_and_synthesizes():
    llm = StubLLM(
        tool_responses=[
            LLMToolUseResponse(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="web_search",
                        arguments={"query": "biomass residues EU policy"},
                    )
                ],
                model="gpt-5.6-sol",
                input_tokens=10,
                output_tokens=5,
            ),
            LLMToolUseResponse(
                text="Gathered enough evidence.",
                tool_calls=[],
                model="gpt-5.6-sol",
                input_tokens=8,
                output_tokens=4,
            ),
        ],
        completion=LLMResponse(
            text="# Dossier\nSynthesis grounded in gathered sources.",
            model="gpt-5.6-sol",
            input_tokens=50,
            output_tokens=100,
        ),
    )
    web_search = StubWebSearch(
        WebSearchResponse(
            query="biomass residues EU policy",
            requested_provider="auto",
            actual_provider="tavily",
            results=[
                WebResult(
                    title="CAP Strategy",
                    url="https://europa.eu/cap",
                    snippet="Common Agricultural Policy provisions.",
                    score=0.9,
                ),
            ],
        )
    )
    service = BoundedToolResearchService(llm=llm, web_search=web_search)
    brief = ResearchBrief(
        brief_id="RQ-1",
        track="eu_policy_and_regulation",
        question="Which provisions govern biomass residue valorisation?",
        purpose="policy_alignment",
        linked_claim_ids=["CL-1"],
        max_tool_calls=4,
    )

    dossier = await service.research(brief=brief, instructions="Be rigorous.")

    assert dossier.status == "completed"
    assert dossier.model == "gpt-5.6-sol"
    assert len(dossier.citations) == 1
    assert dossier.citations[0].url == "https://europa.eu/cap"
    assert dossier.usage.tool_calls == 1
    assert "Synthesis grounded" in dossier.report_markdown
    assert web_search.calls[0]["query"] == "biomass residues EU policy"
    # Tool-orchestration turns go through chat_with_tools; the actual
    # synthesis is a separate, tool-free call requesting max reasoning.
    assert llm.complete_calls[0]["reasoning_effort"] == "xhigh"


async def test_bounded_tool_research_service_supports_claude_fable_5():
    llm = StubLLM(
        tool_responses=[
            LLMToolUseResponse(
                text="done",
                tool_calls=[],
                model="claude-fable-5",
                input_tokens=5,
                output_tokens=5,
            ),
        ],
        completion=LLMResponse(
            text="Synthesis.",
            model="claude-fable-5",
            input_tokens=20,
            output_tokens=30,
        ),
    )
    web_search = StubWebSearch(
        WebSearchResponse(
            query="x", requested_provider="auto", actual_provider="tavily", results=[]
        )
    )
    service = BoundedToolResearchService(llm=llm, web_search=web_search)
    brief = ResearchBrief(
        brief_id="RQ-2",
        track="state_of_art",
        question="q",
        purpose="p",
        research_model="claude-fable-5",
        max_tool_calls=2,
    )

    dossier = await service.research(brief=brief, instructions="Be rigorous.")

    assert dossier.model == "claude-fable-5"
    assert dossier.citations == []
    assert dossier.usage.tool_calls == 0


async def test_bounded_tool_research_service_hands_off_on_timeout():
    # A negative budget means the deadline is already in the past before the
    # first chat_with_tools call — the loop must stop immediately rather
    # than block on gathering, and still synthesize from what it has (none).
    llm = StubLLM(
        tool_responses=[
            LLMToolUseResponse(
                text=None,
                tool_calls=[ToolCall(id="t1", name="web_search", arguments={"query": "x"})],
                model="gpt-5.6-sol",
                input_tokens=5,
                output_tokens=5,
            ),
        ],
        completion=LLMResponse(
            text="Best-effort synthesis with no gathered evidence.",
            model="gpt-5.6-sol",
            input_tokens=10,
            output_tokens=10,
        ),
    )
    web_search = StubWebSearch(
        WebSearchResponse(
            query="x", requested_provider="auto", actual_provider="tavily", results=[]
        )
    )
    service = BoundedToolResearchService(llm=llm, web_search=web_search)
    brief = ResearchBrief(
        brief_id="RQ-4", track="state_of_art", question="q", purpose="p", max_tool_calls=5
    )

    dossier = await service.research(
        brief=brief, instructions="Be rigorous.", max_duration_seconds=-1
    )

    assert dossier.status == "incomplete"
    assert dossier.citations == []
    assert llm.chat_with_tools_calls == []  # never reached: deadline was already past
    assert len(llm.complete_calls) == 1  # synthesis still runs with whatever was gathered
    assert web_search.calls == []


async def test_bounded_tool_research_service_stops_on_actual_cost_cap():
    # A single response with an implausibly huge output_tokens count breaches
    # the $15/call cap once CostLedger prices it — the loop must stop
    # gathering right there rather than feeding the expensive turn back in
    # for more (possibly even more expensive) rounds.
    llm = StubLLM(
        tool_responses=[
            LLMToolUseResponse(
                text=None,
                tool_calls=[ToolCall(id="t1", name="web_search", arguments={"query": "x"})],
                model="gpt-5.6-sol",
                input_tokens=100,
                output_tokens=1_000_000,  # gpt-5.6-sol: $0.030/1K -> $30, over cap
            ),
        ],
        completion=LLMResponse(
            text="Forced summary after cost cap breach.",
            model="gpt-5.6-sol",
            input_tokens=10,
            output_tokens=10,
        ),
    )
    web_search = StubWebSearch(
        WebSearchResponse(
            query="x", requested_provider="auto", actual_provider="tavily", results=[]
        )
    )
    service = BoundedToolResearchService(llm=llm, web_search=web_search)
    brief = ResearchBrief(
        brief_id="RQ-5", track="state_of_art", question="q", purpose="p", max_tool_calls=10
    )

    dossier = await service.research(
        brief=brief, instructions="Be rigorous.", max_cost_per_call_usd=15.0
    )

    assert dossier.status == "incomplete"
    assert "Forced summary" in dossier.report_markdown
    assert len(llm.chat_with_tools_calls) == 1  # stopped right after the expensive call
    assert web_search.calls == []  # never got to executing the requested search


async def test_bounded_tool_research_service_skips_synthesis_when_projected_cost_too_high():
    # A vanishingly small cap fails the pre-call projected-cost estimate
    # immediately, before the very first chat_with_tools call — proving the
    # guard prevents an over-cap call from ever being made, not just reacts
    # after the fact — and the synthesis call is skipped the same way.
    llm = StubLLM(
        tool_responses=[
            LLMToolUseResponse(
                text="done", tool_calls=[], model="gpt-5.6-sol", input_tokens=5, output_tokens=5
            ),
        ],
        completion=LLMResponse(
            text="Should never be returned.", model="gpt-5.6-sol", input_tokens=1, output_tokens=1
        ),
    )
    web_search = StubWebSearch(
        WebSearchResponse(
            query="x", requested_provider="auto", actual_provider="tavily", results=[]
        )
    )
    service = BoundedToolResearchService(llm=llm, web_search=web_search)
    brief = ResearchBrief(
        brief_id="RQ-6", track="state_of_art", question="q", purpose="p", max_tool_calls=2
    )

    dossier = await service.research(
        brief=brief, instructions="Be rigorous.", max_cost_per_call_usd=0.0000001
    )

    assert dossier.status == "incomplete"
    assert "Synthesis skipped" in dossier.report_markdown
    assert len(llm.chat_with_tools_calls) == 0  # gather call itself was never made
    assert len(llm.complete_calls) == 0


async def test_bounded_tool_research_service_forces_summary_when_iterations_exhausted():
    # early_stopping_method="generate": every turn keeps requesting another
    # search, so max_iterations runs out before a final answer — the brief
    # must still hand back a forced-summary dossier, not raise.
    tool_responses = [
        LLMToolUseResponse(
            text=None,
            tool_calls=[ToolCall(id=f"t{i}", name="web_search", arguments={"query": "x"})],
            model="gpt-5.6-sol",
            input_tokens=5,
            output_tokens=5,
        )
        for i in range(3)
    ]
    llm = StubLLM(
        tool_responses=tool_responses,
        completion=LLMResponse(
            text="Forced summary from partial results.",
            model="gpt-5.6-sol",
            input_tokens=10,
            output_tokens=10,
        ),
    )
    web_search = StubWebSearch(
        WebSearchResponse(
            query="x", requested_provider="auto", actual_provider="tavily", results=[]
        )
    )
    service = BoundedToolResearchService(llm=llm, web_search=web_search)
    brief = ResearchBrief(
        brief_id="RQ-3", track="state_of_art", question="q", purpose="p", max_tool_calls=10
    )

    dossier = await service.research(
        brief=brief, instructions="Be rigorous.", max_iterations=3
    )

    assert dossier.status == "incomplete"
    assert "Forced summary" in dossier.report_markdown
    assert len(llm.chat_with_tools_calls) == 3  # capped at max_iterations
    assert len(llm.complete_calls) == 1


async def test_bounded_deep_research_agent_end_to_end_with_new_service():
    llm = StubLLM(
        tool_responses=[
            LLMToolUseResponse(
                text=None,
                tool_calls=[ToolCall(id="t1", name="web_search", arguments={"query": "q"})],
                model="gpt-5.6-sol",
                input_tokens=5,
                output_tokens=5,
            ),
            LLMToolUseResponse(
                text="done", tool_calls=[], model="gpt-5.6-sol", input_tokens=3, output_tokens=3
            ),
        ],
        completion=LLMResponse(
            text="Dossier text.", model="gpt-5.6-sol", input_tokens=10, output_tokens=10
        ),
    )
    web_search = StubWebSearch(
        WebSearchResponse(
            query="q",
            requested_provider="auto",
            actual_provider="tavily",
            results=[
                WebResult(
                    title="Doc",
                    url="https://arxiv.org/abs/1234",
                    snippet="evidence",
                    score=0.5,
                )
            ],
        )
    )
    service = BoundedToolResearchService(llm=llm, web_search=web_search)
    catalog = FakeSkillCatalog()

    cls = NodeRegistry.get("BoundedDeepResearchAgent")
    brief = {
        "brief_id": "RQ-1",
        "track": "state_of_art",
        "question": "q",
        "purpose": "p",
        "linked_claim_ids": ["CL-1"],
        "max_tool_calls": 2,
    }
    node = cls(
        "deep_research",
        {"research_briefs": [brief]},
        services={"deep_research": service, "scientific_skill_catalog": catalog},
    )
    result = await node.run({}, node.config.model_dump())

    assert result["jobs_completed"] == 1
    assert result["jobs_failed"] == 0
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["claim_id"] == "CL-1"
    assert "enable_code_interpreter" not in node.config.model_dump()
    assert "background" not in node.config.model_dump()
