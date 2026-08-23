"""Driver for the Verder Liquids assessment POC.

Runs workflows/verder_email_intake.yaml once per email (no loop/map node
type exists in this platform — see README_ASSESSMENT.md's architecture
section for why a thin driver script is the right call here, not a new
node type) and assembles the seven results into one results.json array,
matching 03_Output_schema.json exactly.

Real services throughout: the production LLM gateway (get_llm_gateway()),
the real snippet-runner sandbox for the assembly step, and the real
Knowledge Studio collection/retrieval profile created by
scripts/verder_setup_knowledge.py (only actually queried for the one email
that needs it — see the workflow's own conditional RAG gate).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.llm import get_llm_gateway
from app.retrieval.service import RetrievalService
from app.retrieval.weaviate_client import WeaviateClient
from app.knowledge.repository import KnowledgeRepository
from app.ingestion.embedder import Embedder
from app.runtime.snippet_client import SnippetRunnerClient
from app.runtime.executor import run_workflow
from app.runtime.hitl import resume_workflow_durable
from app.runtime.loader import load_workflow
from app.workflow.orchestration import BackgroundRunManager, finalize_run_result, start_new_run_record
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

WORKFLOW_PATH = Path("workflows/verder_email_intake.yaml")
EMAILS_DIR = Path("/Users/ayushkhandelwal/Downloads/Test case")
RESULTS_PATH = EMAILS_DIR / "results.json"
KNOWLEDGE_CONFIG_PATH = Path(__file__).resolve().parent / "verder_knowledge_config.json"

EMAIL_FILES = [(f"02_Email #{i}.txt", f"Email {i:02d}") for i in range(1, 32)]


async def main() -> None:
    """Compute the main."""
    workflow = load_workflow(WORKFLOW_PATH)
    knowledge_cfg = json.loads(KNOWLEDGE_CONFIG_PATH.read_text())

    mongo_client = AsyncIOMotorClient(settings.mongo_uri)
    db = mongo_client[settings.mongo_db]
    repository = KnowledgeRepository(db)
    weaviate_wrapper = WeaviateClient()
    raw_weaviate_client = weaviate_wrapper.connect()
    embedder = Embedder()
    llm = get_llm_gateway()
    retrieval_service = RetrievalService(
        weaviate_client=raw_weaviate_client,
        embedder=embedder,
        llm=llm,
        repository=repository,
    )

    manager = BackgroundRunManager(redis=None)
    services = {
        "llm": llm,
        "retrieval_service": retrieval_service,
        "cost_ledger": SimpleNamespace(record=lambda *a, **k: None),
        "audit_db": db,
        "background_run_manager": manager,
        "python_runner": SnippetRunnerClient(settings.snippet_runner_socket_path),
    }

    results = []
    try:
        for filename, label in EMAIL_FILES:
            email_path = EMAILS_DIR / filename
            email_text = email_path.read_text(encoding="utf-8", errors="replace")
            inputs = {
                "email_text": email_text,
                "source_file": label,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }
            print(f"processing {label} ({filename})...")
            import uuid
            run_id = f"verder-{label.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}"
            session_id = knowledge_cfg["owner_scope_id"]
            await start_new_run_record(
                db, run_id=run_id, session=session_id, spec=workflow,
                workflow_yaml=WORKFLOW_PATH.read_text(), inputs=inputs, collection_id="default",
            )
            result = await run_workflow(
                workflow, inputs, session_id=session_id, services=services, run_id=run_id,
            )
            # run_workflow() itself never touches Mongo — it just returns a
            # plain in-memory result. The workflow has no SubprocessAgent or
            # HITL gate anymore, so nothing else finalizes this run; call the
            # same finalizer the production API path uses, or run_history's
            # top-level status stays "running" forever even though every
            # node completed.
            await finalize_run_result(result, db=db, run_id=run_id, session=session_id)
            while manager._tasks:
                await asyncio.gather(*list(manager._tasks))
            run_doc = await db["run_history"].find_one({"run_id": run_id})
            if not run_doc or run_doc.get("status") != "completed":
                raise RuntimeError(f"{label} did not complete: {run_doc}")
            node_runs = run_doc.get("node_runs") or {}
            end_key = "end_result_with_knowledge" if "end_result_with_knowledge" in node_runs else "end_result_without_knowledge"
            record = node_runs[end_key]["output"]["result"]["record"]
            print(json.dumps(record, indent=2))
            results.append(record)
    finally:
        mongo_client.close()
        weaviate_wrapper.close()

    RESULTS_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {len(results)} records to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
