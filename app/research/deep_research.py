"""Bounded OpenAI Deep Research contracts and provider adapter.

The proposal workflow deliberately treats a Deep Research response as a
candidate research dossier, never as verified proposal evidence.  A later
source-acquisition and claim-verification stage must resolve every material
citation against immutable full text before a claim can enter the proposal
truth graph.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


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
    research_model: Literal[
        "o3-deep-research",
        "o4-mini-deep-research",
        "o4-mini-deep-research-2025-06-26",
    ] = "o4-mini-deep-research"
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


@dataclass(frozen=True)
class _ProviderResult:
    response_id: str
    status: str
    output_text: str
    payload: dict[str, Any]


class OpenAIDeepResearchService:
    """Small, mockable adapter around the Responses API.

    Long-running requests use background mode and are polled inside the
    workflow node.  This keeps pipeline checkpoint semantics simple while
    still following the provider's recommended execution pattern.
    """

    _TERMINAL = {"completed", "failed", "cancelled", "incomplete"}

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 3600.0,
        poll_interval_seconds: float = 2.0,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._client = client or (
            AsyncOpenAI(
                api_key=self.api_key,
                max_retries=0,
                timeout=timeout_seconds,
            )
            if self.api_key
            else None
        )

    def available(self) -> bool:
        return bool(self.api_key and self._client is not None)

    async def probe_model_access(self, model: str) -> str:
        if not self.available():
            raise RuntimeError("OPENAI_API_KEY is not configured")
        result = await self._client.models.retrieve(model)
        return str(result.id)

    async def research(
        self,
        *,
        brief: ResearchBrief,
        instructions: str,
        enable_code_interpreter: bool = False,
        background: bool = True,
    ) -> ResearchDossier:
        if not self.available():
            raise RuntimeError("OpenAI Deep Research is not configured")

        tools: list[dict[str, Any]] = [{"type": "web_search_preview"}]
        if enable_code_interpreter:
            tools.append(
                {
                    "type": "code_interpreter",
                    "container": {"type": "auto"},
                }
            )

        async def _execute() -> _ProviderResult:
            response = await self._client.responses.create(
                model=brief.research_model,
                instructions=instructions,
                input=_brief_prompt(brief),
                background=background,
                tools=tools,
                max_tool_calls=brief.max_tool_calls,
                reasoning={"summary": "auto"},
            )
            while str(getattr(response, "status", "")) not in self._TERMINAL:
                await asyncio.sleep(self.poll_interval_seconds)
                response = await self._client.responses.retrieve(response.id)
            payload = _response_payload(response)
            return _ProviderResult(
                response_id=str(getattr(response, "id", "")),
                status=str(getattr(response, "status", "")),
                output_text=str(getattr(response, "output_text", "") or ""),
                payload=payload,
            )

        provider = await asyncio.wait_for(
            _execute(),
            timeout=self.timeout_seconds,
        )
        if provider.status != "completed":
            error = provider.payload.get("error") or provider.payload.get(
                "incomplete_details"
            )
            raise RuntimeError(
                f"Deep Research {provider.response_id or '(unknown)'} ended "
                f"with status {provider.status!r}: {error or 'no details'}"
            )

        citations = _extract_citations(
            provider.payload,
            provider.output_text,
        )
        tool_trace = _extract_tool_trace(provider.payload)
        usage = _extract_usage(provider.payload, len(tool_trace))
        return ResearchDossier(
            brief_id=brief.brief_id,
            track=brief.track,
            question=brief.question,
            linked_claim_ids=brief.linked_claim_ids,
            linked_call_requirement_ids=brief.linked_call_requirement_ids,
            model=brief.research_model,
            response_id=provider.response_id,
            status=provider.status,
            report_markdown=provider.output_text,
            max_tool_calls_budget=brief.max_tool_calls,
            citations=citations,
            tool_trace=tool_trace,
            usage=usage,
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
        + "\n\nReturn an analytical dossier with explicit sections for: "
        "answer and synthesis; supporting evidence; contradictory, null, or "
        "boundary-condition evidence; methodological limitations; EU "
        "relevance; source list; and unresolved questions. Use inline URL "
        "citations for every material factual statement. Do not recommend "
        "changes to proposal objectives, KPIs, work packages, partners, or "
        "budgets. Those decisions belong to the governed proposal workflow."
    )


def _response_payload(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        dumped = response.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    if isinstance(response, dict):
        return response
    return {}


def _extract_citations(
    payload: dict[str, Any],
    output_text: str,
) -> list[ResearchCitation]:
    citations: list[ResearchCitation] = []
    seen: set[str] = set()
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            for annotation in content.get("annotations") or []:
                if not isinstance(annotation, dict):
                    continue
                if annotation.get("type") != "url_citation":
                    continue
                url = str(annotation.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                start = annotation.get("start_index")
                end = annotation.get("end_index")
                cited_text = ""
                if isinstance(start, int) and isinstance(end, int):
                    cited_text = output_text[max(0, start) : max(start, end)]
                citations.append(
                    ResearchCitation(
                        citation_id=f"DR-CIT-{len(citations) + 1:04d}",
                        title=str(annotation.get("title") or url),
                        url=url,
                        start_index=start if isinstance(start, int) else None,
                        end_index=end if isinstance(end, int) else None,
                        cited_text=cited_text.strip(),
                    )
                )
    return citations


def _extract_tool_trace(
    payload: dict[str, Any],
) -> list[ResearchToolTrace]:
    trace: list[ResearchToolTrace] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if not item_type.endswith("_call"):
            continue
        action = item.get("action")
        if isinstance(action, dict):
            action_name = str(action.get("type") or action.get("name") or "")
        else:
            action_name = str(action or "")
        trace.append(ResearchToolTrace(type=item_type, action=action_name))
    return trace


def _extract_usage(
    payload: dict[str, Any],
    tool_calls: int,
) -> ResearchUsage:
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    return ResearchUsage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        tool_calls=tool_calls,
    )


def get_deep_research_service() -> OpenAIDeepResearchService:
    from app.config import settings

    return OpenAIDeepResearchService(
        api_key=settings.openai_api_key,
        timeout_seconds=settings.deep_research_timeout_seconds,
        poll_interval_seconds=settings.deep_research_poll_interval_seconds,
    )
