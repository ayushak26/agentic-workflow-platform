"""Authenticated APIs for owner-scoped private Business Chat workflows."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
import yaml

from app.api.workflow_generation import (
    GenerateWorkflowRequest,
    generate_workflow_endpoint,
)
from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import preflight_workflow_yaml
from app.security.dependencies import CurrentUser, require_consultant
from app.workflow.chat_workflow_store import (
    ChatWorkflowConflictError,
    ChatWorkflowNotFoundError,
    ChatWorkflowRecord,
    ChatWorkflowStore,
)
from app.workflow.chat_output_compatibility import analyze_chat_output


router = APIRouter(prefix="/api/chat-workflows", tags=["chat-workflows"])
WORKFLOWS_DIR = Path("workflows")
_SAFE_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")
_DEEP_RESEARCH_SLUG = "deep-research-chat"
_DEEP_RESEARCH_YAML = """
name: Deep Research Chat
description: Run bounded web and research-paper discovery, acquire lawful full-text sources, and return a cited research report.
version: '1.0'
library:
  title: Deep Research
  summary: Research a question across web pages and research papers with bounded budgets and downloadable acquired PDFs.
  visibility_status: draft
entry: start
exit: end
nodes:
  - id: start
    type: StartAgent
    config:
      mode: chatbot
      chatbot_name: Deep Research
      welcome_message: Ask a research question. I will search the web and research literature under bounded budgets.
      message_placeholder: What should I research?
  - id: deep_research
    type: BoundedDeepResearchAgent
    config:
      research_briefs:
        - brief_id: CHAT-RESEARCH-1
          track: state_of_art
          question: '{{outputs.start.message}}'
          purpose: Answer the user's research question with supporting and contradictory sources.
          linked_claim_ids: [CHAT-QUERY]
          required_source_types: [peer-reviewed papers, official sources, credible web pages]
          geographic_scope: [Global]
          date_priority: 2021-present
          must_find: [supporting evidence, contradictory evidence, limitations]
          selected_skills: []
          tier: standard
          research_model: gpt-5.6-sol
          max_tool_calls: 10
      max_jobs: 1
      max_parallel_jobs: 1
      max_total_tool_calls: 10
      max_tool_calls_per_job: 10
      max_citations_per_brief: 20
      max_candidates_per_claim: 12
      max_duration_seconds: 900
      max_iterations: 10
  - id: acquire_sources
    type: ResearchSourceAcquirer
    config:
      candidates: '{{outputs.deep_research.candidates}}'
      max_concurrent_requests: 4
      max_sources_per_claim: 7
      max_total_sources: 12
      request_timeout_seconds: 45
      fail_when_none_acquired: false
  - id: synthesize
    type: TransformAgent
    config:
      mode: ai
      model: gpt-5.6-sol
      system_prompt: Treat dossiers as candidate research, not verified evidence. Never invent citations. Preserve source distinctions and clearly state uncertainty.
      prompt_template: |
        Answer this research question:
        {{outputs.start.message}}

        Use only these bounded research dossiers:
        {{outputs.deep_research.dossiers}}

        Write a concise research report. Cite sources with [N] markers in the order they appear in the dossiers. Distinguish research papers, official sources, and general web pages. Include disagreements and limitations.
      output_schema:
        answer: str
  - id: end
    type: EndAgent
    config:
      mode: chat_response
      chat_message: '{{outputs.synthesize.parsed.answer}}'
      outcome: research_complete
edges:
  - from: start
    to: deep_research
  - from: deep_research
    to: acquire_sources
  - from: acquire_sources
    to: synthesize
  - from: synthesize
    to: end
"""


def _scope(user: CurrentUser) -> str:
    return getattr(user, "session_id", None) or user.username


def _store(request: Request) -> ChatWorkflowStore:
    db = getattr(request.app.state, "services", {}).get("audit_db")
    if db is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Private Chat workflow storage is unavailable",
        )
    return ChatWorkflowStore(db)


def _slug(value: str) -> str:
    value = value.strip()
    if not _SAFE_SLUG.fullmatch(value):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "slug must contain only letters, numbers, underscores, and hyphens",
        )
    return value


def _private_yaml(yaml_text: str) -> str:
    """Force private lifecycle metadata without changing executable semantics."""
    try:
        document = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if not isinstance(document, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Workflow YAML must be a mapping")
    library = document.get("library")
    if not isinstance(library, dict):
        library = {}
        document["library"] = library
    library["visibility_status"] = "draft"
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def _validate(yaml_text: str):
    report = preflight_workflow_yaml(yaml_text, compile_graph=True)
    if not report.valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "The workflow is not valid for private Chat execution",
                "preflight": report.model_dump(mode="json"),
            },
        )
    return load_workflow_from_string(yaml_text)


def _summary(record: ChatWorkflowRecord) -> dict[str, Any]:
    return record.public_summary()


class ImportChatWorkflowRequest(BaseModel):
    slug: str
    display_name: str = Field(min_length=1, max_length=160)
    yaml: str = Field(min_length=1)


class CopyChatWorkflowRequest(BaseModel):
    workflow_name: str
    slug: str
    display_name: str = Field(min_length=1, max_length=160)


class GenerateChatWorkflowRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    slug: str
    display_name: str = Field(min_length=1, max_length=160)
    preferred_output_type: Literal[
        "auto", "text", "code", "image", "pdf", "docx", "pptx", "xlsx",
    ] = "auto"
    sample_inputs: dict[str, Any] | None = None


async def _save(
    request: Request,
    user: CurrentUser,
    *,
    slug: str,
    display_name: str,
    yaml_text: str,
    source: Literal["generated", "imported", "existing"],
    source_workflow_name: str | None = None,
) -> ChatWorkflowRecord:
    safe_slug = _slug(slug)
    private_yaml = _private_yaml(yaml_text)
    spec = _validate(private_yaml)
    compatibility = analyze_chat_output(spec)
    if not compatibility.supported:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "The workflow does not produce a meaningful user-visible Chat result",
                "output_compatibility": compatibility.model_dump(mode="json"),
            },
        )
    try:
        return await _store(request).create(
            owner_scope_id=_scope(user),
            slug=safe_slug,
            display_name=display_name.strip(),
            description=spec.description or "",
            yaml_text=private_yaml,
            source=source,
            source_workflow_name=source_workflow_name,
            output_compatibility=compatibility.model_dump(mode="json"),
        )
    except ChatWorkflowConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("")
async def list_private_chat_workflows(
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    records = await _store(request).list_private(_scope(user))
    return {"workflows": [_summary(record) for record in records]}


@router.post("/presets/deep-research", status_code=status.HTTP_201_CREATED)
async def ensure_deep_research_chat_workflow(
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Return the owner's vetted Deep Research Chat workflow, creating it once."""
    store = _store(request)
    existing = await store.get_by_slug(_scope(user), _DEEP_RESEARCH_SLUG)
    if existing is not None:
        return _summary(existing)
    return _summary(await _save(
        request,
        user,
        slug=_DEEP_RESEARCH_SLUG,
        display_name="Deep Research",
        yaml_text=_DEEP_RESEARCH_YAML,
        source="imported",
    ))


@router.get("/{chat_workflow_id}")
async def get_private_chat_workflow(
    chat_workflow_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    try:
        record = await _store(request).get(_scope(user), chat_workflow_id)
    except ChatWorkflowNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {**_summary(record), "yaml": record.yaml}


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_private_chat_workflow(
    body: ImportChatWorkflowRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    return _summary(await _save(
        request, user,
        slug=body.slug,
        display_name=body.display_name,
        yaml_text=body.yaml,
        source="imported",
    ))


@router.post("/from-existing", status_code=status.HTTP_201_CREATED)
async def copy_existing_chat_workflow(
    body: CopyChatWorkflowRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    if not _SAFE_SLUG.fullmatch(body.workflow_name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    path = WORKFLOWS_DIR / f"{body.workflow_name}.yaml"
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    return _summary(await _save(
        request, user,
        slug=body.slug,
        display_name=body.display_name,
        yaml_text=path.read_text(encoding="utf-8"),
        source="existing",
        source_workflow_name=body.workflow_name,
    ))


@router.post("/generate", status_code=status.HTTP_201_CREATED)
async def generate_private_chat_workflow(
    body: GenerateChatWorkflowRequest,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    output_instruction = (
        "Choose the best user-visible output type."
        if body.preferred_output_type == "auto"
        else f"The primary user-visible output must be {body.preferred_output_type}."
    )
    prompt = (
        f"Create a workflow intended for conversational Business Chat. {output_instruction} "
        "The final result must be meaningful to the user and the workflow must remain a draft.\n\n"
        f"User request: {body.prompt.strip()}"
    )
    generated = await generate_workflow_endpoint(
        GenerateWorkflowRequest(prompt=prompt, sample_inputs=body.sample_inputs),
        request,
        user,
    )
    if not generated.get("success") or not generated.get("yaml"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "The workflow could not be generated successfully",
                "generation": generated,
            },
        )
    return _summary(await _save(
        request, user,
        slug=body.slug,
        display_name=body.display_name,
        yaml_text=generated["yaml"],
        source="generated",
    ))


@router.delete("/{chat_workflow_id}")
async def archive_private_chat_workflow(
    chat_workflow_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    archived = await _store(request).archive(_scope(user), chat_workflow_id)
    if not archived:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Private Chat workflow not found")
    return {"id": chat_workflow_id, "archived": True}


@router.post("/{chat_workflow_id}/request-publication")
async def request_private_workflow_publication(
    chat_workflow_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    try:
        record = await _store(request).request_publication(
            _scope(user), chat_workflow_id,
        )
    except ChatWorkflowNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _summary(record)