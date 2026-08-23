"""Deterministic planning for the workflow-powered Chat workspace.

The planner never executes a model and never introduces another workflow
runtime.  It either selects a real saved workflow or builds a small adapter
from registered node types; the resulting YAML still goes through the normal
preflight, background execution, run history, SSE, retry and transcript paths.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Literal

import yaml

from app.runtime.schema import WorkflowSpec


PlanKind = Literal["llm", "files", "vision", "web", "retrieval", "integration", "existing_workflow", "artifact"]
OutputKind = Literal["auto", "text", "image", "pdf", "docx", "pptx"]
CHAT_ANSWER_MODEL = "gpt-5"


@dataclass(frozen=True)
class WorkspaceExperience:
    id: str
    title: str
    examples: tuple[str, ...]
    default_plan: PlanKind
    existing_workflow: str | None = None
    capabilities: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "examples": list(self.examples),
            "default_plan": self.default_plan,
            "existing_workflow": self.existing_workflow,
            "capabilities": list(self.capabilities),
        }


EXPERIENCES: tuple[WorkspaceExperience, ...] = (
    WorkspaceExperience("document_qa", "Ask Questions About My Documents", ("What are the common conclusions?",), "retrieval", capabilities=("sources", "citations", "follow_up")),
    WorkspaceExperience("research_analyst", "Research Analyst", ("Analyze these reports and identify market trends.",), "retrieval", capabilities=("research", "structured_findings", "citations")),
    WorkspaceExperience("research_to_presentation", "Research to Presentation", ("Turn these reports into a 10-slide executive presentation.",), "artifact", capabilities=("research", "pptx", "citations")),
    WorkspaceExperience("research_to_pdf", "Research to PDF Report", ("Create a detailed PDF report from these documents.",), "artifact", capabilities=("research", "pdf", "citations")),
    WorkspaceExperience("meeting_intelligence", "Meeting / Interview Intelligence", ("What themes keep appearing in these interviews?",), "files", capabilities=("extraction", "themes", "quotes")),
    WorkspaceExperience("customer_feedback", "Customer Feedback Analysis", ("Identify product issues in these support conversations.",), "files", capabilities=("classification", "frequency", "severity")),
    WorkspaceExperience("competitive_intelligence", "Competitive Intelligence", ("Compare our product with these competitor documents.",), "retrieval", capabilities=("comparison", "citations")),
    WorkspaceExperience("contract_policy", "Contract / Policy Understanding", ("What obligations does this agreement create?",), "retrieval", capabilities=("clauses", "citations", "follow_up")),
    WorkspaceExperience("long_document", "Long Document Assistant", ("Help me understand this long document.",), "retrieval", capabilities=("indexing", "citations", "follow_up")),
    WorkspaceExperience("study_assistant", "Study / Learning Assistant", ("Teach me this material and quiz me.",), "retrieval", capabilities=("study_notes", "quiz", "follow_up")),
    WorkspaceExperience("executive_brief", "Executive Brief Generator", ("Give me a five-minute briefing before my meeting.",), "files", capabilities=("summary", "risks", "talking_points")),
    WorkspaceExperience("results_interpreter", "Data / Results Interpreter", ("Explain these experiment results.",), "files", capabilities=("analysis", "limitations", "recommendations")),
    WorkspaceExperience("product_requirements", "Product Requirements Assistant", ("Turn these interviews into a PRD.",), "files", capabilities=("structured_output", "requirements", "acceptance_criteria")),
    WorkspaceExperience("content_repurposing", "Content Repurposing", ("Turn this report into a blog post and presentation.",), "artifact", capabilities=("multiple_outputs", "pptx")),
    WorkspaceExperience("proposal_generator", "Proposal Generator", ("Create a proposal from these client notes.",), "existing_workflow", existing_workflow="w10_evidence_grounded_proposal", capabilities=("proposal", "pdf", "pptx", "human_review")),
    WorkspaceExperience("due_diligence", "Due-Diligence Assistant", ("Identify issues I should investigate.",), "retrieval", capabilities=("risk", "contradictions", "citations")),
    WorkspaceExperience("troubleshooting", "Incident / Troubleshooting Assistant", ("What probably happened in this incident?",), "files", capabilities=("timeline", "root_cause", "uncertainty")),
    WorkspaceExperience("decision_support", "Decision Support", ("Should we choose option A or B?",), "retrieval", capabilities=("comparison", "scoring", "recommendation")),
    WorkspaceExperience("chat_workflow", "Chat → Workflow Execution", ("Use the customer inquiry workflow.",), "existing_workflow", existing_workflow="w01_intelligent_customer_inquiry_resolution", capabilities=("workflow_selection", "execution")),
    WorkspaceExperience("multi_workflow_project", "Multi-Workflow AI Project", ("Research, recommend a strategy, and create slides.",), "artifact", capabilities=("research", "strategy", "pptx", "composition")),
)

EXPERIENCE_BY_ID = {item.id: item for item in EXPERIENCES}

_PDF = re.compile(r"\b(pdf|report|document)\b", re.I)
_DOCX = re.compile(r"\b(docx|word document|microsoft word)\b", re.I)
_PPTX = re.compile(r"\b(slides?|presentation|deck|powerpoint|pptx)\b", re.I)
_IMAGE_OUTPUT = re.compile(r"\b(create|make|generate|draw|design)\b.*\b(image|illustration|visual|picture)\b", re.I)
_INFOGRAPHIC_OUTPUT = re.compile(
    r"\b(create|make|generate|draw|design|turn)\b[^.?!\n]{0,100}"
    r"\binfo\s*graphics?\b|\binfo\s*graphics?\b[^.?!\n]{0,100}"
    r"\b(create|make|generate|draw|design|visual|study|learn)\b",
    re.I,
)
_DIAGRAM_OUTPUT = re.compile(
    r"\b(create|make|generate|draw|design|visuali[sz]e|turn)\b"
    r"[^.?!\n]{0,100}\b(diagram|flowchart|architecture|schematic|system map)\b"
    r"|\b(diagram|flowchart|architecture|schematic|system map)\b"
    r"[^.?!\n]{0,100}\b(image|visual|graphic)\b",
    re.I,
)
_SOURCE = re.compile(r"\b(according to|sources?|documents?|files?|reports?|papers?|contract|policy|evidence|citations?)\b", re.I)
_TOOL = re.compile(r"\b(mcp|salesforce|crm|erp|email|database|integration|send|create ticket)\b", re.I)
_WEB = re.compile(
    r"https?://|\b(web|online|latest|current|today|recent news|search the internet|weather|forecast)\b",
    re.I,
)


@dataclass(frozen=True)
class WorkspacePlan:
    kind: PlanKind
    title: str
    reason: str
    yaml_text: str | None
    existing_workflow: str | None = None
    experience_id: str | None = None
    missing_requirements: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "reason": self.reason,
            "yaml": self.yaml_text,
            "existing_workflow": self.existing_workflow,
            "experience_id": self.experience_id,
            "missing_requirements": list(self.missing_requirements),
            "capabilities": list(self.capabilities),
        }


def _experience(value: str | None) -> WorkspaceExperience | None:
    return EXPERIENCE_BY_ID.get(value or "")


def _base(title: str, welcome: str, allow_attachments: bool = True) -> dict[str, Any]:
    return {
        "name": title,
        "description": "Private Chat adapter composed from existing workflow node types.",
        "version": "1.0",
        "use_case": "chat_workspace",
        "library": {
            "title": title,
            "summary": welcome,
            "visibility_status": "draft",
        },
        "entry": "start",
        "exit": "reply",
        "nodes": [{
            "id": "start",
            "type": "StartAgent",
            "config": {
                "mode": "chatbot", "chatbot_name": title,
                "welcome_message": welcome, "allow_attachments": allow_attachments,
            },
            "experience": {
                "display_name": "Chat Input", "purpose": "Collect the user's objective and files.",
                "contribution": "Provides the request every later step works on.",
                "expected_output": "A message and optional file references.",
                "failure_message": "The request could not be collected.",
            },
        }],
        "edges": [],
    }


def _experience_block(name: str, purpose: str, output: str) -> dict[str, str]:
    return {
        "display_name": name, "purpose": purpose,
        "contribution": "Produces information used by the next workflow step.",
        "expected_output": output, "failure_message": f"{name} could not complete.",
    }


def _answer_node(instructions: str, input_fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "answer", "type": "TransformAgent", "selected_model": CHAT_ANSWER_MODEL,
        "config": {
            "mode": "ai", "model": CHAT_ANSWER_MODEL, "input_fields": input_fields,
            "instructions": (
                instructions
                + " Return a substantive user-facing answer. Never return null, none, "
                  "an empty array, an empty object, or a quoted placeholder as the answer."
            ),
            "output_fields": [{
                "name": "answer", "type": "text", "required": True,
                "description": "A substantive answer to show directly to the user; never a null or empty placeholder.",
            }],
            "reject_empty_fields": ["answer"],
        },
        "experience": _experience_block("Prepare Answer", "Answer using only the supplied request and context.", "A useful answer."),
    }


def _reply(message: str, *, sources: str | None = None, handoff: dict[str, Any] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"mode": "chat_response", "chat_message": message, "outcome": "answered"}
    if sources:
        config["sources"] = sources
    if handoff:
        config["handoff"] = handoff
    return {
        "id": "reply", "type": "EndAgent", "config": config,
        "experience": _experience_block("Chat Reply", "Present the result and artifacts in Chat.", "A user-visible response."),
    }


def build_existing_workflow_chat_adapter(
    workflow_name: str,
    workflow_spec: WorkflowSpec,
    *,
    runtime_metadata: dict[str, Any] | None = None,
) -> str:
    """Wrap any saved workflow with natural-language input/output LLM calls.

    The selected workflow remains unchanged and runs as an independently
    auditable subprocess. The wrapper only translates the Chat request into
    its declared input contract and translates its result into one answer.
    """
    title = (
        workflow_spec.library.title
        if workflow_spec.library and workflow_spec.library.title
        else workflow_spec.name
    )
    doc = _base(f"{title} Chat", f"Describe what you want {title} to do.")
    input_schema = {
        name: {
            "type": input_spec.type,
            "required": input_spec.required,
            "description": input_spec.description or "",
            **({"multiple": input_spec.multiple, "accept": input_spec.accept}
               if input_spec.type == "file" else {}),
        }
        for name, input_spec in workflow_spec.inputs.items()
    }
    generated_inputs = {
        name: input_spec
        for name, input_spec in workflow_spec.inputs.items()
        if input_spec.type != "file"
    }
    output_schema = {
        name: "dict" if input_spec.type == "json" else "str"
        for name, input_spec in generated_inputs.items()
    }
    if not output_schema:
        output_schema["normalized_request"] = "str"
    runtime_context = runtime_metadata or {"source_label": "Chat message"}
    doc["nodes"].append({
            "id": "prepare_inputs",
            "type": "TransformAgent",
            "selected_model": CHAT_ANSWER_MODEL,
            "config": {
                "mode": "ai",
                "model": CHAT_ANSWER_MODEL,
                "system_prompt": (
                    "Convert the user's Chat request and supplied runtime metadata into the exact "
                    "declared input contract for the selected workflow. Always return every declared "
                    "non-file field. Required fields must be non-empty. Preserve user facts exactly "
                    "and never invent business identifiers, people, addresses, quantities, or dates. "
                    "For provenance, source, filename, timestamp, or processing-time fields, copy the "
                    "matching value from runtime metadata or attachment metadata. When the request "
                    "itself is the source, use runtime_metadata.source_label. Optional missing text "
                    "fields may be empty strings and optional missing JSON fields may be empty objects."
                ),
                "prompt_template": (
                    "ATTACHMENTS:\n{{outputs.start.attachments}}\n\n"
                    "RUNTIME METADATA:\n"
                    f"{json.dumps(runtime_context, ensure_ascii=False, indent=2)}\n\n"
                    "USER REQUEST:\n{{outputs.start.message}}\n\n"
                    "SELECTED WORKFLOW INPUT CONTRACT:\n"
                    f"{json.dumps(input_schema, ensure_ascii=False, indent=2)}\n\n"
                    "Return only the declared non-file input fields."
                ),
                "output_schema": output_schema,
            },
            "experience": _experience_block(
                "Understand Request",
                "Translate the conversation into the selected workflow's declared inputs.",
                "Validated workflow inputs.",
            ),
        })
    doc["edges"].append({"from": "start", "to": "prepare_inputs"})

    child_inputs: dict[str, Any] = {}
    for name, input_spec in workflow_spec.inputs.items():
        if input_spec.type == "file":
            child_inputs[name] = (
                "{{outputs.start.attachments}}"
                if input_spec.multiple
                else "{{outputs.start.attachments.0?}}"
            )
        else:
            child_inputs[name] = f"{{{{outputs.prepare_inputs.parsed.{name}}}}}"

    doc["nodes"].extend([
        {
            "id": "run_workflow",
            "type": "SubprocessAgent",
            "config": {
                "workflow": workflow_name,
                "inputs": child_inputs,
                "result_from": "workflow_output",
                "timeout_seconds": 1800,
            },
            "experience": _experience_block(
                "Run Selected Workflow",
                f"Execute {title} through the existing workflow runtime.",
                "The selected workflow's durable result.",
            ),
        },
        _answer_node(
            (
                "Explain the selected workflow's result as a direct, useful answer to the user's "
                "request. Preserve material facts, decisions, uncertainty, and failure states. "
                "Do not expose node ids, raw payload envelopes, or internal workflow mechanics."
            ),
            [
                {"name": "request", "type": "string", "value": "{{outputs.start.message}}"},
                {"name": "workflow_result", "type": "object", "value": "{{outputs.run_workflow.result}}"},
            ],
        ),
        _reply(
            "{{outputs.answer.parsed.answer}}",
            handoff={"structured_result": "{{outputs.run_workflow.result}}"},
        ),
    ])
    doc["edges"].extend([
        {"from": "prepare_inputs", "to": "run_workflow"},
        {"from": "run_workflow", "to": "answer"},
        {"from": "answer", "to": "reply"},
    ])
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_llm_adapter(title: str = "AI Workspace", previous_result: Any = None) -> str:
    doc = _base(title, "Ask a question or request a transformation.")
    input_fields = [{"name": "request", "type": "string", "value": "{{outputs.start.message}}"}]
    if previous_result is not None:
        input_fields.append({"name": "previous_workflow_result", "type": "object", "value": previous_result})
    doc["nodes"] += [
        _answer_node(
            "Answer the user's request directly. Reuse the supplied previous workflow result when present instead of repeating its work. Use concise, accurate language. Do not claim to have used sources or tools that were not supplied.",
            input_fields,
        ),
        _reply("{{outputs.answer.parsed.answer}}"),
    ]
    doc["edges"] = [{"from": "start", "to": "answer"}, {"from": "answer", "to": "reply"}]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_scientific_skill_adapter(skill_name: str) -> str:
    """Build a conversational workflow that applies one approved skill."""
    title = " ".join(part.capitalize() for part in skill_name.split("-"))
    doc = _base(f"{title} Skill", f"Ask a question using the {title} skill.", allow_attachments=False)
    doc["nodes"].extend([
        {
            "id": "apply_skill",
            "type": "ScientificSkillAgent",
            "selected_model": "claude-sonnet-4-5",
            "config": {
                "model": "claude-sonnet-4-5",
                "objective": "{{outputs.start.message}}",
                "skills": [skill_name],
                "auto_select": False,
                "max_skills": 1,
            },
            "experience": _experience_block(
                "Apply Skill",
                f"Use the approved {title} methodology for this request.",
                "A skill-guided answer with the applied skill recorded.",
            ),
        },
        _reply("{{outputs.apply_skill.answer}}"),
    ])
    doc["edges"] = [
        {"from": "start", "to": "apply_skill"},
        {"from": "apply_skill", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_file_adapter(title: str, instructions: str) -> str:
    doc = _base(title, "Attach files and describe what you need.")
    loader = {
        "id": "load_files", "type": "WorkflowFileLoader",
        "config": {"files": "{{outputs.start.attachments}}", "fail_on_unreadable": False},
        "experience": _experience_block("Read Sources", "Extract usable text from the attached files.", "Extracted source text and stable file references."),
    }
    doc["nodes"] += [
        loader,
        _answer_node(
            instructions + (
                " Base every factual claim on the attached material and complete every deliverable "
                "the user requests, including summaries, comparisons, lists, and simple diagrams. "
                "Match names and subjects semantically and case-insensitively across the request, "
                "filename, headings, and source text. Tolerate capitalization, punctuation, spacing, "
                "minor spelling differences, and PDF/OCR layout artifacts. Never claim that a subject "
                "is absent until you have checked those equivalent forms. If the source clearly identifies "
                "the requested subject, answer directly without an unnecessary limitation disclaimer. "
                "Identify genuine source limitations plainly."
            ),
            [
                {"name": "request", "type": "string", "value": "{{outputs.start.message}}"},
                {"name": "source_text", "type": "string", "value": "{{outputs.load_files.text}}"},
            ],
        ),
        _reply("{{outputs.answer.parsed.answer}}"),
    ]
    doc["edges"] = [
        {"from": "start", "to": "load_files"}, {"from": "load_files", "to": "answer"},
        {"from": "answer", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_vision_adapter(title: str) -> str:
    doc = _base(title, "Attach an image and describe what you need.")
    doc["nodes"] += [
        {
            "id": "vision", "type": "KimiVisionAgent",
            "config": {
                "image": "{{outputs.start.attachments.0}}",
                "prompt": "{{outputs.start.message}}",
            },
            "experience": _experience_block("Understand Image", "Analyze the attached image with the existing vision capability.", "A grounded image analysis."),
        },
        _answer_node(
            "Answer the request using the image analysis. Do not claim to see details absent from the analysis.",
            [
                {"name": "request", "type": "string", "value": "{{outputs.start.message}}"},
                {"name": "image_analysis", "type": "string", "value": "{{outputs.vision.analysis}}"},
            ],
        ),
        _reply("{{outputs.answer.parsed.answer}}"),
    ]
    doc["edges"] = [
        {"from": "start", "to": "vision"}, {"from": "vision", "to": "answer"},
        {"from": "answer", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_multimodal_file_adapter(title: str, instructions: str) -> str:
    """Analyze mixed uploaded sources, including one image plus text files."""
    doc = _base(title, "Attach files or images and describe what you need.")
    doc["nodes"] += [
        {
            "id": "load_files", "type": "WorkflowFileLoader",
            "config": {"files": "{{outputs.start.attachments}}", "fail_on_unreadable": False},
            "experience": _experience_block(
                "Read Sources", "Extract text from documents, presentations, spreadsheets, and code.",
                "Extracted source text and stable file references.",
            ),
        },
        {
            "id": "vision", "type": "KimiVisionAgent",
            "config": {
                "image": "{{outputs.load_files.image_files.0?}}",
                "prompt": "{{outputs.start.message}}",
            },
            "experience": _experience_block(
                "Understand Image", "Analyze the first attached image when one is present.",
                "A grounded image analysis or a clean skipped result.",
            ),
        },
        _answer_node(
            instructions + (
                " Use the extracted text and image analysis together. Base factual claims on the "
                "supplied material, distinguish uncertainty, and say when a file could not be read."
            ),
            [
                {"name": "request", "type": "string", "value": "{{outputs.start.message}}"},
                {"name": "source_text", "type": "string", "value": "{{outputs.load_files.text}}"},
                {"name": "image_analysis", "type": "string", "value": "{{outputs.vision.analysis}}"},
                {"name": "file_status", "type": "object", "value": "{{outputs.load_files.files}}"},
            ],
        ),
        _reply("{{outputs.answer.parsed.answer}}"),
    ]
    doc["edges"] = [
        {"from": "start", "to": "load_files"},
        {"from": "load_files", "to": "vision"},
        {"from": "vision", "to": "answer"},
        {"from": "answer", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_web_adapter(title: str) -> str:
    doc = _base(title, "Ask a question that requires current public web information.", allow_attachments=False)
    doc["nodes"] += [
        {
            "id": "search", "type": "WebSearchAgent",
            "config": {"query": "{{outputs.start.message}}", "provider": "auto", "top_k": 8},
            "experience": _experience_block("Search Web", "Find current candidate sources through the existing web-search service.", "Current web results with URLs and snippets."),
        },
        _answer_node(
            "Answer using only the supplied web results. Treat them as candidate sources, distinguish uncertainty, and cite [1], [2], and so on.",
            [
                {"name": "question", "type": "string", "value": "{{outputs.start.message}}"},
                {"name": "web_results", "type": "string", "value": "{{outputs.search.results}}"},
            ],
        ),
        _reply("{{outputs.answer.parsed.answer}}"),
    ]
    doc["edges"] = [
        {"from": "start", "to": "search"}, {"from": "search", "to": "answer"},
        {"from": "answer", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_mcp_adapter(title: str, server_id: str, tool: str) -> str:
    doc = _base(title, "Describe the connected-system operation to execute.", allow_attachments=False)
    doc["nodes"] += [
        {
            "id": "tool", "type": "MCPToolAgent",
            "config": {
                "server_id": server_id, "tool": tool,
                "arguments": {"request": "{{outputs.start.message}}"},
                "fail_on_error": False,
            },
            "experience": _experience_block("Run Connected Tool", "Invoke the explicitly selected tool through the existing MCP policy boundary.", "A typed tool result or a safe failure status."),
        },
        _answer_node(
            "Explain the connected tool result accurately. If the tool failed, was denied, or needs approval, say so and do not invent a result.",
            [
                {"name": "request", "type": "string", "value": "{{outputs.start.message}}"},
                {"name": "tool_status", "type": "string", "value": "{{outputs.tool.status}}"},
                {"name": "tool_result", "type": "string", "value": "{{outputs.tool.text}}"},
            ],
        ),
        _reply("{{outputs.answer.parsed.answer}}"),
    ]
    doc["edges"] = [
        {"from": "start", "to": "tool"}, {"from": "tool", "to": "answer"},
        {"from": "answer", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_retrieval_adapter(title: str, collection_id: str, retrieval_profile_id: str, instructions: str, document_ids: list[str] | None = None) -> str:
    doc = _base(title, "Ask questions grounded in the selected Knowledge source.")
    retrieve = {
        "id": "retrieve", "type": "KnowledgeRetrieval",
        "config": {
            "collection_id": collection_id, "retrieval_profile_id": retrieval_profile_id,
            "query": "{{outputs.start.message}}",
            "document_ids": document_ids or [],
        },
        "experience": _experience_block("Retrieve Evidence", "Find only the passages relevant to this turn.", "Retrieved passages and citations."),
    }
    doc["nodes"] += [
        retrieve,
        _answer_node(
            instructions + " Use only the retrieved evidence. Cite supported claims with [1], [2], and so on. If evidence is insufficient, say so.",
            [
                {"name": "question", "type": "string", "value": "{{outputs.start.message}}"},
                {"name": "evidence", "type": "string", "value": "{{outputs.retrieve.context}}"},
            ],
        ),
        _reply("{{outputs.answer.parsed.answer}}", sources="{{outputs.retrieve.citations}}"),
    ]
    doc["edges"] = [
        {"from": "start", "to": "retrieve"}, {"from": "retrieve", "to": "answer"},
        {"from": "answer", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_rag_adapter(title: str, rag_agent_id: str, document_ids: list[str] | None = None) -> str:
    doc = _base(title, "Ask questions grounded by the selected saved RAG Agent.")
    doc["inputs"] = {
        "conversation_summary": {
            "type": "text", "required": False,
            "description": "Bounded recent Chat context used only to resolve ambiguous follow-ups.",
        },
    }
    doc["nodes"] += [
        {
            "id": "rewrite_query", "type": "TransformAgent", "selected_model": CHAT_ANSWER_MODEL,
            "config": {
                "mode": "ai", "model": CHAT_ANSWER_MODEL, "temperature": 0,
                "input_fields": [
                    {"name": "current_question", "type": "string", "value": "{{outputs.start.message}}"},
                    {"name": "recent_conversation", "type": "string", "value": "{{inputs.conversation_summary}}"},
                ],
                "instructions": (
                    "Return one standalone retrieval query for the current question. Use recent_conversation "
                    "only to resolve pronouns, ellipsis, or omitted subject matter in a context-dependent "
                    "follow-up. If current_question is already standalone, copy it exactly. Preserve names, "
                    "numbers, product identifiers, qualifications, and intent. Do not answer the question, "
                    "add facts, broaden its scope, or include commentary."
                ),
                "output_fields": [{"name": "retrieval_query", "type": "text", "required": True}],
            },
            "experience": _experience_block(
                "Prepare Retrieval Query",
                "Resolve an ambiguous follow-up into a standalone Knowledge search without changing the user's message.",
                "A bounded standalone retrieval query.",
            ),
        },
        {
            "id": "rag", "type": "RAGAgent",
            "config": {
                "rag_agent_id": rag_agent_id,
                "query": "{{outputs.rewrite_query.parsed.retrieval_query}}",
                "document_ids": document_ids or [],
            },
            "experience": _experience_block("Grounded Answer", "Retrieve relevant evidence and generate a cited answer through the saved RAG Agent.", "A grounded answer and source records."),
        },
        _reply("{{outputs.rag.answer}}", sources="{{outputs.rag.sources}}"),
    ]
    doc["edges"] = [
        {"from": "start", "to": "rewrite_query"},
        {"from": "rewrite_query", "to": "rag"},
        {"from": "rag", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_artifact_adapter(
    title: str, output: Literal["pdf", "docx", "pptx"], *, with_files: bool,
    previous_result: Any = None,
) -> str:
    doc = _base(title, f"Describe the {output.upper()} you need and attach supporting files.")
    predecessor = "start"
    input_fields: list[dict[str, Any]] = [
        {"name": "request", "type": "string", "value": "{{outputs.start.message}}"},
    ]
    if previous_result is not None:
        input_fields.append({
            "name": "previous_workflow_result", "type": "object", "value": previous_result,
        })
    if with_files:
        doc["nodes"].append({
            "id": "load_files", "type": "WorkflowFileLoader",
            "config": {"files": "{{outputs.start.attachments}}", "fail_on_unreadable": False},
            "experience": _experience_block("Read Sources", "Extract the attached source material once.", "Extracted source text."),
        })
        doc["edges"].append({"from": "start", "to": "load_files"})
        predecessor = "load_files"
        input_fields.append({"name": "source_text", "type": "string", "value": "{{outputs.load_files.text}}"})
    sections = {
        "id": "compose", "type": "TransformAgent", "selected_model": CHAT_ANSWER_MODEL,
        "config": {
            "mode": "ai", "model": CHAT_ANSWER_MODEL, "input_fields": input_fields,
            "instructions": "Create a grounded executive deliverable. Do not invent source facts. Return concise title, executive summary, key findings, risks, and recommendations.",
            "output_fields": [
                {"name": "title", "type": "string", "required": True},
                {"name": "executive_summary", "type": "text", "required": True},
                {"name": "key_findings", "type": "text", "required": True},
                {"name": "risks", "type": "text", "required": True},
                {"name": "recommendations", "type": "text", "required": True},
            ],
        },
        "experience": _experience_block("Compose Deliverable", "Create structured content for the requested artifact.", "Structured executive content."),
    }
    renderer_type = {
        "pdf": "PDFProposalRenderer",
        "docx": "DOCXProposalRenderer",
        "pptx": "PowerPointProposalSlides",
    }[output]
    renderer = {
        "id": "render", "type": renderer_type,
        "config": {
            "sections": {
                "Executive Summary": "{{outputs.compose.parsed.executive_summary}}",
                "Key Findings": "{{outputs.compose.parsed.key_findings}}",
                "Risks": "{{outputs.compose.parsed.risks}}",
                "Recommendations": "{{outputs.compose.parsed.recommendations}}",
            },
            **({"template": "professional"} if output == "pdf" else {}),
            "proposal_title": "{{outputs.compose.parsed.title}}", "client_name": "AI Workspace",
        },
        "experience": _experience_block(f"Generate {output.upper()}", f"Render the content with the existing {renderer_type} node.", f"A downloadable {output.upper()} artifact."),
    }
    doc["nodes"] += [sections, renderer, _reply(
        "{{outputs.compose.parsed.executive_summary}}",
        handoff={output: "{{outputs.render.minio_key}}"},
    )]
    doc["edges"] += [
        {"from": predecessor, "to": "compose"}, {"from": "compose", "to": "render"},
        {"from": "render", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_image_generation_adapter(title: str = "Image Creator") -> str:
    doc = _base(title, "Describe the image you want to create.", allow_attachments=False)
    doc["nodes"] += [
        {
            "id": "generate_image",
            "type": "OpenAIImageGenerationAgent",
            "config": {
                "prompt": "{{outputs.start.message}}",
                "backend": "openai",
                "size": "auto",
                "quality": "auto",
                "output_format": "png",
            },
            "experience": _experience_block(
                "Create Image", "Generate the requested image with the configured image service.",
                "A downloadable image artifact.",
            ),
        },
        _reply(
            "I created the requested image.",
            handoff={"image": "{{outputs.generate_image.minio_key}}"},
        ),
    ]
    doc["edges"] = [
        {"from": "start", "to": "generate_image"},
        {"from": "generate_image", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_grounded_diagram_adapter(title: str = "Architecture Diagram") -> str:
    """Read attached sources, explain them, and generate a grounded visual image."""
    doc = _base(title, "Attach source material and describe the visual you need.")
    doc["nodes"] += [
        {
            "id": "load_files", "type": "WorkflowFileLoader",
            "config": {"files": "{{outputs.start.attachments}}", "fail_on_unreadable": False},
            "experience": _experience_block(
                "Read Sources", "Extract the source content that the diagram must represent.",
                "Grounding text and stable file references.",
            ),
        },
        {
            "id": "diagram_plan", "type": "TransformAgent", "selected_model": CHAT_ANSWER_MODEL,
            "config": {
                "mode": "ai", "model": CHAT_ANSWER_MODEL,
                "input_fields": [
                    {"name": "request", "type": "string", "value": "{{outputs.start.message}}"},
                    {"name": "source_text", "type": "string", "value": "{{outputs.load_files.text}}"},
                ],
                "instructions": (
                    "Explain the requested subject concisely and design one accurate visual from the attached "
                    "source. Follow the requested visual form: use a layered technical architecture diagram for "
                    "architecture or system requests, and use a learning infographic with memorable sections, "
                    "visual hierarchy, icons, and a clear study path for educational requests. Match names "
                    "case-insensitively and tolerate PDF/DOCX/OCR spacing, punctuation, duplicated extraction, "
                    "and layout artifacts. Select and compress the most important source-grounded concepts rather "
                    "than copying the document verbatim. The image prompt must describe a clean professional "
                    "flat-vector visual with a white background, strong hierarchy, high contrast, and only short "
                    "legible labels. Include important relationships supported by the source; do not invent facts. "
                    "Avoid logos, photorealism, tiny paragraphs, code, Mermaid syntax, and implementation commentary. "
                    "Return an answer for the user and a standalone image-generation prompt."
                ),
                "output_fields": [
                    {"name": "answer", "type": "text", "required": True},
                    {"name": "image_prompt", "type": "text", "required": True},
                ],
            },
            "experience": _experience_block(
                "Design Visual", "Convert the grounded source into an explanation and visual brief.",
                "A concise explanation and source-grounded image prompt.",
            ),
        },
        {
            "id": "generate_image", "type": "OpenAIImageGenerationAgent",
            "config": {
                "prompt": "{{outputs.diagram_plan.parsed.image_prompt}}",
                "backend": "openai", "size": "auto", "quality": "high", "output_format": "png",
            },
            "experience": _experience_block(
                "Generate Visual", "Render the grounded diagram or infographic as a real image.",
                "A downloadable PNG visual.",
            ),
        },
        _reply(
            "{{outputs.diagram_plan.parsed.answer}}",
            handoff={"image": "{{outputs.generate_image.minio_key}}"},
        ),
    ]
    doc["edges"] = [
        {"from": "start", "to": "load_files"},
        {"from": "load_files", "to": "diagram_plan"},
        {"from": "diagram_plan", "to": "generate_image"},
        {"from": "generate_image", "to": "reply"},
    ]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def plan_workspace(
    objective: str,
    *,
    experience_id: str | None = None,
    skill_name: str | None = None,
    selected_workflow: str | None = None,
    preferred_output: OutputKind = "auto",
    has_attachments: bool = False,
    attachment_categories: list[str] | None = None,
    collection_id: str | None = None,
    retrieval_profile_id: str | None = None,
    rag_agent_id: str | None = None,
    document_ids: list[str] | None = None,
    integration_connection: str | None = None,
    integration_tool: str | None = None,
    previous_run_id: str | None = None,
    previous_result: Any = None,
) -> WorkspacePlan:
    text = objective.strip()
    experience = _experience(experience_id)
    capabilities = experience.capabilities if experience else ()
    if selected_workflow:
        return WorkspacePlan("existing_workflow", "Selected Workflow", "The user explicitly selected this saved workflow.", None, selected_workflow, experience_id, capabilities=capabilities)
    if skill_name:
        title = " ".join(part.capitalize() for part in skill_name.split("-"))
        return WorkspacePlan(
            "llm",
            f"{title} Skill",
            "The user explicitly selected an approved Scientific Agent Skill.",
            build_scientific_skill_adapter(skill_name),
            experience_id=experience_id,
            capabilities=("scientific_skill", skill_name),
        )
    if experience and experience.existing_workflow:
        return WorkspacePlan("existing_workflow", experience.title, "This experience has an exact existing workflow foundation.", None, experience.existing_workflow, experience.id, capabilities=capabilities)
    if _TOOL.search(text):
        missing = tuple(name for name, value in (
            ("integration_connection", integration_connection), ("integration_tool", integration_tool),
        ) if not value)
        if missing:
            return WorkspacePlan("integration", "Connected Tool Required", "Tool and integration actions must use an explicitly configured connection and tool.", None, experience_id=experience_id, missing_requirements=missing, capabilities=("integration", "mcp"))
        return WorkspacePlan(
            "integration", "Connected Tool", "The request targets an external system, so the existing MCP policy and execution node is required.",
            build_mcp_adapter("Connected Tool", integration_connection or "", integration_tool or ""),
            experience_id=experience_id, capabilities=("integration", "mcp"),
        )

    output: OutputKind = preferred_output
    if output == "auto":
        output = (
            "image" if _INFOGRAPHIC_OUTPUT.search(text) or _DIAGRAM_OUTPUT.search(text) or _IMAGE_OUTPUT.search(text)
            else "pptx" if _PPTX.search(text)
            else "docx" if _DOCX.search(text)
            else "pdf" if _PDF.search(text) and re.search(r"\b(create|make|generate|export|turn)\b", text, re.I)
            else "text"
        )
    if output == "image":
        diagram_requested = bool(_DIAGRAM_OUTPUT.search(text))
        infographic_requested = bool(_INFOGRAPHIC_OUTPUT.search(text))
        grounded_visual_requested = diagram_requested or infographic_requested
        title = experience.title if experience else (
            "Learning Infographic" if infographic_requested
            else "Architecture Diagram" if diagram_requested
            else "Image Creator"
        )
        return WorkspacePlan(
            "artifact", title,
            (
                "The attached source is read first, then converted into a grounded diagram image."
                if grounded_visual_requested and has_attachments
                else "The request asks for a generated image, so the configured image-generation service is used."
            ),
            (
                build_grounded_diagram_adapter(title)
                if grounded_visual_requested and has_attachments
                else build_image_generation_adapter(title)
            ),
            experience_id=experience_id,
            capabilities=capabilities or (
                ("files", "infographic", "image")
                if has_attachments and infographic_requested
                else ("files", "diagram", "image")
                if has_attachments and diagram_requested
                else ("image",)
            ),
        )
    if output in {"pdf", "docx", "pptx"}:
        title = experience.title if experience else f"AI Workspace {output.upper()}"
        return WorkspacePlan("artifact", title, f"The request asks for a real {output.upper()} artifact, so the existing renderer is required.", build_artifact_adapter(title, output, with_files=has_attachments, previous_result=previous_result), experience_id=experience_id, capabilities=capabilities or (output,))

    categories = set(attachment_categories or [])
    if has_attachments and categories and categories <= {"image"}:
        title = experience.title if experience else "Image Assistant"
        return WorkspacePlan("vision", title, "Image input requires the existing vision node before language generation.", build_vision_adapter(title), experience_id=experience_id, capabilities=capabilities or ("vision", "images"))

    if has_attachments and "image" in categories:
        title = experience.title if experience else "Source Assistant"
        return WorkspacePlan(
            "files", title,
            "The request combines images with readable files, so both vision and file extraction are used.",
            build_multimodal_file_adapter(
                title,
                "Analyze the requested material and return the clearest useful result.",
            ),
            experience_id=experience_id,
            capabilities=capabilities or ("files", "vision", "analysis"),
        )

    if _WEB.search(text) and not has_attachments:
        title = experience.title if experience else "Web Research Assistant"
        return WorkspacePlan("web", title, "The request needs current public information, so the existing web-search node is required.", build_web_adapter(title), experience_id=experience_id, capabilities=capabilities or ("web", "sources"))

    wants_retrieval = bool(collection_id or retrieval_profile_id or rag_agent_id) or bool(
        (experience and experience.default_plan == "retrieval" or _SOURCE.search(text))
        and not has_attachments
    )
    if wants_retrieval:
        title = experience.title if experience else "Knowledge Assistant"
        if rag_agent_id:
            return WorkspacePlan(
                "retrieval", title,
                "A saved RAG Agent can retrieve and generate the direct cited answer in one existing node.",
                build_rag_adapter(title, rag_agent_id, document_ids), experience_id=experience_id,
                capabilities=capabilities or ("rag", "citations"),
            )
        missing = tuple(name for name, value in (
            ("collection_id", collection_id), ("retrieval_profile_id", retrieval_profile_id),
        ) if not value)
        if missing:
            return WorkspacePlan("retrieval", experience.title if experience else "Knowledge Assistant", "Source-grounded questions require an indexed Knowledge collection and Retrieval Profile.", None, experience_id=experience_id, missing_requirements=missing, capabilities=capabilities or ("retrieval", "citations"))
        return WorkspacePlan("retrieval", title, "The question requires targeted retrieval and source provenance.", build_retrieval_adapter(title, collection_id or "", retrieval_profile_id or "", "Analyze the question and explain the supported answer.", document_ids), experience_id=experience_id, capabilities=capabilities or ("retrieval", "citations"))

    if has_attachments or (experience and experience.default_plan == "files"):
        title = experience.title if experience else "File Analysis Assistant"
        return WorkspacePlan("files", title, "The attached files can be processed directly without creating a persistent index.", build_file_adapter(title, "Analyze the requested material and return structured, actionable findings."), experience_id=experience_id, capabilities=capabilities or ("files", "analysis"))

    return WorkspacePlan(
        "llm", experience.title if experience else "AI Workspace",
        "A lightweight LLM workflow is sufficient; retrieval and complex workflows are unnecessary.",
        build_llm_adapter(experience.title if experience else "AI Workspace", previous_result),
        experience_id=experience_id,
        capabilities=capabilities or (("previous_result",) if previous_run_id else ("llm",)),
    )