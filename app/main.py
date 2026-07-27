"""FastAPI application entry point for Eurskem AI — Agentic Workflow Platform.

Lifespan responsibilities:
- Build all service clients once at startup (Mongo, Weaviate, MinIO, Redis).
- Wire cost ledger, LLM gateway, and event bus into the shared services dict.
- Graceful shutdown closes all connections.

Services dict keys (consumed by routes and the runtime executor):
  mongo          — PyMongo MongoClient
  db             — PyMongo Database (alexos)
  cost_ledger    — CostLedger instance
  weaviate_client — Weaviate client
  object_store   — MinIO client
  redis          — aioredis client
  llm            — RegistryLLMGateway singleton
  event_bus      — RunEventBus for WebSocket live streaming
"""
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.config import settings
from app.observability.logging import configure_logging, get_logger
from app.observability.cost_ledger import CostLedger
from app.llm.registry import get_llm_gateway
from app.runtime.events import RunEventBus
from app.ingestion.collections import CollectionRegistry
from app.workflow.run_history import ensure_indexes as ensure_run_indexes
from app.proposal_graph.workspace_store import ProposalWorkspaceStore

from app.api import health
from app.api.auth import router as auth_router
from app.api.workflows import router as workflows_router
from app.api.cost import router as cost_router
from app.api.eval import router as eval_router
from app.api.inspect import router as inspect_router
from app.api import audit as audit_api
from app.api import runs as runs_api
from app.api import proposals as proposals_api

from app.db.mongo import DB_NAME

from functools import partial

configure_logging(settings.environment)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("eurskem_ai.startup", environment=settings.environment)

    services: dict = {}

    # ── MongoDB ────────────────────────────────────────────────────────────────
    try:
        from pymongo import MongoClient as PyMongoClient
        mongo_client = PyMongoClient(
            settings.mongo_uri, serverSelectionTimeoutMS=3000
        )
        mongo_client.server_info()          # fail fast if Mongo is down
        db = mongo_client[settings.mongo_db]

        # Async motor wrapper — eval + ingestion call its typed CRUD methods
        # (save_scorecard, list_scorecards, manifests). Distinct from the raw
        # pymongo Database below, which the (sync) CostLedger needs.
        from app.db.mongo import MongoClient as AsyncMongo
        services["mongo"] = AsyncMongo(settings.mongo_uri)
        services["collection_registry"] = CollectionRegistry(services["mongo"])
        services["audit_db"] = services["mongo"]._ensure_client()[DB_NAME]
        try:
            await ensure_run_indexes(services["audit_db"])
            await ProposalWorkspaceStore(
                services["audit_db"],
                None,
            ).ensure_indexes()
            logger.info("run_history.indexes_ensured")
        except Exception as exc:
            logger.warning("run_history.indexes_failed", error=str(exc))

        services["db"] = db                 # raw pymongo Database for CostLedger
        services["cost_ledger"] = CostLedger(db)
        logger.info("mongo.connected")
    except Exception as exc:
        logger.warning("mongo.unavailable", error=str(exc))
        services["cost_ledger"] = CostLedger(None)

    # ── Weaviate ───────────────────────────────────────────────────────────────
    try:
        import weaviate
        weaviate_client = weaviate.connect_to_local(
            host=settings.weaviate_host,
            port=settings.weaviate_port,
        )
        services["weaviate_client"] = weaviate_client
        logger.info("weaviate.connected")
    except Exception as exc:
        logger.warning("weaviate.unavailable", error=str(exc))

    # ── Object store (S3-compatible, boto3-backed) ─────────────────────────────
    try:
        from app.storage.minio_client import get_object_store
        services["object_store"] = get_object_store()
        logger.info("object_store.ready")
    except Exception as exc:
        logger.warning("object_store.unavailable", error=str(exc))

    # ── Redis ──────────────────────────────────────────────────────────────────
    try:
        redis_client = await aioredis.from_url(settings.redis_url)
        await redis_client.ping()
        services["redis"] = redis_client
        logger.info("redis.connected")
    except Exception as exc:
        logger.warning("redis.unavailable", error=str(exc))

    # ── LLM gateway ────────────────────────────────────────────────────────────
    # Singleton — shared across all requests. Nodes call with_context() to get
    # a cost-tracking clone bound to their run_id/session_id/node_id.
    services["llm"] = get_llm_gateway()
    logger.info("llm_gateway.ready")

    # ── MCP client ───────────────────────────────────────────────────────────
    # Launches the MCP server as a stdio subprocess and keeps one session open
    # for the app's lifetime. The MCPAgent node calls list_tools()/call_tool().
    try:
        from app.mcp.client import launch_mcp_session
        services["mcp_client"] = await launch_mcp_session()
        logger.info("mcp_client.ready")
    except Exception as exc:
        logger.warning("mcp_client.unavailable", error=str(exc))

    if "weaviate_client" in services:
        from app.retrieval import retrieve
        from app.ingestion.embedder import get_embedder
        _embedder = get_embedder()
        services["embedder"] = _embedder
        _wv = services["weaviate_client"]
        _llm = services["llm"]
        _registry = services["collection_registry"] 
        services["retriever"] = lambda q, llm=None: retrieve(
        q,
        weaviate_client=_wv,
        llm=llm or _llm,        # caller can pass a context-bound gateway
        embedder=_embedder,
        collection_registry=_registry,
        )
        logger.info("retriever.ready")
    else:
        logger.warning("retriever.unavailable", reason="weaviate not connected")

    # ── Event bus ─────────────────────────────────────────────────────────────
    # In-process pub/sub for WebSocket live run streaming.
    services["event_bus"] = RunEventBus()
    logger.info("event_bus.ready")

    app.state.services = services
    yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("eurskem_ai.shutdown")
    if "mongo" in services:
        services["mongo"].close()
    if "weaviate_client" in services:
        services["weaviate_client"].close()
    if "redis" in services:
        await services["redis"].aclose()
    if "mcp_client" in services:
        await services["mcp_client"].stop()    


app = FastAPI(
    title="Eurskem AI — Agentic Workflow Platform",
    description="Built by Rukainnovation for Eurskem",
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────────────────────────────
# Dev: allow the Vite dev server. Tighten to eurskem domain in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Prometheus ─────────────────────────────────────────────────────────────────
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(auth_router)
app.include_router(cost_router)
app.include_router(workflows_router)
app.include_router(eval_router)
app.include_router(inspect_router)
app.include_router(audit_api.router)
app.include_router(runs_api.router)
app.include_router(proposals_api.router)
