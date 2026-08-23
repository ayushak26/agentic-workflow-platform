"""Logging module.

Part of the observability: structured logging, prometheus metrics, tracing, and the cost ledger.

Public symbols: configure_logging, get_logger.
"""
import structlog
import sys,logging


def configure_logging(environment: str = "development") -> None:
    """Configure the logging.

    Args:
        environment (str): Environment name (optional, default 'development').
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if environment == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

def get_logger(name: str = __name__):
    """Return the logger.

    Args:
        name (str): Workflow or resource name (optional, default __name__).
    """
    return structlog.get_logger(name)