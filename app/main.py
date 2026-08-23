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
  llm            — RegistryLLMGateway singleton (dispatches to OpenRouter/OpenAI/Anthropic)
  event_bus      — RunEventBus for authenticated SSE live streaming
"""
import asyncio
import contextlib
import warnings
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from prometheus_client import make_asgi_app

from app.config import settings
from app.observability.logging import configure_logging, get_logger
from app.observability.cost_ledger import CostLedger, configure_pricing_db
from app.llm import get_llm_gateway
from app.llm.registry import configured_local_model_probes
from app.runtime.events import RunEventBus
from app.runtime.coordination import RedisLease
from app.ingestion.collections import CollectionRegistry
from app.workflow.run_history import ensure_indexes as ensure_run_indexes
from app.workflow.pipeline_history import ensure_pipeline_indexes
from app.workflow.claim_verifications import ensure_indexes as ensure_claim_verification_indexes
from app.workflow.business_view.store import ensure_business_view_indexes
from app.workflow.run_chat_store import ensure_run_chat_indexes
from app.workflow.preflight_stats import ensure_indexes as ensure_preflight_stats_indexes
from app.proposal_graph.workspace_store import ProposalWorkspaceStore
from app.security.entity_tokenizer import EntityTokenizerService
from app.security.entity_protection_errors import VaultKeyMisconfiguredError
from app.security.middleware import (
    RedisRateLimitMiddleware,
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
)
from app.workflow.orchestration import BackgroundRunManager
from app.db.migrations import MigrationError, run_migrations

from app.api import health
from app.api.auth import router as auth_router
from app.api.workflows import router as workflows_router
from app.api.cost import router as cost_router
from app.api.cost_admin import router as cost_admin_router
from app.api.eval import router as eval_router
from app.api.inspect import router as inspect_router
from app.api import audit as audit_api
from app.api import runs as runs_api
from app.api import proposals as proposals_api
from app.api import research as research_api
from app.api import workflow_files as workflow_files_api
from app.api import llm_providers as llm_providers_api
from app.api import pipelines as pipelines_api
from app.api import candidates as candidates_api
from app.api import run_chat as run_chat_api
from app.api import node_types_chat as node_types_chat_api
from app.api import workflow_generation as workflow_generation_api
from app.api import builder as builder_api
from app.api import email_oauth as email_oauth_api
from app.api import integration_oauth as integration_oauth_api
from app.api import entity_registry as entity_registry_api
from app.api import knowledge as knowledge_api
from app.api import retrieval as retrieval_api
from app.api import rag_agents as rag_agents_api

from app.db.mongo import DB_NAME

from functools import partial

configure_logging(settings.environment)
logger = get_logger(__name__)

# redisvl warns that get_async_redis_connection() will become async in its next
# major release. langgraph-checkpoint-redis (pinned <0.6) calls it synchronously
# via AsyncRedisSaver; pyproject.toml's version ceiling is the real guard against
# that breaking change, this just silences the noise until then.
warnings.filterwarnings(
    "ignore",
    message="get_async_redis_connection will become async",
    category=DeprecationWarning,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Compute the lifespan.

    Args:
        app (FastAPI): The app.
    """
    logger.info("eurskem_ai.startup", environment=settings.environment)

    services: dict = {}
    checkpointer_context = None

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
        applied_migrations = await run_migrations(services["audit_db"])
        logger.info(
            "mongo.migrations_ready",
            applied=len(applied_migrations),
        )
        try:
            await ensure_run_indexes(services["audit_db"])
            await ensure_pipeline_indexes(services["audit_db"])
            await ensure_claim_verification_indexes(services["audit_db"])
            await ensure_run_chat_indexes(services["audit_db"])
            await ensure_business_view_indexes(services["audit_db"])
            await ensure_preflight_stats_indexes(services["audit_db"])
            await ProposalWorkspaceStore(
                services["audit_db"],
                None,
            ).ensure_indexes()
            logger.info("run_history.indexes_ensured")
        except Exception as exc:
            logger.warning("run_history.indexes_failed", error=str(exc))

        # Confidential entity protection (Phase 1) — always constructed when
        # Mongo is up, since pseudonymised mode is the default for every run.
        # Not a hard boot-time crash on a missing/placeholder vault key (that
        # would break pytest/dev environments that never configure it) — the
        # key is validated lazily the moment a real workflow actually
        # tokenizes something (see app/security/entity_vault.py). This is
        # only an eager, loud warning so a real deployment notices before
        # its first run fails.
        services["entity_tokenizer"] = EntityTokenizerService(services["audit_db"])
        try:
            await services["entity_tokenizer"].ensure_indexes()
            logger.info("entity_tokenizer.ready")
        except Exception as exc:
            logger.warning("entity_tokenizer.indexes_failed", error=str(exc))
        try:
            if settings.entity_protection_default_mode != "public":
                from app.security.entity_vault import _load_kek

                _load_kek()
        except VaultKeyMisconfiguredError as exc:
            logger.error(
                "entity_tokenizer.vault_key_misconfigured",
                error=str(exc),
                hint=(
                    "every workflow run will fail closed until "
                    "ENTITY_VAULT_MASTER_KEY is set to a unique 32+ byte "
                    "secret distinct from SECRET_KEY"
                ),
            )

        services["db"] = db                 # raw pymongo Database for CostLedger
        services["cost_ledger"] = CostLedger(db)
        configure_pricing_db(db)
        logger.info("mongo.connected")
    except MigrationError:
        logger.exception("mongo.migrations_failed")
        raise
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
        logger.info("object_store.ready")
    except Exception as exc:
        logger.warning(
            "object_store.unavailable",
            error_type=type(exc).__name__,
        )

    # ── Redis ──────────────────────────────────────────────────────────────────
    try:
        redis_client = await aioredis.from_url(settings.redis_url)
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
    services.update(configured_local_model_probes())
    logger.info("llm_gateway.ready")

    # ── Web search / image generation / Kimi vision ───────────────────────────
    # Each service is a thin, credential-aware client wrapper (see
    # app/tools/{web_io,image_io,vision_io}.py) — it always constructs, even
    # with no credentials configured, and only raises when actually called.
    # Missing credentials surface as a zero-token preflight issue instead
    # (app/runtime/preflight.py), not a startup failure.
    from app.tools.web_io import get_web_search_service
    from app.tools.database_lookup import get_database_lookup_service
    from app.tools.image_io import get_image_generation_service
    from app.tools.vision_io import get_kimi_vision_service

    services["web_search"] = get_web_search_service()
    services["database_lookup"] = get_database_lookup_service()
    services["image_generator"] = get_image_generation_service()
    services["kimi_vision"] = get_kimi_vision_service()
    logger.info("web_search.ready")
    logger.info("database_lookup.ready")
    logger.info("image_generator.ready")
    logger.info("kimi_vision.ready")

    # ── External Action ───────────────────────────────────────────────────────
    # ExternalActionAgent's outbound REST/webhook calls, gated by the same
    # idempotency ledger MCP and Email already use. Always constructed —
    # there is no credential to be missing, since the author supplies the URL.
    from app.integrations.external_action import ExternalActionService

    services["external_action"] = ExternalActionService(db=services.get("audit_db"))
    logger.info("external_action.ready")

    # ── Snippet runner ────────────────────────────────────────────────────────
    # PythonSnippetAgent's isolated executor — a network-isolated sidecar
    # container reached over a Unix socket (docker-compose.yml's
    # `snippet-runner` service). Registered only when enabled: an unconfigured
    # deployment should see REQUIRED_SERVICE_MISSING at preflight for any
    # workflow using this node, not a runtime surprise mid-run.
    if settings.snippet_runner_enabled:
        from app.runtime.snippet_client import SnippetRunnerClient

        services["python_runner"] = SnippetRunnerClient(settings.snippet_runner_socket_path)
        logger.info("snippet_runner.ready", socket=settings.snippet_runner_socket_path)

    # ── Email integration ─────────────────────────────────────────────────────
    # One capability, adapters per provider. Static connections come from
    # configuration (EMAIL_CONNECTIONS); OAuth-connected mailboxes (someone
    # clicked "Connect Outlook"/"Connect Gmail" in the Builder — see
    # app/api/email_oauth.py) are loaded from Mongo and merged in here, so
    # they survive a restart without needing to be redeclared as env-var
    # config. A workflow references a mailbox by name either way, and can be
    # exported without leaking access. Always constructed: with no
    # connections configured, a workflow using EmailAgent is blocked by
    # preflight with the missing connection named, rather than the API
    # failing to start.
    from app.integrations.email import build_email_service
    from app.integrations.email.connections_store import load_dynamic_connections

    services["email"] = build_email_service(db=services.get("audit_db"))
    if services.get("audit_db") is not None:
        from app.api.email_oauth import ensure_indexes as ensure_email_oauth_indexes

        try:
            dynamic = await load_dynamic_connections(services["audit_db"])
            for connection in dynamic.values():
                services["email"].add_connection(connection)
            await ensure_email_oauth_indexes(services["audit_db"])
        except Exception as error:
            logger.warning("email.dynamic_connections_load_failed", error=str(error))
    logger.info(
        "email.ready",
        connection_count=len(services["email"].connections),
    )

    # ── File integration (Google Drive / OneDrive) ────────────────────────────
    # Same shape as the email integration above: static connections come from
    # configuration (INTEGRATION_CONNECTIONS); OAuth-connected accounts
    # (someone clicked "Connect Google Drive"/"Connect OneDrive" in the
    # Builder — see app/api/integration_oauth.py) are loaded from Mongo and
    # merged in here, so they survive a restart without needing to be
    # redeclared as env-var config. Always constructed: with no connections
    # configured, a workflow using IntegrationAgent is blocked by preflight
    # with the missing connection named, rather than the API failing to start.
    from app.integrations.files import build_integration_service
    from app.integrations.files.connections_store import (
        load_dynamic_connections as load_dynamic_integration_connections,
    )

    services["files_integration"] = build_integration_service(db=services.get("audit_db"))
    if services.get("audit_db") is not None:
        from app.api.integration_oauth import ensure_indexes as ensure_integration_oauth_indexes

        try:
            dynamic = await load_dynamic_integration_connections(services["audit_db"])
            for connection in dynamic.values():
                services["files_integration"].add_connection(connection)
            await ensure_integration_oauth_indexes(services["audit_db"])
        except Exception as error:
            logger.warning("integration.dynamic_connections_load_failed", error=str(error))
    logger.info(
        "integration.ready",
        connection_count=len(services["files_integration"].connections),
    )

    # ── Scientific Agent Skills ──────────────────────────────────────────────
    if settings.scientific_skills_enabled:
        from app.research.skills import ScientificSkillCatalog

        skill_catalog = ScientificSkillCatalog(
            settings.resolved_scientific_skills_path,
            allowlist=settings.allowed_scientific_skills,
            enabled=True,
            max_prompt_chars=(
                settings.scientific_skills_max_prompt_chars
            ),
        )
        skill_catalog.refresh()
        services["scientific_skill_catalog"] = skill_catalog
        logger.info(
            "scientific_skills.ready",
            loaded=len(skill_catalog.metadata()),
            errors=len(skill_catalog.load_errors),
        )

    # ── Bounded Deep Research ────────────────────────────────────────────────
    if settings.deep_research_enabled:
        from app.research.deep_research import get_deep_research_service

        services["deep_research"] = get_deep_research_service(
            llm=services["llm"],
            web_search=services["web_search"],
        )
        logger.info("deep_research.ready")

    # ── MCP client ───────────────────────────────────────────────────────────
    # Launches the MCP server as a stdio subprocess and keeps one session open
    # for the app's lifetime. The MCPAgent node calls list_tools()/call_tool().
    try:
        from app.mcp.client import launch_mcp_session
        services["mcp_client"] = await launch_mcp_session()

        # MCP integration service: server registry, policy gate, structured
        # results, write ledger and audit. Every MCP tool call the Builder or a
        # workflow makes goes through this one object.
        from app.mcp.connections import build_mcp_service

        services["mcp"] = build_mcp_service(
            client=services["mcp_client"],
            db=services.get("audit_db"),
        )
        logger.info(
            "mcp_integration.ready",
            servers=len(services["mcp"].registry),
        )
        logger.info("mcp_client.ready")
    except Exception as exc:
        logger.warning(
            "mcp_client.unavailable",
            error_type=type(exc).__name__,
        )

    if "weaviate_client" in services:
        from app.retrieval import retrieve
        from app.ingestion.embedder import get_embedder
        try:
            _embedder = get_embedder()
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
            # Write path for evidence ingestion (MinIOEvidenceIngestion node).
            services["evidence_indexer"] = lambda chunks, vectors: (
                _wv.upsert_chunks(chunks, vectors)
            )
            logger.info("retriever.ready")
        except Exception as exc:
            logger.warning(
                "retriever.unavailable",
                reason="embedding provider not configured",
                error_type=type(exc).__name__,
            )
    else:
        logger.warning("retriever.unavailable", reason="weaviate not connected")

    # ── Knowledge Studio: repository, retrieval, RAG, ingestion coordinator ───
    # Builds on the same Mongo/Weaviate/embedder/LLM clients wired above —
    # no second connection, no second FastAPI app.
    if "mongo" in services:
        from app.knowledge.repository import KnowledgeRepository
        from app.knowledge.service import KnowledgeService

        knowledge_db = services["mongo"]._ensure_client()[DB_NAME]
        knowledge_repository = KnowledgeRepository(knowledge_db)
        try:
            await knowledge_repository.ensure_indexes()
        except Exception as exc:
            logger.warning(
                "knowledge_repository.ensure_indexes_failed", error_type=type(exc).__name__
            )
        services["knowledge_repository"] = knowledge_repository
        services["knowledge_service"] = KnowledgeService(knowledge_repository)
        logger.info("knowledge_repository.ready")

        if "weaviate_client" in services and "embedder" in services:
            from app.rag.service import RAGService
            from app.retrieval.service import RetrievalService

            retrieval_service = RetrievalService(
                weaviate_client=services["weaviate_client"],
                embedder=services["embedder"],
                llm=services["llm"],
                repository=knowledge_repository,
            )
            services["retrieval_service"] = retrieval_service
            services["rag_service"] = RAGService(
                repository=knowledge_repository,
                retrieval_service=retrieval_service,
                llm=services["llm"],
            )
            logger.info("retrieval_service.ready")
        else:
            logger.warning(
                "retrieval_service.unavailable",
                reason="weaviate or embedder not available",
            )

        if all(key in services for key in ("object_store", "embedder", "weaviate_client")):
            from app.ingestion.coordinator import IngestionCoordinator

            ingestion_coordinator = IngestionCoordinator(
                repository=knowledge_repository,
                object_store=services["object_store"],
                embedder=services["embedder"],
                weaviate_client=services["weaviate_client"],
                redis=services.get("redis"),
            )
            services["ingestion_coordinator"] = ingestion_coordinator
            try:
                recovered = await ingestion_coordinator.recover()
                logger.info("ingestion_coordinator.ready", recovered_jobs=recovered)
            except Exception as exc:
                logger.warning(
                    "ingestion_coordinator.recover_failed", error_type=type(exc).__name__
                )
        else:
            logger.warning(
                "ingestion_coordinator.unavailable",
                reason="object store, embedder or weaviate not available",
            )
    else:
        logger.warning("knowledge_repository.unavailable", reason="mongo not connected")

    # ── Event bus ─────────────────────────────────────────────────────────────
    # Redis streams + pub/sub provide replay and live delivery across all
    # Uvicorn workers. Local memory remains a development-only fallback.
    services["event_bus"] = RunEventBus(
        redis=services.get("redis"),
        max_events_per_run=settings.sse_replay_events_per_run,
        max_run_histories=settings.sse_replay_run_limit,
        replay_ttl_seconds=settings.sse_replay_ttl_seconds,
    )
    services["background_run_manager"] = BackgroundRunManager(
        services.get("redis"),
        lease_seconds=settings.distributed_lease_seconds,
    )
    services["sse_heartbeat_seconds"] = settings.sse_heartbeat_seconds
    logger.info("event_bus.ready")

    # ── Subprocess launch correlation ────────────────────────────────────────
    # SubprocessAgent's parent<->child correlation collection (see
    # app/workflow/subprocess_launches.py) — TTL-indexed the same way the
    # email-OAuth pending-state collection is, so an abandoned or never-
    # called-back launch ages out on its own.
    if services.get("audit_db") is not None:
        from app.workflow.subprocess_launches import ensure_indexes as ensure_subprocess_indexes

        try:
            await ensure_subprocess_indexes(services["audit_db"])
            logger.info("subprocess_launches.ready")
        except Exception as error:
            logger.warning("subprocess_launches.index_setup_failed", error=str(error))

    # ── Stale-run auto-cleanup ──────────────────────────────────────────────────
    # Every worker has a timer so leadership can move after a worker exits, but
    # only the worker holding the renewable Redis lease performs a sweep.
    cleanup_task: asyncio.Task | None = None
    if services.get("audit_db") is not None:
        async def _run_cleanup_loop() -> None:
            """Run the cleanup loop."""
            from app.workflow.run_history import cleanup_stale_runs
            while True:
                await asyncio.sleep(settings.run_auto_cleanup_interval_seconds)
                lease = None
                heartbeat = None
                try:
                    redis = services.get("redis")
                    if redis is not None:
                        lease = RedisLease(
                            redis,
                            "awp:leader:stale-run-cleanup",
                            ttl_seconds=settings.distributed_lease_seconds,
                        )
                        if not await lease.acquire():
                            continue
                        heartbeat = asyncio.create_task(lease.keep_alive())
                    elif settings.environment.lower() == "production":
                        logger.error(
                            "run_history.auto_cleanup_skipped",
                            reason="distributed lease service unavailable",
                        )
                        continue
                    cleanup_job = asyncio.create_task(
                        cleanup_stale_runs(services["audit_db"])
                    )
                    if heartbeat is not None:
                        done, _ = await asyncio.wait(
                            {cleanup_job, heartbeat},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if heartbeat in done and cleanup_job not in done:
                            cleanup_job.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await cleanup_job
                            logger.error(
                                "run_history.auto_cleanup_lease_lost"
                            )
                            continue
                    deleted = await cleanup_job
                    if deleted:
                        logger.warning(
                            "run_history.auto_cleanup_swept", count=len(deleted)
                        )
                except Exception as exc:
                    logger.error(
                        "run_history.auto_cleanup_loop_failed", error=str(exc)
                    )
                finally:
                    if heartbeat is not None:
                        heartbeat.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await heartbeat
                    if lease is not None:
                        await lease.release()

        cleanup_task = asyncio.create_task(_run_cleanup_loop())

    app.state.services = services
    yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("eurskem_ai.shutdown")
    if cleanup_task is not None:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
    if "background_run_manager" in services:
        await services["background_run_manager"].close()
    if "event_bus" in services:
        await services["event_bus"].close()
    if "database_lookup" in services:
        await services["database_lookup"].close()
    if "mongo" in services:
        await services["mongo"].close()
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
# or test hosts when ENVIRONMENT=production. Starlette executes the last-added
# middleware first, so the additions below are intentionally inside-out:
# request context -> CORS -> trusted host -> body limit -> rate limit -> router.
app.add_middleware(RedisRateLimitMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=list(settings.allowed_hosts),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Cache-Control",
        "Last-Event-ID",
        "X-Request-ID",
    ],
    expose_headers=[
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "Retry-After",
    ],
)

# RequestContext is outermost, so size/rate-limit rejections receive correlation
# and security headers. CORS also wraps those rejections for browser clients.
app.add_middleware(RequestContextMiddleware)

# ── Prometheus ─────────────────────────────────────────────────────────────────
if settings.metrics_enabled:
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(auth_router)
app.include_router(cost_router)
app.include_router(cost_admin_router)
app.include_router(workflows_router)
app.include_router(eval_router)
app.include_router(inspect_router)
app.include_router(audit_api.router)
app.include_router(runs_api.router)
app.include_router(proposals_api.router)
app.include_router(research_api.router)
app.include_router(workflow_files_api.router)
app.include_router(llm_providers_api.router)
app.include_router(pipelines_api.router)
app.include_router(candidates_api.router)
app.include_router(run_chat_api.router)
app.include_router(node_types_chat_api.router)
app.include_router(workflow_generation_api.router)
app.include_router(entity_registry_api.router)
app.include_router(builder_api.router)
app.include_router(email_oauth_api.router)
app.include_router(integration_oauth_api.router)
app.include_router(knowledge_api.router)
app.include_router(retrieval_api.router)
app.include_router(rag_agents_api.router)