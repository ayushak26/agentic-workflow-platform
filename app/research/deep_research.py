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
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.evidence.retrieval import stable_id


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
      1. A bounded ``chat_with_tools`` gather loop (up to
         ``brief.max_tool_calls`` ``web_search`` calls) collects candidate
         citations.
      2. One dedicated ``complete`` call, at the model's highest declared
         reasoning effort, synthesizes the dossier from what was gathered.
         Tool-calling turns intentionally run cheap (providers such as
         gpt-5.6 require ``reasoning_effort="none"`` when tools are bound —
         see ``app.llm.openai_gw._chat_tool_reasoning_effort``); routing the
         actual synthesis through a tool-free call is what lets it run at
         full reasoning effort.
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
    ) -> ResearchDossier:
        if not self.available():
            raise RuntimeError("Bounded research service is not configured")

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

        for _ in range(brief.max_tool_calls + 1):
            tools = (
                [_WEB_SEARCH_TOOL] if searches_used < brief.max_tool_calls else []
            )
            response = await self._llm.chat_with_tools(
                model=brief.research_model,
                system=instructions,
                messages=messages,
                tools=tools,
                temperature=0.2,
            )
            input_tokens += response.input_tokens
            output_tokens += response.output_tokens
            if not response.tool_calls:
                final_text = response.text or ""
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
            for tc in response.tool_calls:
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
        else:
            raise RuntimeError(
                f"Deep Research brief {brief.brief_id} exhausted its gather "
                "loop without a final answer."
            )

        synthesis = await self._llm.complete(
            model=brief.research_model,
            system=instructions,
            user=(
                _brief_prompt(brief)
                + "\n\nWEB SEARCH RESULTS GATHERED SO FAR:\n"
                + (
                    "\n".join(
                        f"- {c.title} ({c.url}): {c.cited_text}"
                        for c in citations
                    )
                    or "(none — no search returned usable results)"
                )
                + "\n\nAGENT'S OWN NOTES FROM THE GATHER PHASE:\n"
                + (final_text or "")
                + "\n\nWrite the analytical dossier now, grounded only in the "
                "results above."
            ),
            temperature=0.0,
            max_tokens=4096,
            reasoning_effort="xhigh",
        )
        input_tokens += synthesis.input_tokens
        output_tokens += synthesis.output_tokens

        return ResearchDossier(
            brief_id=brief.brief_id,
            track=brief.track,
            question=brief.question,
            linked_claim_ids=brief.linked_claim_ids,
            linked_call_requirement_ids=brief.linked_call_requirement_ids,
            model=brief.research_model,
            response_id=stable_id("RESP", brief.brief_id, str(searches_used)),
            status="completed",
            report_markdown=synthesis.text,
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
