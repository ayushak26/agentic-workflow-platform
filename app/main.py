from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.observability.logging import get_logger
from app.storage.minio_client import get_object_store
from app.runtime.events import RunEventBus
from app.api.workflows import router as workflows_router

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup builds the long-lived services that nodes consume via DI.
    Shutdown tears them down cleanly."""
    log.info("app.startup", env=settings.app_env, log_level=settings.log_level)

    # 1. Core clients — best-effort. If a service is down, log and continue.
    # Same pattern as MCP below. The platform stays partially functional:
    # RAG workflows fail at run time with a clear error; non-RAG workflows
    # (Literal+Echo, Transform-only, etc.) run normally.
    from app.llm import get_llm_gateway
    from app.ingestion.embedder import get_embedder
    from app.retrieval.weaviate_client import get_weaviate_client

    llm = get_llm_gateway()
    embedder = get_embedder()
    weaviate_wrapper = None
    weaviate_client = None
    try:
        weaviate_wrapper = get_weaviate_client()
        weaviate_client = weaviate_wrapper.connect()
        log.info("weaviate.connected")
    except Exception as e:
        log.warning(
            "weaviate.startup_failed",
            error=str(e),
            error_type=type(e).__name__,
            hint="run `docker compose up -d weaviate` for RAG workflows",
        )

    # 2. Retriever — closure remains, but checks for missing client at call time
    from app.retrieval.retriever import retrieve

    async def retriever_service(q):
        if weaviate_client is None:
            raise RuntimeError(
            "Weaviate is not connected. Start it with `docker compose up -d weaviate` "
            "and restart the server."
            )
        return await retrieve(
        q, weaviate_client=weaviate_client, llm=llm, embedder=embedder,
    )

    # 3. MCP client — launch the subprocess. Best-effort: if MCP fails to
    # start, log and keep going. Only MCPAgent workflows will fail; the
    # rest of the platform still works.
    mcp_client = None
    try:
        from app.mcp.client import launch_mcp_session
        mcp_client = await launch_mcp_session()
    except Exception as e:
        log.warning("mcp.client.startup_failed", error=str(e), error_type=type(e).__name__)

    object_store = get_object_store()
    # 4. Bind everything to app.state so workflows.py can read it
    app.state.services = {
        "llm": llm,
        "embedder": embedder,
        "weaviate_client": weaviate_client,
        "retriever": retriever_service,
        "mcp_client": mcp_client,
        "object_store": object_store, 
        "event_bus": RunEventBus(),
    }

    try:
        yield
    finally:
        log.info("app.shutdown")
        if mcp_client is not None:
            try:
                await mcp_client.stop()
            except Exception as e:
                log.warning("mcp.client.shutdown_failed", error=str(e))
        if weaviate_wrapper is not None:
            try:
                weaviate_wrapper.close()
            except Exception as e:
                log.warning("weaviate.shutdown_failed", error=str(e))


app = FastAPI(
    title="Agentic Workflow Platform",
    version="0.1.0",
    description="Optimoz-style platform for composable AI workflows.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,    # MUST be False when origins=["*"] — CORS spec rule
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows_router)

