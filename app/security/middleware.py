"""HTTP request identity, limits, security headers, and Redis rate limiting."""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from typing import Callable

import structlog.contextvars
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.config import settings
from app.observability import metrics
from app.observability.logging import get_logger
from app.security.jwt_handler import decode_token

log = get_logger(__name__)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else str(uuid.uuid4())
        started = time.perf_counter()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        try:
            response = await call_next(request)
        except Exception:
            log.exception("http.request_failed")
            metrics.HTTP_REQUESTS.labels(
                method=request.method,
                route=_route_label(request),
                status="500",
            ).inc()
            raise
        finally:
            elapsed = time.perf_counter() - started
            metrics.HTTP_REQUEST_LATENCY.labels(
                method=request.method,
                route=_route_label(request),
            ).observe(elapsed)

        metrics.HTTP_REQUESTS.labels(
            method=request.method,
            route=_route_label(request),
            status=str(response.status_code),
        ).inc()
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Cache-Control"] = (
            "no-store"
            if request.url.path.startswith(("/api", "/auth"))
            else "no-cache"
        )
        structlog.contextvars.clear_contextvars()
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header"},
                )
            if size > settings.max_request_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            "Request body exceeds the "
                            f"{settings.max_request_body_mb} MB limit"
                        )
                    },
                )
        return await call_next(request)


class RedisRateLimitMiddleware(BaseHTTPMiddleware):
    _EXEMPT = frozenset({"/health", "/ready", "/metrics"})

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        if (
            not settings.rate_limit_enabled
            or request.method == "OPTIONS"
            or request.url.path in self._EXEMPT
            or not request.url.path.startswith(("/api", "/auth"))
        ):
            return await call_next(request)

        services = getattr(request.app.state, "services", {})
        redis = services.get("redis")
        if redis is None:
            if settings.environment.lower() == "production":
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Rate-limit service is unavailable"},
                    headers={"Retry-After": "5"},
                )
            return await call_next(request)

        limit = (
            settings.rate_limit_auth_requests_per_minute
            if request.url.path.startswith("/auth")
            else settings.rate_limit_requests_per_minute
        )
        subject = _rate_limit_subject(request)
        window = int(time.time() // 60)
        digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]
        key = f"awp:ratelimit:{window}:{digest}"
        try:
            pipeline = redis.pipeline(transaction=True)
            pipeline.incr(key)
            pipeline.expire(key, 120)
            count, _ = await pipeline.execute()
        except Exception as exc:
            log.warning("rate_limit.unavailable", error_type=type(exc).__name__)
            if settings.environment.lower() == "production":
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Rate-limit service is unavailable"},
                    headers={"Retry-After": "5"},
                )
            return await call_next(request)

        remaining = max(0, limit - int(count))
        reset = (window + 1) * 60
        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(reset),
        }
        if int(count) > limit:
            metrics.RATE_LIMIT_REJECTIONS.labels(
                scope="auth" if request.url.path.startswith("/auth") else "api"
            ).inc()
            headers["Retry-After"] = str(max(1, reset - int(time.time())))
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers=headers,
            )

        response = await call_next(request)
        response.headers.update(headers)
        return response


def _rate_limit_subject(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        try:
            payload = decode_token(authorization.split(None, 1)[1])
            return f"user:{payload['sub']}"
        except (ValueError, KeyError):
            pass
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path
