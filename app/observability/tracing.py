"""Optional OpenTelemetry wiring.

Production can export to an OTLP/HTTP collector or managed observability
backend. When disabled, this module has no effect and imports no exporter.
"""
# pyright: reportMissingImports=false
# OpenTelemetry is an intentionally optional deployment feature. Imports stay
# inside configure_tracing so the default installation does not carry six
# tracing packages; production still fails closed if OTEL_ENABLED=true and the
# selected image has not installed the tracing extra.
from __future__ import annotations

from app.config import settings
from app.observability.logging import get_logger

log = get_logger(__name__)


def configure_tracing(app) -> None:
    """Configure the tracing.

    Args:
        app: The app.
    """
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create(
                {"service.name": settings.otel_service_name}
            )
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint,
                )
            )
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()
        log.info(
            "tracing.enabled",
            service_name=settings.otel_service_name,
        )
    except Exception as exc:
        if settings.environment.lower() == "production":
            raise RuntimeError("OpenTelemetry initialization failed") from exc
        log.warning(
            "tracing.initialization_failed",
            error_type=type(exc).__name__,
        )
