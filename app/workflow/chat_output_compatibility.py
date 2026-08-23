"""Static compatibility analysis for Business Chat's visible output contract."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.runtime.schema import WorkflowSpec


ChatOutputKind = Literal["text", "code", "image", "pdf", "docx", "pptx", "xlsx"]


class ChatOutputCompatibility(BaseModel):
    supported: bool
    detected_types: list[ChatOutputKind] = Field(default_factory=list)
    fallback_to_text: bool = False
    warnings: list[str] = Field(default_factory=list)


_NODE_OUTPUTS: dict[str, ChatOutputKind] = {
    "OpenAIImageGenerationAgent": "image",
    "DynamicFigureAgent": "image",
    "PDFProposalRenderer": "pdf",
    "DOCXProposalRenderer": "docx",
    "HorizonDOCXProposalRenderer": "docx",
    "PowerPointProposalSlides": "pptx",
}


def _terminal_ids(spec: WorkflowSpec) -> set[str]:
    if isinstance(spec.exit, str):
        return {spec.exit}
    if spec.exit:
        return set(spec.exit)
    outgoing = {edge.from_ for edge in spec.edges}
    return {node.id for node in spec.nodes if node.id not in outgoing}


def analyze_chat_output(spec: WorkflowSpec) -> ChatOutputCompatibility:
    """Describe whether a workflow has a meaningful normalized Chat result."""
    detected: set[ChatOutputKind] = set()
    warnings: list[str] = []
    terminal_ids = _terminal_ids(spec)
    by_id = {node.id: node for node in spec.nodes}

    # Artifacts are collected from every completed node by the frontend, not
    # only explicit workflow projection exits.
    for node in spec.nodes:
        kind = _NODE_OUTPUTS.get(node.type)
        if kind:
            detected.add(kind)

    for node_id in terminal_ids:
        node = by_id.get(node_id)
        if node is None:
            continue
        if node.type == "EndAgent":
            mode = str(node.config.get("mode") or "workflow_result")
            if mode == "chat_response":
                message = node.config.get("chat_message")
                if isinstance(message, str) and message.strip():
                    detected.add("code" if "```" in message else "text")
            elif mode == "custom_response":
                message = node.config.get("message")
                if isinstance(message, str) and message.strip():
                    detected.add("code" if "```" in message else "text")
            else:
                outputs = node.config.get("outputs")
                if isinstance(outputs, list) and outputs:
                    for output in outputs:
                        if not isinstance(output, dict):
                            continue
                        key = str(output.get("key") or "").lower()
                        value = output.get("value_from")
                        value_text = value.lower() if isinstance(value, str) else ""
                        if "code" in key or "```" in value_text:
                            detected.add("code")
                        elif value_text.endswith(".xlsx") or "xlsx_key" in key:
                            detected.add("xlsx")
                        else:
                            detected.add("text")
            continue

        kind = _NODE_OUTPUTS.get(node.type)
        if kind:
            detected.add(kind)
        else:
            # Runtime outputs are typed and the normalizer renders arbitrary
            # scalar/structured terminal output as readable labelled text.
            detected.add("text")

    # An explicit output projection is also meaningful even when its selected
    # nodes are not graph exits.
    if spec.output and spec.output.nodes:
        detected.add("text")

    fallback = "text" in detected and any(
        by_id[node_id].type != "EndAgent"
        for node_id in terminal_ids
        if node_id in by_id
    )
    if fallback:
        warnings.append(
            "Structured terminal output will be normalized to readable text in Chat.",
        )
    if not detected:
        warnings.append(
            "The workflow has no non-empty Chat response, mapped result, or supported artifact output.",
        )
    order: list[ChatOutputKind] = ["text", "code", "image", "pdf", "docx", "pptx", "xlsx"]
    return ChatOutputCompatibility(
        supported=bool(detected),
        detected_types=[kind for kind in order if kind in detected],
        fallback_to_text=fallback,
        warnings=warnings,
    )