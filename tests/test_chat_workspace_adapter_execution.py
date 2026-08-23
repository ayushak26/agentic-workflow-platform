from __future__ import annotations

from typing import Any, Type

import pytest
from pydantic import BaseModel

from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string
from app.runtime.schema import WorkflowFileRef
from app.workflow.chat_workspace_planner import (
    build_artifact_adapter,
    build_file_adapter,
    build_llm_adapter,
)


class DeterministicLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete_structured(
        self, *, model: str, system: str, user: str,
        response_model: Type[BaseModel], **_: Any,
    ) -> BaseModel:
        self.calls.append({"model": model, "system": system, "user": user})
        return response_model.model_validate(self.responses.pop(0))


class ObjectStore:
    def __init__(self, blobs: dict[str, bytes] | None = None) -> None:
        self.blobs = dict(blobs or {})
        self.puts: list[tuple[str, str | None]] = []

    def get_bytes(self, key: str) -> bytes:
        return self.blobs[key]

    def put_bytes(self, data: bytes, key: str, content_type: str | None = None) -> None:
        self.blobs[key] = data
        self.puts.append((key, content_type))


async def execute(yaml_text: str, inputs: dict[str, Any], services: dict[str, Any], run_id: str):
    return await run_workflow(
        load_workflow_from_string(yaml_text), inputs,
        session_id="workspace-owner", run_id=run_id, services=services,
    )


@pytest.mark.asyncio
async def test_generated_lightweight_adapter_executes_through_the_real_runtime():
    llm = DeterministicLLM([{"answer": "Recursion solves a problem using a smaller instance of itself."}])
    result = await execute(
        build_llm_adapter(), {"message": "Explain recursion."}, {"llm": llm}, "workspace-llm",
    )

    assert result["status"] == "completed"
    assert result["output"]["message"].startswith("Recursion solves")
    assert "Explain recursion." in llm.calls[0]["user"]


@pytest.mark.asyncio
async def test_generated_file_adapter_extracts_uploaded_text_before_answering():
    ref = WorkflowFileRef(
        file_id="wf_notes", name="notes.md", extension=".md", category="document",
        content_type="text/markdown", size_bytes=35, sha256="a" * 64,
        minio_key="workflow-inputs/workspace/notes.md", parseable_text=True,
    )
    store = ObjectStore({ref.minio_key: b"# Interview notes\nReliability is the top concern."})
    llm = DeterministicLLM([{"answer": "Reliability is the dominant interview theme."}])
    result = await execute(
        build_file_adapter("Interview Analyst", "Identify recurring themes."),
        {"message": "What themes recur?", "attachments": [ref.model_dump()]},
        {"llm": llm, "object_store": store}, "workspace-files",
    )

    assert result["status"] == "completed"
    assert result["output"]["message"] == "Reliability is the dominant interview theme."
    assert "Reliability is the top concern" in llm.calls[0]["user"]
    assert result["state"]["node_outputs"]["load_files"]["text_file_count"] == 1


@pytest.mark.parametrize(
    ("output", "magic", "content_type"),
    [
        ("pdf", b"%PDF-", "application/pdf"),
        ("pptx", b"PK", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ],
)
@pytest.mark.asyncio
async def test_generated_artifact_adapters_render_real_files(output: str, magic: bytes, content_type: str):
    llm = DeterministicLLM([{
        "title": "Workspace Brief",
        "executive_summary": "A concise grounded summary.",
        "key_findings": "Reliability is the leading concern.",
        "risks": "Evidence is limited to one source.",
        "recommendations": "Validate the finding with more interviews.",
    }])
    store = ObjectStore()
    result = await execute(
        build_artifact_adapter("Workspace Brief", output, with_files=False),
        {"message": f"Create a {output} brief."},
        {"llm": llm, "object_store": store}, f"workspace-{output}",
    )

    key = result["output"]["handoff"][output]
    assert result["status"] == "completed"
    assert store.blobs[key].startswith(magic)
    assert store.puts == [(key, content_type)]