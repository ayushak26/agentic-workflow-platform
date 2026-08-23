from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request

import app.api.chat_workspace as workspace_api
from app.runtime.preflight import preflight_workflow_yaml
from app.security.dependencies import CurrentUser
from app.security.rbac import Role
from app.workflow.chat_workspace_planner import (
    CHAT_ANSWER_MODEL,
    EXPERIENCES,
    build_artifact_adapter,
    build_scientific_skill_adapter,
    build_file_adapter,
    build_grounded_diagram_adapter,
    build_image_generation_adapter,
    build_llm_adapter,
    build_existing_workflow_chat_adapter,
    build_mcp_adapter,
    build_multimodal_file_adapter,
    build_rag_adapter,
    build_retrieval_adapter,
    build_vision_adapter,
    build_web_adapter,
    plan_workspace,
)
from app.runtime.loader import load_workflow_from_string
from app.workflow.chat_workflow_store import ChatWorkflowStore
from tests.fake_mongo import InMemoryDB


ALICE = CurrentUser("alice", Role.CONSULTANT, session_id="alice-scope")


def request(db: InMemoryDB) -> Request:
    return cast(Request, SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(services={"audit_db": db})),
    ))


def service_request(**services: object) -> Request:
    return cast(Request, SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(services=services)),
    ))


@pytest.mark.asyncio
async def test_chat_workflow_labels_preserve_authored_titles_without_llm():
    llm = SimpleNamespace(complete_structured=AsyncMock())
    result = await workspace_api.chat_workflow_labels(
        workspace_api.WorkflowLabelsRequest(workflows=[{
            "name": "w09_it_helpdesk_access_request",
            "title": "Internal IT Helpdesk and Access Request",
            "description": "Routes employee support and access requests.",
            "use_case": "it_operations",
        }]),
        service_request(llm=llm),
        ALICE,
    )
    assert result == {"labels": {
        "w09_it_helpdesk_access_request": "Internal IT Helpdesk and Access Request",
    }}
    llm.complete_structured.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_workflow_labels_batch_generic_names_through_gpt5_and_cache():
    workspace_api._WORKFLOW_LABEL_CACHE.clear()
    generated = workspace_api.GeneratedWorkflowLabels(labels=[
        workspace_api.GeneratedWorkflowLabel(name="pump", label="Pump Request Support"),
        workspace_api.GeneratedWorkflowLabel(name="pump_with_database", label="Pump Records Lookup"),
    ])
    llm = SimpleNamespace(complete_structured=AsyncMock(return_value=generated))
    body = workspace_api.WorkflowLabelsRequest(workflows=[
        {"name": "pump", "title": "Pump", "description": "Answers pump requests.", "use_case": "generic"},
        {"name": "pump_with_database", "title": "Pump with database", "description": "Uses pump records.", "use_case": "generic"},
    ])

    first = await workspace_api.chat_workflow_labels(body, service_request(llm=llm), ALICE)
    second = await workspace_api.chat_workflow_labels(body, service_request(llm=llm), ALICE)

    assert first == second == {"labels": {
        "pump": "Pump Request Support",
        "pump_with_database": "Pump Records Lookup",
    }}
    llm.complete_structured.assert_awaited_once()
    assert llm.complete_structured.await_args.kwargs["model"] == CHAT_ANSWER_MODEL


@pytest.mark.asyncio
async def test_chat_workflow_labels_fall_back_when_llm_is_unavailable():
    workspace_api._WORKFLOW_LABEL_CACHE.clear()
    result = await workspace_api.chat_workflow_labels(
        workspace_api.WorkflowLabelsRequest(workflows=[{
            "name": "sample_invoice_review_workflow",
            "title": "Workflow",
            "description": "Reviews invoices for exceptions.",
            "use_case": "finance",
        }]),
        service_request(),
        ALICE,
    )
    assert result == {"labels": {
        "sample_invoice_review_workflow": "Sample Invoice Review Workflow",
    }}


def test_registry_covers_all_twenty_product_experiences():
    assert len(EXPERIENCES) == 20
    assert len({item.id for item in EXPERIENCES}) == 20
    assert {item.id for item in EXPERIENCES} >= {
        "document_qa", "research_to_presentation", "proposal_generator",
        "chat_workflow", "multi_workflow_project",
    }


def test_explicit_scientific_skill_builds_a_single_approved_skill_node():
    plan = plan_workspace("Review the evidence quality.", skill_name="literature-review")
    spec = load_workflow_from_string(plan.yaml_text or "")
    node = next(item for item in spec.nodes if item.type == "ScientificSkillAgent")

    assert plan.capabilities == ("scientific_skill", "literature-review")
    assert node.config["skills"] == ["literature-review"]
    assert node.config["auto_select"] is False
    assert node.config["objective"] == "{{outputs.start.message}}"


def test_rag_adapter_rewrites_only_the_retrieval_query_with_bounded_chat_context():
    spec = load_workflow_from_string(build_rag_adapter("RAG", "rag-agent"))
    rewrite = next(item for item in spec.nodes if item.id == "rewrite_query")
    rag = next(item for item in spec.nodes if item.id == "rag")

    assert spec.inputs["conversation_summary"].required is False
    assert rewrite.config["input_fields"][0]["value"] == "{{outputs.start.message}}"
    assert rewrite.config["input_fields"][1]["value"] == "{{inputs.conversation_summary}}"
    assert "If current_question is already standalone, copy it exactly" in rewrite.config["instructions"]
    assert rag.config["query"] == "{{outputs.rewrite_query.parsed.retrieval_query}}"


def test_rag_adapter_enforces_selected_knowledge_documents():
    spec = load_workflow_from_string(build_rag_adapter("RAG", "rag-agent", ["doc-a", "doc-b"]))
    rag = next(item for item in spec.nodes if item.id == "rag")
    assert rag.config["document_ids"] == ["doc-a", "doc-b"]


@pytest.mark.parametrize("yaml_text", [
    build_llm_adapter(),
    build_scientific_skill_adapter("literature-review"),
    build_file_adapter("File Analyst", "Analyze the files."),
    build_retrieval_adapter("Knowledge", "collection", "profile", "Answer."),
    build_rag_adapter("RAG", "rag-agent"),
    build_artifact_adapter("Report", "pdf", with_files=True),
    build_artifact_adapter("Presentation", "pptx", with_files=True),
    build_artifact_adapter("Document", "docx", with_files=True),
    build_image_generation_adapter(),
    build_grounded_diagram_adapter(),
    build_vision_adapter("Vision"),
    build_web_adapter("Web"),
    build_multimodal_file_adapter("Mixed", "Analyze."),
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


@pytest.mark.parametrize("objective", [
    "What is the current weather in Amsterdam?",
    "What is the weather in Amsterdam?",
    "Show me today's Amsterdam forecast.",
])
def test_weather_questions_use_current_web_information(objective: str):
    plan = plan_workspace(objective)
    assert plan.kind == "web"
    assert "type: WebSearchAgent" in (plan.yaml_text or "")
    assert "weather API" not in (plan.yaml_text or "")


@pytest.mark.parametrize("yaml_text", [
    build_llm_adapter(),
    build_file_adapter("File Analyst", "Analyze the files."),
    build_retrieval_adapter("Knowledge", "collection", "profile", "Answer."),
    build_vision_adapter("Vision"),
    build_web_adapter("Web"),
    build_mcp_adapter("MCP", "business_records", "find_customer"),
])
def test_generated_chat_answer_steps_use_one_preconfigured_model_and_answer_field(yaml_text):
    assert f"selected_model: {CHAT_ANSWER_MODEL}" in yaml_text
    assert f"model: {CHAT_ANSWER_MODEL}" in yaml_text
    assert "name: answer" in yaml_text
    assert "{{outputs.answer.parsed.answer}}" in yaml_text


def test_direct_files_do_not_force_indexing_or_rag():
    plan = plan_workspace("Summarize these files.", has_attachments=True)
    assert plan.kind == "files"
    assert "WorkflowFileLoader" in (plan.yaml_text or "")
    assert "KnowledgeRetrieval" not in (plan.yaml_text or "")


def test_file_answers_resolve_subject_names_across_pdf_ocr_variants_and_complete_diagrams():
    yaml_text = build_file_adapter(
        "File Analyst",
        "Analyze the requested material and return structured, actionable findings.",
    )
    spec = load_workflow_from_string(yaml_text)
    answer = next(node for node in spec.nodes if node.id == "answer")
    instructions = str(answer.config["instructions"])
    assert "Match names and subjects semantically and case-insensitively" in instructions
    assert "PDF/OCR layout artifacts" in instructions
    assert "Never claim that a subject is absent" in instructions
    assert "Identify genuine source limitations plainly" in instructions
    assert "Never return null" in instructions
    assert answer.config["reject_empty_fields"] == ["answer"]
    assert answer.config["output_fields"][0]["description"].startswith("A substantive answer")


def test_attached_architecture_diagram_uses_grounded_image_generation():
    plan = plan_workspace(
        "Explain the architecture of Eurskem AI and create a simple architecture diagram.",
        has_attachments=True,
        attachment_categories=["document"],
    )
    assert plan.kind == "artifact"
    assert plan.capabilities == ("files", "diagram", "image")
    assert "type: WorkflowFileLoader" in (plan.yaml_text or "")
    assert "id: diagram_plan" in (plan.yaml_text or "")
    assert "type: OpenAIImageGenerationAgent" in (plan.yaml_text or "")
    assert "{{outputs.diagram_plan.parsed.image_prompt}}" in (plan.yaml_text or "")
    assert "{{outputs.generate_image.minio_key}}" in (plan.yaml_text or "")


def test_flowchart_from_attached_pdf_is_not_misrouted_to_pdf_export():
    plan = plan_workspace(
        "Create a flowchart from this PDF.",
        has_attachments=True,
        attachment_categories=["document"],
    )
    assert plan.kind == "artifact"
    assert "OpenAIImageGenerationAgent" in (plan.yaml_text or "")
    assert "PDFProposalRenderer" not in (plan.yaml_text or "")


@pytest.mark.parametrize("objective", [
    "Create a infographics to learn the docx.",
    "Create an infographic to learn this document.",
    "Make a study info graphic from the attached Word document.",
])
def test_attached_learning_infographic_uses_grounded_image_generation(objective):
    plan = plan_workspace(
        objective,
        has_attachments=True,
        attachment_categories=["document"],
    )
    assert plan.kind == "artifact"
    assert plan.title == "Learning Infographic"
    assert plan.capabilities == ("files", "infographic", "image")
    assert "type: WorkflowFileLoader" in (plan.yaml_text or "")
    assert "type: OpenAIImageGenerationAgent" in (plan.yaml_text or "")
    assert "DOCXProposalRenderer" not in (plan.yaml_text or "")
    assert "PDFProposalRenderer" not in (plan.yaml_text or "")
    spec = load_workflow_from_string(plan.yaml_text or "")
    visual_plan = next(node for node in spec.nodes if node.id == "diagram_plan")
    assert "learning infographic" in str(visual_plan.config["instructions"])
    assert "PDF/DOCX/OCR" in str(visual_plan.config["instructions"])


def test_general_automatically_combines_images_and_readable_files():
    plan = plan_workspace(
        "Compare the screenshot with the spreadsheet and explain the differences.",
        has_attachments=True,
        attachment_categories=["image", "spreadsheet"],
    )
    assert plan.kind == "files"
    assert "WorkflowFileLoader" in (plan.yaml_text or "")
    assert "KimiVisionAgent" in (plan.yaml_text or "")
    assert "image_analysis" in (plan.yaml_text or "")


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


def test_existing_workflow_chat_wrapper_uses_llm_input_subprocess_and_llm_output():
    workflow_name = "crm_aware_customer_triage"
    selected_yaml = Path(f"workflows/{workflow_name}.yaml").read_text(encoding="utf-8")
    adapter = build_existing_workflow_chat_adapter(
        workflow_name,
        load_workflow_from_string(selected_yaml),
    )
    assert adapter.count("type: TransformAgent") == 2
    assert f"workflow: {workflow_name}" in adapter
    assert "type: SubprocessAgent" in adapter
    assert "message: '{{outputs.prepare_inputs.parsed.message}}'" in adapter
    assert "subject: '{{outputs.prepare_inputs.parsed.subject}}'" in adapter
    assert "{{outputs.run_workflow.result}}" in adapter
    assert "{{outputs.answer.parsed.answer}}" in adapter
    report = preflight_workflow_yaml(adapter, compile_graph=True)
    assert report.valid, [issue.message for issue in report.errors]


def test_chatbot_workflow_wrapper_still_uses_universal_llm_input_and_output_adapters():
    workflow_name = "w01_intelligent_customer_inquiry_resolution"
    selected_yaml = Path(f"workflows/{workflow_name}.yaml").read_text(encoding="utf-8")
    adapter = build_existing_workflow_chat_adapter(
        workflow_name,
        load_workflow_from_string(selected_yaml),
    )
    spec = load_workflow_from_string(adapter)
    subprocess = next(node for node in spec.nodes if node.id == "run_workflow")
    assert subprocess.config["inputs"]["message"] == "{{outputs.prepare_inputs.parsed.message}}"
    assert subprocess.config["inputs"]["attachments"] == "{{outputs.start.attachments}}"
    assert any(node.id == "prepare_inputs" for node in spec.nodes)
    assert any(node.id == "answer" for node in spec.nodes)


def test_universal_wrapper_populates_required_email_intake_contract_from_llm_and_runtime_metadata():
    workflow_name = "verder_email_intake"
    selected_yaml = Path(f"workflows/{workflow_name}.yaml").read_text(encoding="utf-8")
    adapter = build_existing_workflow_chat_adapter(
        workflow_name,
        load_workflow_from_string(selected_yaml),
        runtime_metadata={
            "processed_at": "2026-08-23T22:00:00+00:00",
            "requested_at": "2026-08-23T22:00:00+00:00",
            "source_label": "Chat message",
        },
    )
    spec = load_workflow_from_string(adapter)
    prepare = next(node for node in spec.nodes if node.id == "prepare_inputs")
    subprocess = next(node for node in spec.nodes if node.id == "run_workflow")
    reply = next(node for node in spec.nodes if node.id == "reply")

    assert set(prepare.config["output_schema"]) >= {"email_text", "source_file", "processed_at"}
    assert "2026-08-23T22:00:00+00:00" in prepare.config["prompt_template"]
    assert subprocess.config["inputs"] == {
        "email_text": "{{outputs.prepare_inputs.parsed.email_text}}",
        "source_file": "{{outputs.prepare_inputs.parsed.source_file}}",
        "processed_at": "{{outputs.prepare_inputs.parsed.processed_at}}",
    }
    assert reply.config["handoff"]["structured_result"] == "{{outputs.run_workflow.result}}"
    report = preflight_workflow_yaml(adapter, compile_graph=True)
    assert report.valid, [issue.message for issue in report.errors]


def test_every_saved_workflow_can_be_wrapped_for_chat():
    failures: dict[str, list[str]] = {}
    for path in sorted(Path("workflows").glob("*.yaml")):
        try:
            spec = load_workflow_from_string(path.read_text(encoding="utf-8"))
            wrapper = build_existing_workflow_chat_adapter(path.stem, spec)
            report = preflight_workflow_yaml(wrapper, compile_graph=True)
            if not report.valid:
                failures[path.name] = [issue.message for issue in report.errors]
        except Exception as exc:
            failures[path.name] = [str(exc)]
    assert failures == {}


@pytest.mark.asyncio
async def test_prepare_selected_workflow_persists_a_conversational_wrapper():
    db = InMemoryDB()
    result = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(
            objective="Route this customer request",
            selected_workflow="crm_aware_customer_triage",
        ),
        request(db), ALICE,
    )
    stored_yaml = db["chat_workflows"].docs[0]["yaml"]
    assert result["plan"]["kind"] == "existing_workflow"
    assert result["workflow"]["source_workflow_name"] == "crm_aware_customer_triage"
    assert "type: SubprocessAgent" in stored_yaml
    assert "workflow: crm_aware_customer_triage" in stored_yaml
    assert stored_yaml.count("type: TransformAgent") == 2


def test_artifact_requests_use_real_renderer_nodes():
    slides = plan_workspace("Turn this into an executive presentation", has_attachments=True)
    report = plan_workspace("Create a PDF report", has_attachments=True)
    document = plan_workspace("Create a Microsoft Word document", has_attachments=True)
    image = plan_workspace("Generate an image of a solar-powered city")
    assert slides.kind == report.kind == document.kind == image.kind == "artifact"
    assert "PowerPointProposalSlides" in (slides.yaml_text or "")
    assert "PDFProposalRenderer" in (report.yaml_text or "")
    assert "DOCXProposalRenderer" in (document.yaml_text or "")
    assert "OpenAIImageGenerationAgent" in (image.yaml_text or "")


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
async def test_prepare_reuses_one_managed_adapter_for_equivalent_general_requests():
    db = InMemoryDB()
    first = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(objective="Explain recursion simply"),
        request(db), ALICE,
    )
    second = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(objective="Explain dependency injection simply"),
        request(db), ALICE,
    )

    assert first["workflow"]["id"] == second["workflow"]["id"]
    assert first["workflow"]["managed"] is True
    assert len(db["chat_workflows"].docs) == 1
    assert db["chat_workflows"].docs[0]["adapter_key"] == "llm:default:auto:no-attachments:none:llm"


@pytest.mark.asyncio
async def test_prepare_reuses_rag_adapter_by_saved_agent_not_question_text():
    db = InMemoryDB()
    first = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(
            objective="What is the maintenance interval?",
            collection_id="collection-1", rag_agent_id="rag-1",
        ),
        request(db), ALICE,
    )
    second = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(
            objective="Which seal materials are supported?",
            collection_id="collection-1", rag_agent_id="rag-1",
        ),
        request(db), ALICE,
    )

    assert first["workflow"]["id"] == second["workflow"]["id"]
    assert len(db["chat_workflows"].docs) == 1
    assert db["chat_workflows"].docs[0]["adapter_key"] == "rag:rag-1:documents:all"


@pytest.mark.asyncio
async def test_managed_rag_adapter_identity_changes_with_document_scope():
    db = InMemoryDB()
    first = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(
            objective="What is the maintenance interval?", collection_id="collection-1",
            rag_agent_id="rag-1", document_ids=["doc-a"],
        ), request(db), ALICE,
    )
    second = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(
            objective="What is the maintenance interval?", collection_id="collection-1",
            rag_agent_id="rag-1", document_ids=["doc-b"],
        ), request(db), ALICE,
    )
    assert first["workflow"]["id"] != second["workflow"]["id"]


@pytest.mark.asyncio
async def test_managed_adapter_reuse_is_owner_scoped():
    db = InMemoryDB()
    alice = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(objective="Explain recursion"),
        request(db), ALICE,
    )
    bob = CurrentUser("bob", Role.CONSULTANT, session_id="bob-scope")
    bob_result = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(objective="Explain recursion"),
        request(db), bob,
    )

    assert alice["workflow"]["id"] != bob_result["workflow"]["id"]
    assert len(db["chat_workflows"].docs) == 2


@pytest.mark.asyncio
async def test_prepare_adopts_an_exact_legacy_workspace_adapter_without_claiming_user_workflows():
    db = InMemoryDB()
    plan = plan_workspace("Explain recursion")
    from app.api.chat_workflows import _private_yaml
    canonical_yaml = _private_yaml(plan.yaml_text or "")
    store = ChatWorkflowStore(db)
    legacy = await store.create(
        owner_scope_id="alice-scope", slug="workspace-legacy", display_name="AI Workspace",
        description="", yaml_text=canonical_yaml, source="imported",
    )
    await store.create(
        owner_scope_id="alice-scope", slug="my-authored-chat", display_name="My Authored Chat",
        description="", yaml_text=canonical_yaml, source="imported",
    )

    result = await workspace_api.prepare_chat_workspace(
        workspace_api.PlanWorkspaceRequest(objective="Explain another concept"),
        request(db), ALICE,
    )

    assert result["workflow"]["id"] == legacy.chat_workflow_id
    assert len(db["chat_workflows"].docs) == 2
    authored = next(item for item in db["chat_workflows"].docs if item["slug"] == "my-authored-chat")
    assert authored.get("managed", False) is False


@pytest.mark.asyncio
async def test_prepare_rejects_a_skill_that_is_not_loaded():
    db = InMemoryDB()
    catalog = SimpleNamespace(loaded_skill_names=("literature-review",))
    with pytest.raises(HTTPException) as exc_info:
        await workspace_api.prepare_chat_workspace(
            workspace_api.PlanWorkspaceRequest(
                objective="Analyze this study", skill_name="unapproved-skill",
            ),
            service_request(audit_db=db, scientific_skill_catalog=catalog),
            ALICE,
        )
    assert exc_info.value.status_code == 422
    assert db["chat_workflows"].docs == []


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