"""Structured logging setup. JSON output, contextvars-aware.

Every log line is a JSON dict. The contextvars processor means values
bound via ``structlog.contextvars.bind_contextvars()`` propagate to every
log call within the same async/sync execution context — used in Phase 11
to attach session_id and request_id to every line of a request.
"""
import logging
import sys

import structlog

from app.config import settings


def configure_logging() -> None:
    """Configure stdlib + structlog. Call once at app startup."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger. Use ``log = get_logger(__name__)``."""
    return structlog.get_logger(name)