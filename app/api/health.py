"""Liveness and dependency-readiness endpoints."""
from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings

router = APIRouter(tags=["health"])

Probe = Callable[[], Any | Awaitable[Any]]


async def _call_probe(probe: Probe) -> Any:
    result = probe()
    if inspect.isawaitable(result):
        return await result
    return result


async def _probe(name: str, probe: Probe | None) -> tuple[str, dict[str, Any]]:
    started = time.perf_counter()
    if probe is None:
        return name, {
            "status": "unavailable",
            "latency_ms": 0.0,
            "error": "not_configured",
        }
    try:
        result = await asyncio.wait_for(
            _call_probe(probe),
            timeout=settings.health_probe_timeout_seconds,
        )
        if result is False:
            raise RuntimeError("probe returned false")
        return name, {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except TimeoutError:
        return name, {
            "status": "unavailable",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": "timeout",
        }
    except Exception as exc:
        return name, {
            "status": "unavailable",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            # Do not expose connection strings, credentials, or host details.
            "error": type(exc).__name__,
        }


def _mongo_probe(services: dict[str, Any]) -> Probe | None:
    database = services.get("audit_db")
    if database is None:
        return None
    return lambda: database.command("ping")


def _weaviate_probe(services: dict[str, Any]) -> Probe | None:
    client = services.get("weaviate_client")
    if client is None:
        return None
    return lambda: asyncio.to_thread(client.is_ready)


def _minio_probe(services: dict[str, Any]) -> Probe | None:
    store = services.get("object_store")
    if store is None:
        return None
    return lambda: asyncio.to_thread(
        store.client.head_bucket,
        Bucket=store.bucket,
    )


def _redis_probe(services: dict[str, Any]) -> Probe | None:
    client = services.get("redis")
    if client is None:
        return None
    return client.ping


def _checkpointer_probe(services: dict[str, Any]) -> Probe | None:
    checkpointer = services.get("langgraph_checkpointer")
    if checkpointer is None:
        return None
    return lambda: checkpointer.aget(
        {
            "configurable": {
                "thread_id": "__readiness__",
                "checkpoint_ns": "",
            }
        }
    )


async def probe_services(services: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Probe every configured dependency concurrently."""

    probes: list[tuple[str, Probe | None]] = [
        ("mongo", _mongo_probe(services)),
        ("weaviate", _weaviate_probe(services)),
        ("minio", _minio_probe(services)),
        ("redis", _redis_probe(services)),
        ("checkpointer", _checkpointer_probe(services)),
    ]
    probes.extend(
        (name, probe)
        for name, probe in sorted(services.items())
        if name.startswith("llm:local-") and callable(probe)
    )
    skill_catalog = services.get("scientific_skill_catalog")
    if settings.scientific_skills_enabled or skill_catalog is not None:
        probes.append(
            (
                "scientific_skills",
                getattr(skill_catalog, "probe", None),
            )
        )

    mcp = services.get("mcp_client")
    configured = tuple(getattr(mcp, "configured_servers", ())) if mcp else ()
    mcp_names = configured or ("eurskem",)
    for server in mcp_names:
        probes.append(
            (
                f"mcp:{server}",
                (lambda name=server: mcp.probe(name)) if mcp else None,
            )
        )

    results = await asyncio.gather(
        *(_probe(name, probe) for name, probe in probes)
    )
    return dict(results)


async def _health_payload(request: Request) -> tuple[dict[str, Any], bool]:
    services = getattr(request.app.state, "services", {})
    service_results = await probe_services(services)
    mcp = services.get("mcp_client")
    configured_mcp = tuple(
        f"mcp:{name}"
        for name in getattr(mcp, "configured_servers", ())
    )
    enabled_research = (
        ("scientific_skills",)
        if settings.scientific_skills_enabled
        else ()
    )
    required_local_models = (
        tuple(
            name
            for name in services
            if name.startswith("llm:local-")
        )
        if settings.local_llm_readiness_required
        else ()
    )
    # Every enabled MCP process is a required dependency. This prevents an
    # explicitly enabled paper-search server from failing while /ready stays
    # green because an operator forgot to duplicate it in the CSV setting.
    required = tuple(
        dict.fromkeys(
            (
                *settings.required_readiness_services,
                *configured_mcp,
                *enabled_research,
                *required_local_models,
            )
        )
    )
    ready = all(
        service_results.get(name, {}).get("status") == "ok"
        for name in required
    )
    payload = {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "required_services": list(required),
        "services": service_results,
    }
    return payload, ready


@router.get("/health")
async def health(request: Request):
    """Return live dependency status without failing the liveness request."""

    payload, _ = await _health_payload(request)
    return payload


@router.get("/ready")
async def ready(request: Request):
    """Return 503 until every required runtime dependency is reachable."""

    payload, is_ready = await _health_payload(request)
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content=payload,
    )
