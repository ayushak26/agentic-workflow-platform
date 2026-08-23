"""Deterministic planning for the workflow-powered Chat workspace.

The planner never executes a model and never introduces another workflow
runtime.  It either selects a real saved workflow or builds a small adapter
from registered node types; the resulting YAML still goes through the normal
preflight, background execution, run history, SSE, retry and transcript paths.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal

import yaml


PlanKind = Literal["llm", "files", "vision", "web", "retrieval", "integration", "existing_workflow", "artifact"]
OutputKind = Literal["auto", "text", "pdf", "pptx"]


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
_PPTX = re.compile(r"\b(slides?|presentation|deck|powerpoint|pptx)\b", re.I)
_SOURCE = re.compile(r"\b(according to|sources?|documents?|files?|reports?|papers?|contract|policy|evidence|citations?)\b", re.I)
_TOOL = re.compile(r"\b(mcp|salesforce|crm|erp|email|database|integration|send|create ticket)\b", re.I)
_WEB = re.compile(r"https?://|\b(web|online|latest|current|today|recent news|search the internet)\b", re.I)


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
        "id": "answer", "type": "TransformAgent", "selected_model": "auto",
        "config": {
            "mode": "ai", "model": "auto", "input_fields": input_fields,
            "instructions": instructions,
            "output_fields": [{"name": "answer", "type": "text", "required": True}],
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
            instructions + " Base every factual claim on the attached material; identify limitations plainly.",
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


def build_retrieval_adapter(title: str, collection_id: str, retrieval_profile_id: str, instructions: str) -> str:
    doc = _base(title, "Ask questions grounded in the selected Knowledge source.")
    retrieve = {
        "id": "retrieve", "type": "KnowledgeRetrieval",
        "config": {
            "collection_id": collection_id, "retrieval_profile_id": retrieval_profile_id,
            "query": "{{outputs.start.message}}",
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


def build_rag_adapter(title: str, rag_agent_id: str) -> str:
    doc = _base(title, "Ask questions grounded by the selected saved RAG Agent.")
    doc["nodes"] += [
        {
            "id": "rag", "type": "RAGAgent",
            "config": {"rag_agent_id": rag_agent_id, "query": "{{outputs.start.message}}"},
            "experience": _experience_block("Grounded Answer", "Retrieve relevant evidence and generate a cited answer through the saved RAG Agent.", "A grounded answer and source records."),
        },
        _reply("{{outputs.rag.answer}}", sources="{{outputs.rag.sources}}"),
    ]
    doc["edges"] = [{"from": "start", "to": "rag"}, {"from": "rag", "to": "reply"}]
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def build_artifact_adapter(
    title: str, output: Literal["pdf", "pptx"], *, with_files: bool,
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
        "id": "compose", "type": "TransformAgent", "selected_model": "auto",
        "config": {
            "mode": "ai", "model": "auto", "input_fields": input_fields,
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
    renderer_type = "PDFProposalRenderer" if output == "pdf" else "PowerPointProposalSlides"
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


def plan_workspace(
    objective: str,
    *,
    experience_id: str | None = None,
    selected_workflow: str | None = None,
    preferred_output: OutputKind = "auto",
    has_attachments: bool = False,
    attachment_categories: list[str] | None = None,
    collection_id: str | None = None,
    retrieval_profile_id: str | None = None,
    rag_agent_id: str | None = None,
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
        output = "pptx" if _PPTX.search(text) else "pdf" if _PDF.search(text) and re.search(r"\b(create|make|generate|export|turn)\b", text, re.I) else "text"
    if output in {"pdf", "pptx"}:
        title = experience.title if experience else f"AI Workspace {output.upper()}"
        return WorkspacePlan("artifact", title, f"The request asks for a real {output.upper()} artifact, so the existing renderer is required.", build_artifact_adapter(title, output, with_files=has_attachments, previous_result=previous_result), experience_id=experience_id, capabilities=capabilities or (output,))

    categories = set(attachment_categories or [])
    if has_attachments and categories and categories <= {"image"}:
        title = experience.title if experience else "Image Assistant"
        return WorkspacePlan("vision", title, "Image input requires the existing vision node before language generation.", build_vision_adapter(title), experience_id=experience_id, capabilities=capabilities or ("vision", "images"))

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
                build_rag_adapter(title, rag_agent_id), experience_id=experience_id,
                capabilities=capabilities or ("rag", "citations"),
            )
        missing = tuple(name for name, value in (
            ("collection_id", collection_id), ("retrieval_profile_id", retrieval_profile_id),
        ) if not value)
        if missing:
            return WorkspacePlan("retrieval", experience.title if experience else "Knowledge Assistant", "Source-grounded questions require an indexed Knowledge collection and Retrieval Profile.", None, experience_id=experience_id, missing_requirements=missing, capabilities=capabilities or ("retrieval", "citations"))
        return WorkspacePlan("retrieval", title, "The question requires targeted retrieval and source provenance.", build_retrieval_adapter(title, collection_id or "", retrieval_profile_id or "", "Analyze the question and explain the supported answer."), experience_id=experience_id, capabilities=capabilities or ("retrieval", "citations"))

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