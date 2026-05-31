"""End-to-end test of the flagship workflow with stubbed services.

This is the test that catches wiring bugs the unit tests miss — fan-out
edge order, HITL state preservation across multiple pauses, template
resolution across long dependency chains. If this passes, the flagship
YAML is structurally sound."""
from pathlib import Path
import json

import app.nodes  # noqa: F401
from app.runtime.executor import run_workflow
from app.runtime.hitl import resume_workflow
from app.runtime.loader import load_workflow

FLAGSHIP_YAML = Path(__file__).parent.parent / "workflows" / "proposal_generation.yaml"
RFP_TEXT = (Path(__file__).parent / "fixtures" / "sample_rfp.txt").read_text()


# ---------- Stubs ----------------------------------------------------------
class _StubCompletion:
    """Mimics the gateway's LLMResponse: has a .text attribute."""
    def __init__(self, text: str):
        self.text = text
        
class StubLLM:
    """Scripted LLM. Matches by the first words of each prompt — unambiguous,
    because every prompt template starts with a distinctive opening line.

    Earlier version used substring matching in a dict, which collided when
    the compile_and_qa prompt referenced section labels by name. Now we
    anchor on prompt openings via startswith()."""

    def __init__(self):
        self.calls: list[dict] = []
        # Ordered list (not dict) — first match wins.
        self._routes: list[tuple[str, str]] = [
            ("Read this RFP and extract", json.dumps({
                "industry": "financial services",
                "client_pain_points": ["onboarding latency", "fraud loss"],
                "required_capabilities": ["process redesign", "ML feasibility"],
                "evaluation_criteria": ["case studies", "methodology"],
                "timeline_constraints": "8 weeks to initial findings",
            })),
            ("Synthesize the RFP intelligence", json.dumps({
                "engagement_summary": "Multi-stream financial services operations review.",
                "success_metrics": ["40% faster onboarding", "20% lower fraud loss"],
            })),
            ('Draft the "Project Understanding',  "Stub project understanding section. [1]"),
            ('Draft the "Approach and Methodology', "Stub approach section. [1] [2]"),
            ('Draft the "Relevant Experience',   "Stub experience section. [1]"),
            ('Draft the "Team Structure',        "Stub team section."),
            ('Draft the "Commercial Terms',      "Stub commercials section."),
            ("QA-pass each section", json.dumps({
                "project_understanding": "Cleaned project understanding.",
                "approach_and_methodology": "Cleaned approach.",
                "relevant_experience": "Cleaned experience.",
                "team_structure": "Cleaned team.",
                "commercial_terms": "Cleaned commercials.",
            })),
            ("Answer the question using ONLY", "Synthesized retrieval answer [1] [2]."),
        ]

    async def chat(self, *, model, messages, system=None, temperature=0.0, **_):
        prompt = messages[-1]["content"]
        self.calls.append({"model": model, "system": system, "prompt": prompt[:80]})
        stripped = prompt.lstrip()
        for marker, response in self._routes:
            if stripped.startswith(marker):
                return response
        return "STUB FALLBACK"
    
    async def complete(self, *, model, system=None, user=None,
                       temperature=0.0, max_tokens=1024, **_):
        """Plain-text completion. RAGAgent uses this and reads resp.text, so we
        return a small object with a .text attribute, not a bare string.
        Matches on user first, then system (the RAG generation_prompt marker
        lives in system, not user)."""
        self.calls.append({"method": "complete", "model": model})
        for field in (user, system):
            if not field:
                continue
            stripped = field.lstrip()
            for marker, response in self._routes:
                if stripped.startswith(marker):
                    return _StubCompletion(response)
        return _StubCompletion("STUB FALLBACK")
    
    async def complete_structured(
        self, *, model, system, user,
        response_model, temperature=0.0, max_tokens=1024, **_,
    ):
        """Schema path. Matches the same way chat() does — by prompt opening —
        then validates the queued JSON string into the node's response_model.
        The schema-bearing routes (rfp_intel, context_synthesis, compile_and_qa)
        already queue json.dumps(...) payloads, so they parse cleanly."""
        self.calls.append({
            "method": "complete_structured",
            "model": model,
            "response_model": response_model.__name__,
        })
        stripped = (user or "").lstrip()
        for marker, response in self._routes:
            if stripped.startswith(marker):
                return response_model.model_validate_json(response)
        # No match: return an empty instance so the failure is a validation
        # error you can read, not a silent KeyError three nodes downstream.
        return response_model.model_validate_json("{}")

class StubRetriever:
    """Returns a fixed RetrievalResult with two stubbed chunks."""

    async def __call__(self, q):
        # Import here so the module doesn't depend on retrieval imports at load
        from app.retrieval.models import RetrievalResult, RetrievedChunk
        chunks = [
            RetrievedChunk(
                chunk_id="cs-001", doc_id="cs-001",
                doc_title="Acme Bank Onboarding Redesign",
                doc_type="case_study",
                text="Reduced onboarding from 14 days to 3.", metadata={},
                hybrid_score=0.92,
            ),
            RetrievedChunk(
                chunk_id="meth-007", doc_id="meth-007",
                doc_title="Eurskem Process Re-engineering Method",
                doc_type="methodology",
                text="5-phase methodology: discover, design, pilot, scale, sustain.",
                metadata={}, hybrid_score=0.88,
            ),
        ]
        return RetrievalResult(
            query=q.query,
            rewritten_query=None,
            chunks=chunks,
            filters_applied=q.filters,
            timings_ms={"total_ms": 12.3},
        )


class StubObjectStore:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}
    def get_bytes(self, key): return self.blobs[key]
    def put_bytes(self, data, key, content_type=None):
        self.blobs[key] = data


# ---------- The test ------------------------------------------------------

async def test_flagship_workflow_full_run():
    spec = load_workflow(FLAGSHIP_YAML)
    services = {
        "llm": StubLLM(),
        "retriever": StubRetriever(),
        "object_store": StubObjectStore(),
    }

    # Phase 1: run until first HITL pause (approve_requirements)
    r = await run_workflow(
        spec,
        inputs={
            "rfp_text": RFP_TEXT,
            "client_name": "Acme Financial Services",
            "proposal_title": "Operations Excellence Proposal",
        },
        services=services,
    )
    assert r["status"] == "paused", f"Expected first HITL pause, got {r['status']}"

    # Phase 2: approve, run until second pause (approve_retrievals)
    r = await resume_workflow(r["run_id"], {"decision": "approve"})
    assert r["status"] == "paused", "Expected second HITL pause"

    # Phase 3: approve, run until third pause (approve_drafts).
    # Between gate 2 and gate 3, all FIVE drafters execute in parallel.
    r = await resume_workflow(r["run_id"], {"decision": "approve"})
    assert r["status"] == "paused", "Expected third HITL pause after parallel drafts"
    state = r["state"]
    for drafter in (
        "draft_understanding", "draft_approach", "draft_experience",
        "draft_team", "draft_commercials",
    ):
        assert drafter in state["node_outputs"], f"Missing drafter output: {drafter}"

    # Phase 4: approve drafts, workflow runs to completion (compile + PDF)
    r = await resume_workflow(r["run_id"], {"decision": "approve"})
    assert r["status"] == "completed", f"Expected completion, got {r['status']}"

    final_state = r["state"]
    pdf_output = final_state["node_outputs"]["generate_pdf"]
    assert pdf_output["template_used"] == "corporate"
    assert pdf_output["byte_size"] > 1000  # PDF should be non-trivial
    assert pdf_output["minio_key"].endswith("/proposal.pdf")

    # The actual PDF bytes are in the stub object store
    pdf_bytes = services["object_store"].blobs[pdf_output["minio_key"]]
    assert pdf_bytes.startswith(b"%PDF-"), "Output is not a valid PDF"

    # Audit log: 9 nodes (3 HITL pauses don't count as separate executions in
    # the audit log; they appear once each as resumed)
    audit_node_ids = {entry["node_id"] for entry in final_state["audit_log"]}
    expected = {
        "rfp_intel", "context_synthesis", "approve_requirements",
        "knowledge_retrieval", "approve_retrievals",
        "draft_understanding", "draft_approach", "draft_experience",
        "draft_team", "draft_commercials",
        "approve_drafts", "compile_and_qa", "generate_pdf",
    }
    assert expected.issubset(audit_node_ids), f"Missing nodes in audit: {expected - audit_node_ids}"