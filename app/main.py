"""FastAPI app entry point.

Run via uvicorn (see Dockerfile CMD):
    uvicorn app.main:app --host 0.0.0.0 --port 8000

The lifespan context manager handles startup and shutdown hooks.
Endpoints today:
    /health  -- liveness: process is up
    /ready   -- readiness: dependencies reachable
"""
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI

from app.config import settings
from app.observability.logging import configure_logging, get_logger
from app.api import workflows

# Configure logging at import time so even startup logs are JSON
configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown hooks. Body before yield = startup, after = shutdown."""
    log.info("app.startup", env=settings.app_env, log_level=settings.log_level)
    yield
    log.info("app.shutdown")


app = FastAPI(
    title="Agentic Workflow Platform",
    version="0.1.0",
    description="Optimoz-style platform for composable AI workflows (Phase 1: skeleton).",
    lifespan=lifespan,
)

app.include_router(workflows.router)

@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 if the process is responding."""
    return {"status": "ok"}


@app.get("/ready", tags=["meta"])
async def ready() -> dict:
    """Readiness probe. Pings dependencies; reports per-subsystem status."""
    checks: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            r = await client.get(f"{settings.weaviate_url}/v1/.well-known/ready")
            checks["weaviate"] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
        except Exception as e:
            checks["weaviate"] = f"err:{type(e).__name__}"
            log.warning("ready.weaviate_unreachable", error=str(e))

    # Mongo and Redis land in Phase 2 once those clients are wired
    all_ok = all(v == "ok" for v in checks.values())
    status = "ok" if all_ok else "degraded"
    return {"status": status, "checks": checks}