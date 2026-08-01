"""Bounded Deep Research contracts and provider adapter.

The proposal workflow deliberately treats a Deep Research response as a
candidate research dossier, never as verified proposal evidence.  A later
source-acquisition and claim-verification stage must resolve every material
citation against immutable full text before a claim can enter the proposal
truth graph.

This runs as a bounded, in-process tool-calling loop against the generic
``llm`` gateway (``chat_with_tools``) plus the platform's ``web_search``
service — not OpenAI's dedicated async Deep Research product (Responses API
background jobs with a built-in ``web_search_preview`` tool). That product
surface required a model registered under a distinct ``kind="deep_research"``
catalog entry (only ``o3-deep-research``/``o4-mini-deep-research`` ever were);
running the gather loop ourselves lets any chat model — ``gpt-5.6-sol`` or
``claude-fable-5`` — do deep research.
"""
from __future__ import annotations

import json
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.evidence.retrieval import stable_id
from app.observability.cost_ledger import CostLedger
from app.observability.logging import get_logger

log = get_logger(__name__)

# ~4 characters per token is the same rough pre-call estimate used by
# app.llm.registry._estimate_input_tokens for deterministic model routing.
_CHARS_PER_TOKEN_ESTIMATE = 4
_MAX_TURN_TOKENS = 4096


def _projected_call_cost(model: str, prompt_text: str, max_tokens: int) -> float:
    """Worst-case pre-call cost: rough input-token estimate, full max_tokens output."""
    estimated_input_tokens = max(0, len(prompt_text) // _CHARS_PER_TOKEN_ESTIMATE)
    return CostLedger.calculate(model, estimated_input_tokens, max_tokens)


ResearchTrack = Literal[
    "state_of_art",
    "eu_policy_and_regulation",
    "prior_projects_and_synergies",
    "methodology_selection",
    "market_adoption_and_social",
    "environment_climate_biodiversity",
    "impact_baselines_and_targets",
    "risks_contradictions_and_failure_conditions",
]
ResearchTier = Literal["standard", "critical"]
ResearchModel = Literal["gpt-5.6-sol", "claude-fable-5"]


class ResearchBrief(BaseModel):
    brief_id: str
    track: ResearchTrack
    question: str
    purpose: str
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_call_requirement_ids: list[str] = Field(default_factory=list)
    required_source_types: list[str] = Field(default_factory=list)
    geographic_scope: list[str] = Field(default_factory=lambda: ["European Union"])
    date_priority: str = "2021-present"
    must_find: list[str] = Field(default_factory=list)
    selected_skills: list[str] = Field(default_factory=list)
    tier: ResearchTier = "standard"
    research_model: ResearchModel = "gpt-5.6-sol"
    max_tool_calls: int = Field(default=8, ge=2, le=40)


class ResearchCitation(BaseModel):
    citation_id: str
    title: str
    url: str
    start_index: int | None = None
    end_index: int | None = None
    cited_text: str = ""


class ResearchToolTrace(BaseModel):
    type: str
    action: str = ""


class ResearchUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tool_calls: int = 0


class ResearchDossier(BaseModel):
    brief_id: str
    track: ResearchTrack
    question: str
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_call_requirement_ids: list[str] = Field(default_factory=list)
    model: str
    response_id: str
    status: str
    report_markdown: str
    max_tool_calls_budget: int = 0
    citations: list[ResearchCitation] = Field(default_factory=list)
    tool_trace: list[ResearchToolTrace] = Field(default_factory=list)
    usage: ResearchUsage = Field(default_factory=ResearchUsage)
    skills_used: list[str] = Field(default_factory=list)
    skill_versions: dict[str, str] = Field(default_factory=dict)


_WEB_SEARCH_TOOL: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Search the public web for a query and return candidate results "
        "(title, url, snippet). Results are search candidates, not verified "
        "evidence — cite them but do not treat them as ground truth."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    },
}


class BoundedToolResearchService:
    """Bounded, in-process replacement for the OpenAI Deep Research job API.

    Each brief runs two phases:
      1. A bounded ``chat_with_tools`` gather loop (at most ``max_iterations``
         sequential turns, each optionally issuing ``web_search`` calls up to
         ``brief.max_tool_calls``) collects candidate citations.
      2. One dedicated ``complete`` call, at the model's highest declared
         reasoning effort, synthesizes the dossier from what was gathered.
         Tool-calling turns intentionally run cheap (providers such as
         gpt-5.6 require ``reasoning_effort="none"`` when tools are bound —
         see ``app.llm.openai_gw._chat_tool_reasoning_effort``); routing the
         actual synthesis through a tool-free call is what lets it run at
         full reasoning effort.

    Early stopping always forces this synthesis call rather than failing the
    brief outright — whether the gather loop is cut short by
    ``max_duration_seconds``, by exhausting ``max_iterations``, or by a
    single call's cost (via ``CostLedger.calculate``) breaching
    ``max_cost_per_call_usd``, the dossier's ``status`` becomes
    ``"incomplete"`` and the model is asked to generate its best summary
    from whatever was gathered so far.
    """

    def __init__(self, *, llm: Any, web_search: Any) -> None:
        self._llm = llm
        self._web_search = web_search

    def available(self) -> bool:
        return self._llm is not None and self._web_search is not None

    async def research(
        self,
        *,
        brief: ResearchBrief,
        instructions: str,
        max_duration_seconds: float = 1800.0,
        max_iterations: int = 15,
        max_cost_per_call_usd: float = 15.0,
    ) -> ResearchDossier:
        if not self.available():
            raise RuntimeError("Bounded research service is not configured")

        deadline = time.monotonic() + max_duration_seconds
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": _brief_prompt(brief)}
        ]
        citations: list[ResearchCitation] = []
        seen_urls: set[str] = set()
        tool_trace: list[ResearchToolTrace] = []
        searches_used = 0
        input_tokens = 0
        output_tokens = 0
        final_text: str | None = None
        # Assume an early stop unless the loop reaches a genuine final
        # answer below — covers the time budget, the iteration cap, and the
        # per-call cost cap.
        stopped_early = True

        for _ in range(max_iterations):
            if time.monotonic() >= deadline:
                break
            prompt_text = instructions + " ".join(
                str(m.get("content") or "") for m in messages
            )
            projected_cost = _projected_call_cost(
                brief.research_model, prompt_text, _MAX_TURN_TOKENS
            )
            if projected_cost > max_cost_per_call_usd:
                log.warning(
                    "deep_research.cost_cap_projected",
                    brief_id=brief.brief_id,
                    model=brief.research_model,
                    projected_usd=round(projected_cost, 4),
                    cap_usd=max_cost_per_call_usd,
                )
                break
            tools = (
                [_WEB_SEARCH_TOOL] if searches_used < brief.max_tool_calls else []
            )
            response = await self._llm.chat_with_tools(
                model=brief.research_model,
                system=instructions,
                messages=messages,
                tools=tools,
                temperature=0.2,
                max_tokens=_MAX_TURN_TOKENS,
            )
            input_tokens += response.input_tokens
            output_tokens += response.output_tokens
            actual_cost = CostLedger.calculate(
                response.model, response.input_tokens, response.output_tokens
            )
            if actual_cost > max_cost_per_call_usd:
                log.warning(
                    "deep_research.cost_cap_actual",
                    brief_id=brief.brief_id,
                    model=response.model,
                    actual_usd=round(actual_cost, 4),
                    cap_usd=max_cost_per_call_usd,
                )
            if not response.tool_calls:
                # A genuine final answer is worth keeping even if this last
                # call happened to breach the cap — the money's spent either
                # way, and discarding a finished answer buys nothing back.
                final_text = response.text or ""
                stopped_early = False
                break
            if actual_cost > max_cost_per_call_usd:
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": response.text or "",
                    "reasoning_content": response.reasoning_content,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                }
            )
            deadline_hit = False
            for tc in response.tool_calls:
                if time.monotonic() >= deadline:
                    deadline_hit = True
                    break
                query = str(tc.arguments.get("query") or "").strip()
                top_k = int(tc.arguments.get("top_k") or 8)
                if not query or searches_used >= brief.max_tool_calls:
                    result_text = "Search skipped: tool-call budget exhausted."
                else:
                    searches_used += 1
                    search_response = await self._web_search.search(
                        query, top_k=top_k
                    )
                    tool_trace.append(
                        ResearchToolTrace(type="web_search_call", action=query)
                    )
                    for item in search_response.results:
                        if item.url in seen_urls:
                            continue
                        seen_urls.add(item.url)
                        citations.append(
                            ResearchCitation(
                                citation_id=f"DR-CIT-{len(citations) + 1:04d}",
                                title=item.title,
                                url=item.url,
                                cited_text=item.snippet,
                            )
                        )
                    result_text = json.dumps(
                        [
                            {"title": r.title, "url": r.url, "snippet": r.snippet}
                            for r in search_response.results
                        ]
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                )
            if deadline_hit:
                break

        # early_stopping_method="generate": whether cut short by the time
        # budget, the iteration cap, or the per-call cost cap, force one
        # last synthesis call from whatever was gathered rather than failing
        # the brief or blocking the rest of the run. BoundedDeepResearchAgent
        # runs briefs concurrently under a semaphore, so one brief stopping
        # early here simply frees its slot for the next queued brief.
        synthesis_user_prompt = (
            _brief_prompt(brief)
            + "\n\nWEB SEARCH RESULTS GATHERED SO FAR:\n"
            + (
                "\n".join(
                    f"- {c.title} ({c.url}): {c.cited_text}" for c in citations
                )
                or "(none — no search returned usable results)"
            )
            + "\n\nAGENT'S OWN NOTES FROM THE GATHER PHASE:\n"
            + (final_text or "")
            + (
                "\n\nNOTE: the gather phase was stopped early (time budget, "
                "iteration cap, or cost cap) before the model finished "
                "searching — synthesize from whatever was gathered above "
                "rather than waiting for more."
                if stopped_early
                else ""
            )
            + "\n\nWrite the analytical dossier now, grounded only in the "
            "results above."
        )
        projected_synthesis_cost = _projected_call_cost(
            brief.research_model,
            instructions + synthesis_user_prompt,
            _MAX_TURN_TOKENS,
        )
        if projected_synthesis_cost > max_cost_per_call_usd:
            log.warning(
                "deep_research.cost_cap_projected_synthesis",
                brief_id=brief.brief_id,
                model=brief.research_model,
                projected_usd=round(projected_synthesis_cost, 4),
                cap_usd=max_cost_per_call_usd,
            )
            stopped_early = True
            report_markdown = (
                f"[Synthesis skipped: projected cost exceeded the "
                f"${max_cost_per_call_usd:.2f} per-call cap.]\n\n"
                + (final_text or "(no gather-phase notes available)")
            )
        else:
            synthesis = await self._llm.complete(
                model=brief.research_model,
                system=instructions,
                user=synthesis_user_prompt,
                temperature=0.0,
                max_tokens=_MAX_TURN_TOKENS,
                reasoning_effort="xhigh",
            )
            input_tokens += synthesis.input_tokens
            output_tokens += synthesis.output_tokens
            actual_synthesis_cost = CostLedger.calculate(
                synthesis.model, synthesis.input_tokens, synthesis.output_tokens
            )
            if actual_synthesis_cost > max_cost_per_call_usd:
                log.warning(
                    "deep_research.cost_cap_actual_synthesis",
                    brief_id=brief.brief_id,
                    model=synthesis.model,
                    actual_usd=round(actual_synthesis_cost, 4),
                    cap_usd=max_cost_per_call_usd,
                )
                stopped_early = True
            report_markdown = synthesis.text

        return ResearchDossier(
            brief_id=brief.brief_id,
            track=brief.track,
            question=brief.question,
            linked_claim_ids=brief.linked_claim_ids,
            linked_call_requirement_ids=brief.linked_call_requirement_ids,
            model=brief.research_model,
            response_id=stable_id("RESP", brief.brief_id, str(searches_used)),
            status="incomplete" if stopped_early else "completed",
            report_markdown=report_markdown,
            max_tool_calls_budget=brief.max_tool_calls,
            citations=citations,
            tool_trace=tool_trace,
            usage=ResearchUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                tool_calls=searches_used,
            ),
            skills_used=brief.selected_skills,
        )


def _brief_prompt(brief: ResearchBrief) -> str:
    return (
        f"RESEARCH QUESTION ID: {brief.brief_id}\n"
        f"TRACK: {brief.track}\n"
        f"QUESTION: {brief.question}\n"
        f"PURPOSE: {brief.purpose}\n"
        "REQUIRED SOURCE TYPES: "
        + ", ".join(brief.required_source_types)
        + "\nGEOGRAPHIC SCOPE: "
        + ", ".join(brief.geographic_scope)
        + f"\nDATE PRIORITY: {brief.date_priority}\n"
        "MUST FIND:\n- "
        + "\n- ".join(brief.must_find)
        + "\n\nUse the web_search tool to gather sources, then return an "
        "analytical dossier with explicit sections for: answer and "
        "synthesis; supporting evidence; contradictory, null, or "
        "boundary-condition evidence; methodological limitations; EU "
        "relevance; source list; and unresolved questions. Cite every "
        "material factual statement with the URL it came from. Do not "
        "recommend changes to proposal objectives, KPIs, work packages, "
        "partners, or budgets. Those decisions belong to the governed "
        "proposal workflow."
    )


def get_deep_research_service(
    *, llm: Any, web_search: Any
) -> BoundedToolResearchService:
    return BoundedToolResearchService(llm=llm, web_search=web_search)
