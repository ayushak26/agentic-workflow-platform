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
import asyncio

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from prometheus_client import make_asgi_app

from app.config import settings
from app.observability.logging import configure_logging, get_logger
from app.observability.cost_ledger import CostLedger
from app.llm.registry import get_llm_gateway
from app.runtime.events import RunEventBus
from app.ingestion.collections import CollectionRegistry
from app.workflow.run_history import ensure_indexes as ensure_run_indexes
from app.proposal_graph.workspace_store import ProposalWorkspaceStore
from app.security.users import ensure_user_indexes
from app.security.middleware import (
    RedisRateLimitMiddleware,
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
)

from app.api import health
from app.api.auth import router as auth_router
from app.api.workflows import router as workflows_router
from app.api.cost import router as cost_router
from app.api.eval import router as eval_router
from app.api.inspect import router as inspect_router
from app.api import audit as audit_api
from app.api import runs as runs_api
from app.api import proposals as proposals_api
from app.api import workflow_files as workflow_files_api

from app.db.mongo import DB_NAME

from functools import partial

configure_logging(settings.environment)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("eurskem_ai.startup", environment=settings.environment)

    services: dict = {}
    checkpointer_context = None

    # ── MongoDB ────────────────────────────────────────────────────────────────
    try:
        from pymongo import MongoClient as PyMongoClient
        mongo_client = PyMongoClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=int(
                settings.external_request_timeout_seconds * 1_000
            ),
            connectTimeoutMS=int(
                settings.external_request_timeout_seconds * 1_000
            ),
            socketTimeoutMS=int(
                settings.external_request_timeout_seconds * 1_000
            ),
        )
        mongo_client.server_info()          # fail fast if Mongo is down
        db = mongo_client[settings.mongo_db]

        # Async motor wrapper — eval + ingestion call its typed CRUD methods
        # (save_scorecard, list_scorecards, manifests). Distinct from the raw
        # pymongo Database below, which the (sync) CostLedger needs.
        from app.db.mongo import MongoClient as AsyncMongo
        services["mongo"] = AsyncMongo(settings.mongo_uri)
        services["mongo_sync_client"] = mongo_client
        services["collection_registry"] = CollectionRegistry(services["mongo"])
        services["audit_db"] = services["mongo"]._ensure_client()[DB_NAME]
        try:
            await ensure_run_indexes(services["audit_db"])
            await ensure_user_indexes(services["audit_db"])
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
        logger.warning(
            "mongo.unavailable",
            error_type=type(exc).__name__,
        )
        services["cost_ledger"] = CostLedger(None)

    # ── Weaviate ───────────────────────────────────────────────────────────────
    try:
        import weaviate
        from weaviate.auth import Auth
        from weaviate.config import AdditionalConfig, Timeout

        auth_credentials = (
            Auth.api_key(settings.weaviate_api_key)
            if settings.weaviate_api_key
            else None
        )
        weaviate_client = weaviate.connect_to_local(
            host=settings.weaviate_host,
            port=settings.weaviate_port,
            grpc_port=settings.weaviate_grpc_port,
            auth_credentials=auth_credentials,
            additional_config=AdditionalConfig(
                timeout=Timeout(
                    init=settings.external_request_timeout_seconds,
                    query=settings.external_request_timeout_seconds,
                    insert=settings.external_request_timeout_seconds,
                )
            ),
        )
        services["weaviate_client"] = weaviate_client
        logger.info("weaviate.connected")
    except Exception as exc:
        logger.warning(
            "weaviate.unavailable",
            error_type=type(exc).__name__,
        )

    # ── Object store (S3-compatible, boto3-backed) ─────────────────────────────
    try:
        from app.storage.minio_client import get_object_store
        services["object_store"] = get_object_store()
        await asyncio.to_thread(services["object_store"].ensure_bucket)
        logger.info("object_store.ready")
    except Exception as exc:
        logger.warning(
            "object_store.unavailable",
            error_type=type(exc).__name__,
        )

    # ── Redis ──────────────────────────────────────────────────────────────────
    try:
        redis_client = await aioredis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.external_request_timeout_seconds,
            socket_timeout=settings.external_request_timeout_seconds,
            health_check_interval=30,
        )
        await redis_client.ping()
        services["redis"] = redis_client
        logger.info("redis.connected")
    except Exception as exc:
        logger.warning(
            "redis.unavailable",
            error_type=type(exc).__name__,
        )

    # ── Durable LangGraph checkpointing ──────────────────────────────────────
    # The workflow thread_id is the run_id. Redis persists graph state across
    # FastAPI restarts, while Mongo keeps the operator-facing run/audit record.
    if "redis" in services:
        try:
            from langgraph.checkpoint.redis.aio import AsyncRedisSaver

            checkpointer_context = AsyncRedisSaver.from_conn_string(
                settings.redis_url
            )
            checkpointer = await checkpointer_context.__aenter__()
            await checkpointer.asetup()
            services["langgraph_checkpointer"] = checkpointer
            logger.info("langgraph_checkpointer.ready", backend="redis")
        except Exception as exc:
            if checkpointer_context is not None:
                await checkpointer_context.__aexit__(
                    type(exc),
                    exc,
                    exc.__traceback__,
                )
                checkpointer_context = None
            logger.warning(
                "langgraph_checkpointer.unavailable",
                error_type=type(exc).__name__,
            )

    # ── LLM gateway ────────────────────────────────────────────────────────────
    # Singleton — shared across all requests. Nodes call with_context() to get
    # a cost-tracking clone bound to their run_id/session_id/node_id.
    services["llm"] = get_llm_gateway()
    logger.info("llm_gateway.ready")

    # ── Embeddings + tenant-scoped semantic cache ───────────────────────────
    embedder = None
    if settings.openai_api_key:
        try:
            from app.ingestion.embedder import get_embedder

            embedder = get_embedder()
            services["embedder"] = embedder
            logger.info("embedder.ready")
        except Exception as exc:
            logger.warning(
                "embedder.unavailable",
                error_type=type(exc).__name__,
            )
    if settings.semantic_cache_enabled:
        if "redis" in services and embedder is not None:
            from app.llm.semantic_cache import SemanticLLMCache

            services["semantic_cache"] = SemanticLLMCache(
                services["redis"],
                embedder,
            )
            logger.info("semantic_cache.ready")
        else:
            logger.warning(
                "semantic_cache.unavailable",
                reason="redis_or_embedder_missing",
            )

    # ── MCP client ───────────────────────────────────────────────────────────
    # Launches the MCP server as a stdio subprocess and keeps one session open
    # for the app's lifetime. The MCPAgent node calls list_tools()/call_tool().
    try:
        from app.mcp.client import launch_mcp_session
        services["mcp_client"] = await launch_mcp_session()
        logger.info("mcp_client.ready")
    except Exception as exc:
        logger.warning(
            "mcp_client.unavailable",
            error_type=type(exc).__name__,
        )

    if "weaviate_client" in services:
        from app.retrieval import retrieve
        if embedder is None:
            logger.warning(
                "retriever.unavailable",
                reason="embedding provider not configured",
            )
        else:
            _embedder = embedder
            services["embedder"] = _embedder
            _wv = services["weaviate_client"]
            _llm = services["llm"]
            _registry = services["collection_registry"]
            services["retriever"] = lambda q, llm=None: retrieve(
                q,
                weaviate_client=_wv,
                llm=llm or _llm,
                embedder=_embedder,
                collection_registry=_registry,
            )
            logger.info("retriever.ready")
    else:
        logger.warning("retriever.unavailable", reason="weaviate not connected")

    # ── Event bus ─────────────────────────────────────────────────────────────
    services["event_bus"] = RunEventBus(services.get("redis"))
    logger.info(
        "event_bus.ready",
        backend="redis" if "redis" in services else "memory",
    )

    app.state.services = services
    yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("eurskem_ai.shutdown")
    if "mongo" in services:
        await services["mongo"].close()
    if "mongo_sync_client" in services:
        services["mongo_sync_client"].close()
    if "weaviate_client" in services:
        services["weaviate_client"].close()
    if "redis" in services:
        await services["redis"].aclose()
    if "mcp_client" in services:
        await services["mcp_client"].stop()
    if checkpointer_context is not None:
        await checkpointer_context.__aexit__(None, None, None)


app = FastAPI(
    title="Eurskem AI — Agentic Workflow Platform",
    description="Built by Rukainnovation for Eurskem",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url="/redoc" if settings.api_docs_enabled else None,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
)

# ── CORS ───────────────────────────────────────────────────────────────────────
# Origins and hosts are explicit settings. Settings refuses localhost, wildcard,
# or test hosts when ENVIRONMENT=production.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=list(settings.allowed_hosts),
)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(RedisRateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
    expose_headers=[
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
    ],
)

# ── Prometheus ─────────────────────────────────────────────────────────────────
if settings.metrics_enabled:
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
app.include_router(workflow_files_api.router)

from app.observability.tracing import configure_tracing

configure_tracing(app)
