from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

import app.api.chat_workspace as workspace_api
from app.runtime.preflight import preflight_workflow_yaml
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from app.workflow.chat_workspace_planner import (
    EXPERIENCES,
    build_artifact_adapter,
    build_file_adapter,
    build_llm_adapter,
    build_mcp_adapter,
    build_rag_adapter,
    build_retrieval_adapter,
    build_vision_adapter,
    build_web_adapter,
    plan_workspace,
)
from tests.fake_mongo import InMemoryDB


ALICE = CurrentUser("alice", Role.CONSULTANT, session_id="alice-scope")


def request(db: InMemoryDB) -> Request:
    return cast(Request, SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(services={"audit_db": db})),
    ))


def test_registry_covers_all_twenty_product_experiences():
    assert len(EXPERIENCES) == 20
    assert len({item.id for item in EXPERIENCES}) == 20
    assert {item.id for item in EXPERIENCES} >= {
        "document_qa", "research_to_presentation", "proposal_generator",
        "chat_workflow", "multi_workflow_project",
    }


@pytest.mark.parametrize("yaml_text", [
    build_llm_adapter(),
    build_file_adapter("File Analyst", "Analyze the files."),
    build_retrieval_adapter("Knowledge", "collection", "profile", "Answer."),
    build_rag_adapter("RAG", "rag-agent"),
    build_artifact_adapter("Report", "pdf", with_files=True),
    build_artifact_adapter("Presentation", "pptx", with_files=True),
    build_vision_adapter("Vision"),
    build_web_adapter("Web"),
    build_mcp_adapter("MCP", "business_records", "find_customer"),
])
def test_every_adapter_is_strict_preflight_clean(yaml_text: str):
    report = preflight_workflow_yaml(yaml_text, compile_graph=True)
    assert report.valid, [issue.message for issue in report.errors]
    assert report.warnings == []
    assert report.tokens_spent == 0


def test_simple_question_uses_only_lightweight_llm_path():
    plan = plan_workspace("Explain recursion in simple language.")
    assert plan.kind == "llm"
    assert "TransformAgent" in (plan.yaml_text or "")
    assert "KnowledgeRetrieval" not in (plan.yaml_text or "")
    assert "RAGAgent" not in (plan.yaml_text or "")


def test_direct_files_do_not_force_indexing_or_rag():
    plan = plan_workspace("Summarize these files.", has_attachments=True)
    assert plan.kind == "files"
    assert "WorkflowFileLoader" in (plan.yaml_text or "")
    assert "KnowledgeRetrieval" not in (plan.yaml_text or "")


def test_saved_rag_agent_is_the_direct_grounded_answer_path():
    plan = plan_workspace(
        "According to our sources, what changed?", rag_agent_id="rag-1",
    )
    assert plan.kind == "retrieval"
    assert "RAGAgent" in (plan.yaml_text or "")
    assert "rag_agent_id: rag-1" in (plan.yaml_text or "")


def test_raw_retrieval_requires_real_collection_and_profile():
    blocked = plan_workspace("According to these reports, what changed?")
    assert blocked.kind == "retrieval"
    assert set(blocked.missing_requirements) == {"collection_id", "retrieval_profile_id"}

    ready = plan_workspace(
        "According to these reports, what changed?",
        collection_id="col-1", retrieval_profile_id="rp-1",
    )
    assert ready.missing_requirements == ()
    assert "KnowledgeRetrieval" in (ready.yaml_text or "")


def test_explicit_workflow_is_authoritative():
    plan = plan_workspace(
        "Just summarize this", selected_workflow="w01_intelligent_customer_inquiry_resolution",
    )
    assert plan.kind == "existing_workflow"
    assert plan.existing_workflow == "w01_intelligent_customer_inquiry_resolution"
    assert plan.yaml_text is None


def test_artifact_requests_use_real_renderer_nodes():
    slides = plan_workspace("Turn this into an executive presentation", has_attachments=True)
    report = plan_workspace("Create a PDF report", has_attachments=True)
    assert slides.kind == report.kind == "artifact"
    assert "PowerPointProposalSlides" in (slides.yaml_text or "")
    assert "PDFProposalRenderer" in (report.yaml_text or "")


def test_integrations_fail_closed_without_a_connection():
    plan = plan_workspace("Check Salesforce and send an email summary")
    assert plan.yaml_text is None
    assert set(plan.missing_requirements) == {"integration_connection", "integration_tool"}


def test_image_web_and_configured_mcp_use_existing_specialized_nodes():
    vision = plan_workspace(
        "Explain this image", has_attachments=True, attachment_categories=["image"],
    )
    web = plan_workspace("Find the latest public information about this market")
    mcp = plan_workspace(
        "Check the CRM record", integration_connection="business_records",
        integration_tool="find_customer",
    )
    assert vision.kind == "vision" and "KimiVisionAgent" in (vision.yaml_text or "")
    assert web.kind == "web" and "WebSearchAgent" in (web.yaml_text or "")
    assert mcp.kind == "integration" and "MCPToolAgent" in (mcp.yaml_text or "")


def test_previous_result_is_embedded_for_follow_up_without_rerunning_source_work():
    previous = {"analysis": {"themes": ["cost", "reliability"]}}
    plan = plan_workspace(
        "Turn that result into slides", preferred_output="pptx",
        previous_run_id="run-1", previous_result=previous,
    )
    assert plan.kind == "artifact"
    assert "previous_workflow_result" in (plan.yaml_text or "")
    assert "reliability" in (plan.yaml_text or "")
    assert "KnowledgeRetrieval" not in (plan.yaml_text or "")


@pytest.mark.parametrize("experience", EXPERIENCES, ids=lambda item: item.id)
def test_all_twenty_styles_have_an_executable_or_existing_foundation(experience):
    kwargs = {
        "experience_id": experience.id,
        "has_attachments": experience.default_plan in {"files", "artifact"},
        "collection_id": "col-1",
        "retrieval_profile_id": "rp-1",
    }
    plan = plan_workspace(experience.examples[0], **kwargs)
    assert plan.experience_id == experience.id
    assert plan.existing_workflow or plan.yaml_text
    if plan.yaml_text:
        report = preflight_workflow_yaml(plan.yaml_text, compile_graph=True)
        assert report.valid, [issue.message for issue in report.errors]


@pytest.mark.asyncio
async def test_prepare_persists_adapter_through_existing_private_chat_store():
    db = InMemoryDB()
    result = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(objective="Explain this simply"),
        request(db), ALICE,
    )
    assert result["plan"]["kind"] == "llm"
    assert result["workflow"]["source"] == "imported"
    assert result["workflow"]["visibility"] == "private"
    assert len(db["chat_workflows"].docs) == 1


@pytest.mark.asyncio
async def test_prepare_rejects_unconfigured_retrieval_without_persisting():
    db = InMemoryDB()
    with pytest.raises(HTTPException) as exc_info:
        await workspace_api.prepare_chat_workspace(
            workspace_api.PlanWorkspaceRequest(
                objective="Ask questions about my documents", experience_id="document_qa",
            ),
            request(db), ALICE,
        )
    assert exc_info.value.status_code == 422
    assert db["chat_workflows"].docs == []


@pytest.mark.asyncio
async def test_prepare_follow_up_reuses_owner_scoped_previous_run_output():
    db = InMemoryDB()
    db["run_history"].docs.append({
        "run_id": "prior-run", "session_id": "alice-scope",
        "workflow_name": "Research Analysis", "status": "completed",
        "outputs": {"analysis": {"themes": ["cost", "reliability"]}},
        "node_runs": {}, "schema_version": 5,
    })
    result = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(
            objective="Turn that result into slides",
            preferred_output="pptx", previous_run_id="prior-run",
        ),
        request(db), ALICE,
    )
    assert result["plan"]["kind"] == "artifact"
    stored_yaml = db["chat_workflows"].docs[0]["yaml"]
    assert "previous_workflow_result" in stored_yaml
    assert "reliability" in stored_yaml
    assert "KnowledgeRetrieval" not in stored_yaml