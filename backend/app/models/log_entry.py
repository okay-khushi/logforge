"""
models/log_entry.py — Pydantic Schemas for Log Entries

WHY PYDANTIC MODELS?
    When a client sends JSON to your API, you can't trust anything in it.
    Pydantic validates the data before it ever touches your business logic:

    - "timestamp" must be a valid datetime — not "banana"
    - "level" must be one of the allowed severities — not "CATASTROPHIC"
    - "message" must be a non-empty string

    If validation fails, FastAPI automatically returns HTTP 422 Unprocessable
    Entity with a clear error message — no try/except needed in your route.

DESIGN DECISION — Two separate models:
    - LogEntryInput: What the CLIENT sends (no ID, no anomaly score yet)
    - LogEntryStored: What we store in Elasticsearch (includes ID, scores, etc.)

    This separation is called the "Input/Output DTO" pattern and prevents
    clients from injecting fields like is_anomaly=false to bypass detection.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class LogLevel(str, Enum):
    """
    Log severity levels (follows standard syslog severity order).
    Using an Enum ensures only valid values are accepted.
    """
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    WARNING = "WARNING"   # Alias — some systems use WARNING, others WARN
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogEntryInput(BaseModel):
    """
    Schema for a single log entry sent by a client.
    This is the INPUT model — validated before any processing.
    """

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the log event occurred (ISO 8601). Defaults to now if not provided.",
        examples=["2024-01-15T10:30:00Z"],
    )
    level: LogLevel = Field(
        description="Log severity level",
        examples=["ERROR"],
    )
    service: str = Field(
        min_length=1,
        max_length=100,
        description="Name of the service that produced this log",
        examples=["auth-service"],
    )
    host: str = Field(
        min_length=1,
        max_length=255,
        description="Hostname or IP of the server that produced this log",
        examples=["prod-server-01"],
    )
    message: str = Field(
        min_length=1,
        max_length=10_000,
        description="The log message content",
        examples=["Failed to connect to database after 3 retries"],
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional key-value pairs for additional context",
        examples=[{"user_id": "u123", "request_id": "req-456"}],
    )
    environment: str = Field(
        default="production",
        min_length=1,
        max_length=50,
        description="Deployment environment (production, staging, development)",
        examples=["production"],
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        """
        Normalize all timestamps to UTC.

        WHY: Mixing timezones in a log database is a debugging nightmare.
        All timestamps are stored as UTC; the frontend converts to local time.
        """
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        elif isinstance(v, datetime):
            dt = v
        else:
            raise ValueError(f"Cannot parse timestamp: {v}")

        # If timestamp has no timezone, assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, v: Any) -> str:
        """Normalize WARN/WARNING to a single canonical value."""
        if isinstance(v, str):
            upper = v.upper()
            if upper == "WARNING":
                return "WARN"
            return upper
        return v

    @field_validator("service", mode="before")
    @classmethod
    def strip_service(cls, v: Any) -> str:
        """Remove leading/trailing whitespace from service name."""
        if isinstance(v, str):
            return v.strip().lower()
        return v

    model_config = {
        "json_schema_extra": {
            "example": {
                "timestamp": "2024-01-15T10:30:00Z",
                "level": "ERROR",
                "service": "auth-service",
                "host": "prod-server-01",
                "message": "Failed to connect to database after 3 retries",
                "environment": "production",
                "metadata": {"user_id": "u123", "request_id": "req-456"},
            }
        }
    }


class LogBatchInput(BaseModel):
    """
    A batch of log entries — the top-level request body for POST /ingest.

    WHY BATCH INGESTION?
        Sending 1 HTTP request per log line is extremely inefficient.
        At 10,000 logs/second, that's 10,000 TCP connections/second.
        Batching reduces this to ~100 requests/second (batch_size=100).
    """

    logs: list[LogEntryInput] = Field(
        min_length=1,
        max_length=1000,
        description="List of log entries (1 to 1000 per request)",
    )

    @model_validator(mode="after")
    def check_batch_not_empty(self) -> "LogBatchInput":
        if not self.logs:
            raise ValueError("Batch must contain at least one log entry")
        return self


class LogEntryStored(BaseModel):
    """
    Schema for a log entry as stored in Elasticsearch.
    This is the OUTPUT model — includes server-generated fields.
    """

    id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier (UUID) generated at ingest time",
    )
    timestamp: datetime
    level: str
    service: str
    host: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    environment: str = Field(
        default="production",
        description="Deployment environment: production | staging | development",
    )
    anomaly_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Anomaly score from Isolation Forest (0=normal, 1=anomalous)",
    )
    is_anomaly: bool = Field(
        default=False,
        description="True if anomaly_score exceeds the configured threshold",
    )
    indexed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this log was indexed by LogForge",
    )

    def to_es_document(self) -> dict[str, Any]:
        """
        Convert to a dictionary suitable for Elasticsearch indexing.
        Timestamps are serialized to ISO format strings.
        """
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "service": self.service,
            "host": self.host,
            "message": self.message,
            "environment": self.environment,
            "metadata": self.metadata,
            "anomaly_score": self.anomaly_score,
            "is_anomaly": self.is_anomaly,
            "indexed_at": self.indexed_at.isoformat(),
        }

    @classmethod
    def from_input(cls, entry: LogEntryInput) -> "LogEntryStored":
        """
        Create a LogEntryStored from a validated LogEntryInput.
        Anomaly score is 0.0 by default; the ML service updates it later.
        """
        return cls(
            timestamp=entry.timestamp,
            level=entry.level.value if hasattr(entry.level, "value") else entry.level,
            service=entry.service,
            host=entry.host,
            message=entry.message,
            environment=entry.environment,
            metadata=entry.metadata,
        )


class IngestResponse(BaseModel):
    """Response body returned after successful log ingestion."""

    status: str = Field(default="accepted", examples=["accepted"])
    ingested: int = Field(description="Number of log entries accepted")
    batch_id: str = Field(description="Unique ID for this ingestion batch")
    errors: list[str] = Field(
        default_factory=list,
        description="List of validation errors for individual entries (if any)",
    )
