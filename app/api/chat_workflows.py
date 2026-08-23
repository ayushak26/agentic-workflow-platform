"""Authenticated APIs for owner-scoped private Business Chat workflows."""
from __future__ import annotations

from pathlib import Path
import hashlib
import re
from datetime import datetime, timezone
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
from app.workflow.chat_workspace_planner import build_existing_workflow_chat_adapter, build_llm_adapter


router = APIRouter(prefix="/api/chat-workflows", tags=["chat-workflows"])
WORKFLOWS_DIR = Path("workflows")
_SAFE_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")
_DEEP_RESEARCH_SLUG = "deep-research-chat"
_GENERAL_CHAT_SLUG = "general-chat"
_DEEP_RESEARCH_YAML = """
name: Deep Research Chat
description: Combine complex bounded research, direct web search, scholarly MCP search, and supplied files into one cited answer.
version: '2.0'
inputs:
  web_source_urls:
    type: json
    description: Optional selected web-page URLs to prioritize as source constraints.
    required: false
library:
  title: Deep Research
  summary: Research with GPT-5.6 Sol across current web sources, scholarly databases, supplied files, and optional internal Knowledge.
  visibility_status: draft
entry: start
exit: end
nodes:
  - id: start
    type: StartAgent
    config:
      mode: chatbot
      chatbot_name: Deep Research
      welcome_message: Ask a complex question. Add files, Drive documents, web pages, or internal Knowledge and I will research them together.
      message_placeholder: What should I research?
      allow_attachments: true
  - id: load_sources
    type: WorkflowFileLoader
    config:
      files: '{{outputs.start.attachments}}'
      max_chars_per_file: 200000
      fail_on_unreadable: false
    experience:
      display_name: Read Added Sources
      purpose: Extract usable text from uploaded files and Google Drive files selected in Chat.
      contribution: Supplies user-provided documentation to the final cross-source comparison.
      expected_output: Extracted source text and a file-by-file status list.
      running_message: Reading uploaded and Google Drive sources…
      completed_message: Added sources are ready for research.
      failure_message: Added files could not be read; check their format or access and retry.
  - id: web_search
    type: WebSearchAgent
    config:
      query: '{{outputs.start.message}} Selected source URLs to prioritize when relevant: {{inputs.web_source_urls}}'
      provider: auto
      top_k: 15
      fallback_to_openai: true
    experience:
      display_name: Search Current Web
      purpose: Find current public information and official sources relevant to the question.
      contribution: Supplies recent external evidence and developments to the final answer.
      expected_output: Ranked current web-source candidates with URLs and snippets.
      running_message: Searching current web sources…
      completed_message: Current web sources found.
      failure_message: Current web search could not complete; verify the web-search provider and retry.
  - id: paper_search
    type: MCPToolAgent
    config:
      server_id: paper-search-mcp
      tool: search_papers
      arguments:
        query: '{{outputs.start.message}}'
        sources: arxiv,openalex,europepmc,core,openaire,zenodo,hal,doaj,pmc,semantic
        max_results_per_source: 5
      fail_on_error: false
      timeout_seconds: 180
    experience:
      display_name: Search Research Papers
      purpose: Search scholarly databases through the approved paper-search-mcp connection.
      contribution: Supplies research-paper metadata and provider-specific search failures.
      expected_output: Scholarly paper candidates from multiple academic sources.
      running_message: Searching scholarly databases through paper-search-mcp…
      completed_message: Scholarly paper candidates found.
      failure_message: Scholarly search could not complete; check paper-search-mcp readiness and retry.
  - id: deep_research
    type: BoundedDeepResearchAgent
    config:
      research_briefs:
        - brief_id: CHAT-CURRENT
          track: state_of_art
          question: '{{outputs.start.message}} Selected source URLs: {{inputs.web_source_urls}}'
          purpose: Establish the strongest current answer from official and credible public sources.
          linked_claim_ids: [CHAT-QUERY]
          required_source_types: [official sources, credible current web pages]
          geographic_scope: [Global]
          date_priority: 2022-present
          must_find: [current facts, official guidance, recent developments]
          selected_skills: []
          tier: standard
          research_model: gpt-5.6-sol
          max_tool_calls: 10
        - brief_id: CHAT-CHALLENGE
          track: risks_contradictions_and_failure_conditions
          question: '{{outputs.start.message}}'
          purpose: Find contradictions, limitations, risks, and evidence that could change the conclusion.
          linked_claim_ids: [CHAT-QUERY]
          required_source_types: [contradictory studies, critical reviews, official warnings, credible web pages]
          geographic_scope: [Global]
          date_priority: 2018-present
          must_find: [contradictory evidence, limitations, risks, unresolved questions]
          selected_skills: []
          tier: standard
          research_model: gpt-5.6-sol
          max_tool_calls: 10
      max_jobs: 2
      max_parallel_jobs: 2
      max_total_tool_calls: 20
      max_tool_calls_per_job: 10
      max_citations_per_brief: 15
      max_candidates_per_claim: 12
      max_duration_seconds: 1200
      max_iterations: 10
    experience:
      display_name: Run Complex Research
      purpose: Investigate the topic iteratively and actively search for contradictions and limitations.
      contribution: Adds multi-step research dossiers that challenge and qualify simpler search results.
      expected_output: Bounded current-evidence and contradiction research dossiers with source candidates.
      running_message: Investigating evidence, contradictions, and limitations…
      completed_message: Complex research dossiers completed.
      failure_message: Complex research could not complete within the configured service or budget limits.
  - id: acquire_sources
    type: ResearchSourceAcquirer
    config:
      candidates: '{{outputs.deep_research.candidates}}'
      max_concurrent_requests: 6
      max_sources_per_claim: 10
      max_total_sources: 30
      request_timeout_seconds: 45
      fail_when_none_acquired: false
    experience:
      display_name: Acquire Research Sources
      purpose: Resolve lawful immutable source versions for bounded-research candidates when available.
      contribution: Distinguishes acquired source material from metadata-only or snippet-only candidates.
      expected_output: Acquired documents plus explicit rejected-candidate reasons.
      running_message: Acquiring lawful source versions where available…
      completed_message: Research source acquisition completed.
      failure_message: Source acquisition could not complete; candidate metadata remains available for qualified use.
  - id: synthesize
    type: TransformAgent
    selected_model: gpt-5.6-sol
    config:
      mode: ai
      model: gpt-5.6-sol
      temperature: 0.15
      input_fields:
        - name: question
          type: string
          value: '{{outputs.start.message}}'
        - name: added_source_text
          type: string
          value: '{{outputs.load_sources.text}}'
        - name: added_source_files
          type: object
          value: '{{outputs.load_sources.files}}'
        - name: selected_web_pages
          type: object
          value: '{{inputs.web_source_urls}}'
        - name: direct_web_results
          type: object
          value: '{{outputs.web_search.results}}'
        - name: paper_search_status
          type: string
          value: '{{outputs.paper_search.status}}'
        - name: research_papers
          type: object
          value: '{{outputs.paper_search.data}}'
        - name: paper_search_error
          type: string
          value: '{{outputs.paper_search.error}}'
        - name: complex_research_dossiers
          type: object
          value: '{{outputs.deep_research.dossiers}}'
        - name: complex_research_candidates
          type: object
          value: '{{outputs.deep_research.candidates}}'
        - name: acquired_sources
          type: object
          value: '{{outputs.acquire_sources.documents}}'
        - name: rejected_sources
          type: object
          value: '{{outputs.acquire_sources.rejected_candidates}}'
      instructions: >-
        Answer the user's question directly, then provide the essential analysis. Use GPT-5.6 Sol reasoning over every supplied lane:
        added uploaded or Google Drive files, selected web-page URL constraints, direct current web results, paper-search-mcp scholarly
        results, and bounded complex-research dossiers. Use only supplied evidence. Treat search snippets, paper metadata, and Deep
        Research prose as candidate research; acquired_sources means lawful acquisition, not automatic verification. Keep files,
        web sources, research papers, and official sources distinguishable. Compare sources, identify agreements and contradictions,
        include limitations, recency concerns, and unresolved uncertainty. Never invent a source, URL, fact, or citation. Cite factual
        claims with [N] markers corresponding to the source cards emitted by source-producing nodes. If a selected URL was only a
        constraint and was not returned by a source-producing node, do not claim its page was read. Prefer concise sections: Answer,
        Evidence, Source comparison, Contradictions and limitations. If evidence is insufficient, say exactly what was not established.
      output_fields:
        - name: answer
          type: text
          required: true
    experience:
      display_name: Synthesize Research Answer
      purpose: Use GPT-5.6 Sol to compare all source lanes and answer the question directly.
      contribution: Produces the final cited answer while preserving source distinctions and uncertainty.
      expected_output: A direct research answer with evidence, source comparison, contradictions, and limitations.
      running_message: GPT-5.6 Sol is comparing all sources and writing the answer…
      completed_message: Combined research answer completed.
      failure_message: GPT-5.6 Sol could not synthesize the research answer; inspect completed source steps and retry.
  - id: end
    type: EndAgent
    config:
      mode: chat_response
      chat_message: '{{outputs.synthesize.parsed.answer}}'
      outcome: research_complete
edges:
  - from: start
    to: load_sources
  - from: start
    to: web_search
  - from: start
    to: paper_search
  - from: start
    to: deep_research
  - from: deep_research
    to: acquire_sources
  - from: load_sources
    to: synthesize
  - from: web_search
    to: synthesize
  - from: paper_search
    to: synthesize
  - from: acquire_sources
    to: synthesize
  - from: synthesize
    to: end
"""


def build_deep_research_chat_workflow(rag_agent_id: str | None = None) -> str:
    """Add a saved RAG Agent lane without exposing collection/index filters."""
    if not rag_agent_id:
        return _DEEP_RESEARCH_YAML
    document = yaml.safe_load(_DEEP_RESEARCH_YAML)
    document["description"] = (
        "Combine internal documentation from a saved RAG Agent with complex bounded research, "
        "direct web search, scholarly MCP search, and supplied files into one cited answer."
    )
    internal_node = {
        "id": "internal_knowledge", "type": "RAGAgent",
        "config": {"rag_agent_id": rag_agent_id, "query": "{{outputs.start.message}}"},
        "experience": {
            "display_name": "Research Internal Knowledge",
            "purpose": "Retrieve evidence through the saved RAG Agent selected from the Knowledge collection.",
            "contribution": "Supplies internal passages and citations for comparison with public evidence.",
            "expected_output": "A cited internal answer and retrieved Knowledge passages.",
            "running_message": "Searching the selected internal Knowledge collection…",
            "completed_message": "Internal documentation research completed.",
            "failure_message": "Internal Knowledge research could not complete; verify the saved RAG Agent and collection readiness.",
        },
    }
    synthesis = next(node for node in document["nodes"] if node["id"] == "synthesize")
    synthesis["config"]["input_fields"] += [
        {"name": "internal_answer", "type": "string", "value": "{{outputs.internal_knowledge.answer}}"},
        {"name": "internal_passages", "type": "object", "value": "{{outputs.internal_knowledge.retrievals}}"},
        {"name": "internal_citations", "type": "object", "value": "{{outputs.internal_knowledge.citations}}"},
    ]
    synthesis["config"]["instructions"] += (
        " Also use the saved RAG Agent's internal answer, passages, and citations. Clearly state where internal documentation "
        "agrees with, adds to, or conflicts with external evidence; internal policy may govern internal action even when public "
        "sources describe a different general practice."
    )
    insert_at = next(index for index, node in enumerate(document["nodes"]) if node["id"] == "deep_research")
    document["nodes"].insert(insert_at, internal_node)
    document["edges"] += [
        {"from": "start", "to": "internal_knowledge"},
        {"from": "internal_knowledge", "to": "synthesize"},
    ]
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


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


async def _repair_workspace_existing_adapter(
    record: ChatWorkflowRecord,
    store: ChatWorkflowStore,
) -> ChatWorkflowRecord:
    """Refresh planner-created saved-workflow wrappers after adapter fixes."""
    if (
        record.source != "existing"
        or not record.source_workflow_name
    ):
        return record
    path = WORKFLOWS_DIR / f"{record.source_workflow_name}.yaml"
    if not path.exists():
        return record
    selected = load_workflow_from_string(path.read_text(encoding="utf-8"))
    rebuilt_yaml = _private_yaml(build_existing_workflow_chat_adapter(
        record.source_workflow_name,
        selected,
    ))
    if rebuilt_yaml == record.yaml:
        return record
    rebuilt_spec = _validate(rebuilt_yaml)
    compatibility = analyze_chat_output(rebuilt_spec)
    if not compatibility.supported:
        return record
    return await store.replace_executable(
        record.owner_scope_id,
        record.chat_workflow_id,
        yaml_text=rebuilt_yaml,
        description=rebuilt_spec.description or "",
        output_compatibility=compatibility.model_dump(mode="json"),
    )


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
    managed: bool = False,
    adapter_key: str | None = None,
    adapter_fingerprint: str | None = None,
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
            managed=managed,
            adapter_key=adapter_key,
            adapter_fingerprint=adapter_fingerprint,
        )
    except ChatWorkflowConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


async def ensure_managed_adapter(
    request: Request,
    user: CurrentUser,
    *,
    adapter_key: str,
    display_name: str,
    yaml_text: str,
    source: Literal["generated", "imported", "existing"],
    source_workflow_name: str | None = None,
    preferred_slug: str | None = None,
) -> ChatWorkflowRecord:
    """Reuse one owner-scoped canonical adapter and upgrade it in place."""
    owner_scope_id = _scope(user)
    store = _store(request)
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
    fingerprint = hashlib.sha256(private_yaml.encode("utf-8")).hexdigest()
    existing = await store.get_by_adapter_key(owner_scope_id, adapter_key)
    if existing is None and preferred_slug:
        existing = await store.get_by_slug(owner_scope_id, preferred_slug)
    if existing is None:
        # Adopt one exact legacy workspace adapter so existing conversation IDs
        # remain useful. Do not claim user-authored/imported workflows by shape.
        existing = next((
            record for record in await store.list_private(owner_scope_id)
            if record.slug.startswith("workspace-") and record.yaml == private_yaml
        ), None)
    if existing is not None:
        if (
            existing.adapter_fingerprint == fingerprint
            and existing.adapter_key == adapter_key
            and existing.managed
        ):
            return existing
        return await store.replace_managed_adapter(
            owner_scope_id,
            existing.chat_workflow_id,
            display_name=display_name.strip(),
            yaml_text=private_yaml,
            description=spec.description or "",
            source=source,
            source_workflow_name=source_workflow_name,
            output_compatibility=compatibility.model_dump(mode="json"),
            adapter_key=adapter_key,
            adapter_fingerprint=fingerprint,
        )
    slug = preferred_slug or f"managed-{hashlib.sha256(adapter_key.encode('utf-8')).hexdigest()[:16]}"
    try:
        return await _save(
            request,
            user,
            slug=slug,
            display_name=display_name,
            yaml_text=private_yaml,
            source=source,
            source_workflow_name=source_workflow_name,
            managed=True,
            adapter_key=adapter_key,
            adapter_fingerprint=fingerprint,
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_409_CONFLICT:
            raise
        # Another equivalent request may have created the canonical adapter
        # between our lookup and insert. Resolve that race to the stable record.
        winner = await store.get_by_adapter_key(owner_scope_id, adapter_key)
        if winner is None:
            winner = await store.get_by_slug(owner_scope_id, slug)
        if winner is None:
            raise
        return winner


@router.get("")
async def list_private_chat_workflows(
    request: Request,
    user: CurrentUser = Depends(require_consultant),
    include_managed: bool = False,
):
    records = await _store(request).list_private(_scope(user))
    if not include_managed:
        records = [
            record for record in records
            if not record.managed and not record.slug.startswith("workspace-")
        ]
    return {"workflows": [_summary(record) for record in records]}


@router.post("/presets/deep-research", status_code=status.HTTP_201_CREATED)
async def ensure_deep_research_chat_workflow(
    request: Request,
    user: CurrentUser = Depends(require_consultant),
    rag_agent_id: str | None = None,
):
    """Return the owner's vetted Deep Research workflow for one Knowledge binding."""
    rag_agent_id = rag_agent_id.strip() if rag_agent_id else None
    adapter_key = (
        f"preset:deep-research:rag-agent:{rag_agent_id}"
        if rag_agent_id else "preset:deep-research"
    )
    return _summary(await ensure_managed_adapter(
        request,
        user,
        adapter_key=adapter_key,
        preferred_slug=_DEEP_RESEARCH_SLUG if not rag_agent_id else None,
        display_name="Deep Research",
        yaml_text=build_deep_research_chat_workflow(rag_agent_id),
        source="imported",
    ))


@router.post("/presets/general", status_code=status.HTTP_201_CREATED)
async def ensure_general_chat_workflow(
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    """Return one stable owner-scoped workflow for general Chat requests."""
    return _summary(await ensure_managed_adapter(
        request,
        user,
        adapter_key="preset:general",
        preferred_slug=_GENERAL_CHAT_SLUG,
        display_name="General Chat",
        yaml_text=build_llm_adapter("General Chat"),
        source="imported",
    ))


@router.get("/adapters/by-name/{workflow_name}")
async def get_builder_chat_execution_adapter(
    workflow_name: str,
    user: CurrentUser = Depends(require_consultant),
):
    """Build a universal, execution-only Chat wrapper around a saved workflow."""
    del user
    if not _SAFE_SLUG.fullmatch(workflow_name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    path = WORKFLOWS_DIR / f"{workflow_name}.yaml"
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    selected = load_workflow_from_string(path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    yaml_text = build_existing_workflow_chat_adapter(
        workflow_name,
        selected,
        runtime_metadata={
            "processed_at": now,
            "requested_at": now,
            "source_label": "Chat message",
        },
    )
    report = preflight_workflow_yaml(yaml_text, compile_graph=True)
    if not report.valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "The saved workflow could not be adapted for Chat execution",
                "preflight": report.model_dump(mode="json"),
            },
        )
    return {"workflow_name": workflow_name, "yaml": yaml_text, "adapted": True}


@router.get("/{chat_workflow_id}")
async def get_private_chat_workflow(
    chat_workflow_id: str,
    request: Request,
    user: CurrentUser = Depends(require_consultant),
):
    store = _store(request)
    try:
        record = await store.get(_scope(user), chat_workflow_id)
    except ChatWorkflowNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    record = await _repair_workspace_existing_adapter(record, store)
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