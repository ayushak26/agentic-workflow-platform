from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.datastructures import UploadFile

import app.nodes  # noqa: F401
from app.nodes.registry import NodeRegistry
from app.runtime.compiler import compile_workflow
from app.runtime.loader import load_workflow_from_string
from app.runtime.schema import WorkflowFileRef, WorkflowInputSpec
from app.workflow.file_inputs import (
    WorkflowFileInputError,
    extract_workflow_file_text,
    scope_token,
    store_upload,
    validate_workflow_inputs,
    workflow_input_prefix,
)


class MemoryObjectStore:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    def put_file(
        self,
        local_path: Path,
        key: str,
        content_type: str | None = None,
        extra_metadata: dict[str, str] | None = None,
    ):
        self.blobs[key] = local_path.read_bytes()
        return key

    def get_bytes(self, key: str) -> bytes:
        return self.blobs[key]

    def object_exists(self, key: str) -> bool:
        return key in self.blobs


def reference(
    session_id: str = "ayush",
    *,
    name: str = "brief.md",
    category: str = "markdown",
    extension: str = ".md",
) -> WorkflowFileRef:
    digest = "a" * 64
    return WorkflowFileRef(
        file_id=f"wf_{digest[:24]}",
        name=name,
        extension=extension,
        category=category,
        content_type="text/markdown",
        size_bytes=12,
        sha256=digest,
        minio_key=f"{workflow_input_prefix(session_id)}{digest}{extension}",
        parseable_text=category != "image",
    )


def test_file_input_schema_supports_categories_and_multiple_files():
    spec = WorkflowInputSpec(
        type="file",
        required=True,
        multiple=True,
        max_files=8,
        accept=["pdf", "document", "markdown", "presentation", "code", "image"],
    )
    assert spec.effective_max_files(20) == 8
    assert "image" in spec.accept


def test_single_file_input_rejects_multi_file_limit():
    with pytest.raises(ValidationError):
        WorkflowInputSpec(type="file", multiple=False, max_files=2)


async def test_upload_streams_to_session_scoped_object_storage():
    store = MemoryObjectStore()
    upload = UploadFile(
        filename="Concept Note.md",
        file=BytesIO(b"# Concept\n\nEvidence grounded."),
    )

    result = await store_upload(
        upload,
        session_id="ayush",
        object_store=store,
    )

    assert result.kind == "workflow_file"
    assert result.name == "Concept Note.md"
    assert result.category == "markdown"
    assert result.parseable_text is True
    assert result.minio_key.startswith(
        f"workflow-inputs/{scope_token('ayush')}/"
    )
    assert store.blobs[result.minio_key].startswith(b"# Concept")


async def test_run_validation_normalizes_file_references_and_checks_scope():
    store = MemoryObjectStore()
    ref = reference()
    store.blobs[ref.minio_key] = b"# Brief"
    specs = {
        "sources": WorkflowInputSpec(
            type="file",
            multiple=True,
            required=True,
            accept=["markdown"],
        )
    }

    normalized = await validate_workflow_inputs(
        specs,
        {"sources": [ref.model_dump()]},
        session_id="ayush",
        object_store=store,
    )
    assert normalized["sources"][0]["minio_key"] == ref.minio_key

    with pytest.raises(
        WorkflowFileInputError,
        match="outside this session",
    ):
        await validate_workflow_inputs(
            specs,
            {"sources": [ref.model_dump()]},
            session_id="different-user",
            object_store=store,
        )


async def test_run_validation_rejects_unaccepted_category():
    store = MemoryObjectStore()
    ref = reference(
        name="diagram.png",
        category="image",
        extension=".png",
    )
    store.blobs[ref.minio_key] = b"png"
    specs = {
        "sources": WorkflowInputSpec(
            type="file",
            accept=["pdf"],
        )
    }
    with pytest.raises(WorkflowFileInputError, match="not accepted"):
        await validate_workflow_inputs(
            specs,
            {"sources": ref.model_dump()},
            session_id="ayush",
            object_store=store,
        )


async def test_workflow_file_loader_extracts_markdown_without_llm_calls():
    store = MemoryObjectStore()
    ref = reference()
    store.blobs[ref.minio_key] = b"# Concept\n\nEvidence grounded."

    node_class = NodeRegistry.get("WorkflowFileLoader")
    node = node_class(
        "load",
        {
            "files": ref.model_dump(),
            "max_chars_per_file": 10_000,
        },
        services={"object_store": store},
    )
    output = await node.run(
        {"inputs": {}},
        node.config.model_dump(),
    )

    assert output["total_files"] == 1
    assert output["text_file_count"] == 1
    assert output["image_count"] == 0
    assert "Evidence grounded." in output["text"]


async def test_workflow_file_loader_keeps_images_as_references():
    ref = reference(
        name="site-plan.png",
        category="image",
        extension=".png",
    )
    node_class = NodeRegistry.get("WorkflowFileLoader")
    node = node_class(
        "load",
        {"files": [ref.model_dump()]},
        services={"object_store": MemoryObjectStore()},
    )

    output = await node.run(
        {"inputs": {}},
        node.config.model_dump(),
    )
    assert output["image_count"] == 1
    assert output["text"] == ""
    assert output["image_files"][0]["minio_key"] == ref.minio_key


async def test_hitl_document_override_extracts_scoped_markdown():
    store = MemoryObjectStore()
    ref = reference()
    store.blobs[ref.minio_key] = (
        b"# Replacement concept\n\nThis document overrides the draft."
    )

    result = await extract_workflow_file_text(
        ref,
        session_id="ayush",
        object_store=store,
        max_chars=10_000,
    )

    assert result["file"]["name"] == "brief.md"
    assert "Replacement concept" in result["text"]
    assert result["truncated"] is False


async def test_hitl_document_override_rejects_cross_session_reference():
    store = MemoryObjectStore()
    ref = reference()
    store.blobs[ref.minio_key] = b"# Private"

    with pytest.raises(
        WorkflowFileInputError,
        match="outside this session",
    ):
        await extract_workflow_file_text(
            ref,
            session_id="another-user",
            object_store=store,
        )


def test_file_input_demo_compiles_with_templated_file_references():
    workflow = load_workflow_from_string(
        Path("workflows/file_input_demo.yaml").read_text()
    )
    compile_workflow(workflow, services={})


def test_hitl_editor_demo_compiles():
    workflow = load_workflow_from_string(
        Path("workflows/hitl_editor_demo.yaml").read_text()
    )
    compile_workflow(workflow, services={})
