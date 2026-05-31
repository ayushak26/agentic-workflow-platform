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

from app.api import health
from app.api.auth import router as auth_router
from app.api.workflows import router as workflows_router
from app.api.cost import router as cost_router

configure_logging(settings.environment)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("eurskem_ai.startup", environment=settings.environment)

    services: dict = {}

    # ── MongoDB ────────────────────────────────────────────────────────────────
    try:
        from pymongo import MongoClient
        mongo_client = MongoClient(
            settings.mongo_uri, serverSelectionTimeoutMS=3000
        )
        mongo_client.server_info()
        db = mongo_client[settings.mongo_db]
        services["mongo"] = mongo_client
        services["db"] = db
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

    # ── MinIO ──────────────────────────────────────────────────────────────────
    try:
        from minio import Minio
        minio_client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
        services["object_store"] = minio_client
        logger.info("minio.connected")
    except Exception as exc:
        logger.warning("minio.unavailable", error=str(exc))

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