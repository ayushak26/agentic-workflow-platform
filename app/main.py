from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.config import settings
from app.observability.logging import get_logger
from app.storage.minio_client import get_object_store

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup builds the long-lived services that nodes consume via DI.
    Shutdown tears them down cleanly."""
    log.info("app.startup", env=settings.app_env, log_level=settings.log_level)

    # 1. Core clients — these survive the whole process lifetime
    from app.llm.gateway import get_llm_gateway
    from app.ingestion.embedder import get_embedder
    from app.retrieval.weaviate_client import get_weaviate_client

    llm = get_llm_gateway()
    embedder = get_embedder()
    weaviate_wrapper = get_weaviate_client()
    weaviate_client = weaviate_wrapper.connect()

    # 2. Retriever — wrap the standalone retrieve() function as a callable
    # that closes over its dependencies. RAGAgent calls this as
    # services["retriever"](query).
    from app.retrieval.retriever import retrieve

    async def retriever_service(q):
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
        weaviate_wrapper.close()


app = FastAPI(
    title="Agentic Workflow Platform",
    version="0.1.0",
    description="Optimoz-style platform for composable AI workflows.",
    lifespan=lifespan,
)

