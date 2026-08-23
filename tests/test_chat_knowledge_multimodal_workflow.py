from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Type

import pytest
from pydantic import BaseModel

from app.knowledge.models import ProfileType, ResourceStatus
from app.retrieval.models import RetrievalFilters, RetrievalResult, RetrievedChunk
from app.runtime.executor import run_workflow
from app.runtime.loader import load_workflow_from_string
from app.runtime.preflight import preflight_workflow_yaml
from app.tools.image_io import GeneratedImage


WORKFLOW_PATH = Path("workflows/test_fixtures/chat_knowledge_five_models_image.yaml")
EXPECTED_MODELS = [
    "gpt-5-mini",
    "claude-sonnet-4-5",
    "claude-opus-5",
    "gpt-5.6-luna",
    "openrouter/anthropic/claude-sonnet-4.5",
]


class KnowledgeRepository:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    async def get_collection(self, scope: str, collection_id: str):
        self.scopes.append(scope)
        assert collection_id == "test-knowledge-source"
        return SimpleNamespace(
            collection_id=collection_id,
            name="Test Knowledge Source",
            status=ResourceStatus.READY,
            active_index_id="test-index",
        )

    async def get_profile(
        self, scope: str, profile_id: str, version: int | None, profile_type: ProfileType,
    ):
        del version
        self.scopes.append(scope)
        assert profile_id == "test-retrieval-profile"
        assert profile_type == ProfileType.RETRIEVAL
        return SimpleNamespace(profile_id=profile_id, status=ResourceStatus.ACTIVE)


class RetrievalService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def retrieve(self, request, *, owner_scope_id: str, llm=None):
        del llm
        self.calls.append((owner_scope_id, request.query))
        chunk = RetrievedChunk(
            chunk_id="chunk-policy-1",
            display_number=1,
            doc_id="doc-policy",
            document_id="doc-policy",
            source_version_id="source-v1",
            doc_title="Operations Handbook.pdf",
            doc_type="policy",
            text="The approved verification code is KS-4827.",
            page=4,
            section="Verification",
            metadata={"source_uri": "knowledge://operations-handbook"},
        )
        return RetrievalResult(
            query=request.query,
            rewritten_query=None,
            chunks=[chunk],
            candidates=[chunk],
            filters_applied=RetrievalFilters(
                session_id=owner_scope_id,
                collection_id="test-knowledge-source",
            ),
            timings_ms={"total_ms": 4.0},
            retrieval_request_id="retrieval-1",
            resolved_index_id="test-index",
            final_context=chunk.text,
            resolved_resources={"index_id": "test-index"},
        )


class FiveModelGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.selection_history: list[dict[str, Any]] = []

    def with_context(self, **kwargs):
        del kwargs
        return self

    def with_collection_id(self, collection_id: str):
        assert collection_id == "test-knowledge-source"
        return self

    async def complete_structured(
        self,
        *,
        model: str,
        response_model: Type[BaseModel],
        **kwargs: Any,
    ) -> BaseModel:
        del kwargs
        self.calls.append(model)
        if "answer" in response_model.model_fields:
            return response_model.model_validate({
                "answer": "The approved verification code is KS-4827 [1].",
                "image_prompt": "A clean shield icon containing KS-4827.",
            })
        return response_model.model_validate({
            "result": f"Validated stage {len(self.calls)} for KS-4827",
        })


class ImageGenerator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate(self, prompt: str, **kwargs: Any) -> GeneratedImage:
        self.calls.append({"prompt": prompt, **kwargs})
        return GeneratedImage(
            data=b"deterministic-png",
            model="google/gemini-3.1-flash-image",
            output_format="png",
            requested_model="google/gemini-3.1-flash-image",
        )


class ObjectStore:
    def __init__(self) -> None:
        self.puts: list[tuple[bytes, str, str | None]] = []

    def put_bytes(self, data: bytes, key: str, content_type: str | None = None) -> None:
        self.puts.append((data, key, content_type))


def workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_fixture_compiles_with_chat_knowledge_five_models_and_image():
    report = preflight_workflow_yaml(workflow_text(), compile_graph=True)
    assert report.valid, [issue.message for issue in report.errors]
    spec = load_workflow_from_string(workflow_text())
    assert [
        node.config["model"] for node in spec.nodes if node.type == "TransformAgent"
    ] == EXPECTED_MODELS
    assert {node.type for node in spec.nodes} >= {
        "StartAgent", "KnowledgeRetrieval", "TransformAgent",
        "OpenAIImageGenerationAgent", "EndAgent",
    }


@pytest.mark.asyncio
async def test_chat_input_executes_knowledge_five_models_image_and_chat_output():
    repository = KnowledgeRepository()
    retrieval = RetrievalService()
    llm = FiveModelGateway()
    images = ImageGenerator()
    store = ObjectStore()
    spec = load_workflow_from_string(workflow_text())

    result = await run_workflow(
        spec,
        {"message": "What is the approved verification code?"},
        session_id="tenant-chat",
        run_id="chat-multimodal-run",
        services={
            "knowledge_repository": repository,
            "retrieval_service": retrieval,
            "llm": llm,
            "image_generator": images,
            "object_store": store,
        },
    )

    assert result["status"] == "completed"
    assert llm.calls == EXPECTED_MODELS
    assert retrieval.calls == [
        ("tenant-chat", "What is the approved verification code?"),
    ]
    assert repository.scopes == ["tenant-chat", "tenant-chat"]
    assert images.calls[0]["prompt"] == "A clean shield icon containing KS-4827."
    assert images.calls[0]["provider"] == "openrouter"
    assert store.puts == [(
        b"deterministic-png",
        "workflows/chat-multimodal-run/images/visual.png",
        "image/png",
    )]

    state = result["state"]
    assert state["node_outputs"]["knowledge"]["citations"][0]["filename"] == (
        "Operations Handbook.pdf"
    )
    assert state["node_outputs"]["visual"]["minio_key"] == (
        "workflows/chat-multimodal-run/images/visual.png"
    )
    assert result["output"] == {
        "outcome": "answered",
        "message": "The approved verification code is KS-4827 [1].",
        "sources": [{
            "document_id": "doc-policy",
            "source_version_id": "source-v1",
            "chunk_id": "chunk-policy-1",
            "filename": "Operations Handbook.pdf",
            "page": 4,
            "section": "Verification",
            "evidence_status": "retrieved_not_verified",
        }],
        "handoff": {
            "image": "workflows/chat-multimodal-run/images/visual.png",
        },
    }