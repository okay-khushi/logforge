"""
core/logging.py — Structured Logging Setup

WHY STRUCTURED LOGGING?
    print("Error happened") — useless in production. You can't search or filter it.

    structlog produces JSON like:
    {
        "timestamp": "2024-01-15T10:30:00Z",
        "level": "error",
        "event": "Failed to connect to database",
        "service": "logforge-api",
        "request_id": "req-12345",
        "user_id": "u999",
        "duration_ms": 142
    }

    This JSON can be ingested by Elasticsearch, CloudWatch, Datadog, or —
    recursively — LogForge itself.

HOW IT WORKS:
    - structlog wraps Python's standard `logging` module
    - In development: pretty colored output for readability
    - In production: JSON output for machine parsing
    - Every log line automatically includes timestamp, level, and service name
"""

import logging
import sys

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """
    Configure structlog and the standard Python logging module.
    Call this ONCE at application startup in main.py.
    """

    # ── Shared processors (run on every log record) ───────────────────────────
    shared_processors = [
        structlog.contextvars.merge_contextvars,   # Merge request-scoped context
        structlog.stdlib.add_log_level,             # Add "level": "info" field
        structlog.stdlib.add_logger_name,           # Add "logger": "app.api.ingest"
        structlog.processors.TimeStamper(fmt="iso"),  # Add ISO timestamp
        structlog.processors.StackInfoRenderer(),   # Include stack traces
    ]

    if settings.is_production:
        # Production: JSON output, one line per log event
        renderer = structlog.processors.JSONRenderer()
    else:
        # Development: human-friendly colored console output
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Apply to root Python logger (captures uvicorn, sqlalchemy, etc.)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    # Quiet down noisy third-party loggers in development
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("elasticsearch").setLevel(logging.WARNING)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a named logger instance.

    Usage:
        from app.core.logging import get_logger
        logger = get_logger(__name__)

        logger.info("User logged in", user_id="u123", ip="192.168.1.1")
        logger.error("DB connection failed", error=str(e), retries=3)
    """
    return structlog.get_logger(name)
