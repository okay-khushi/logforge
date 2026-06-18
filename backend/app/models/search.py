"""
models/search.py — Pydantic Schemas for Search Requests and Responses

WHY DEDICATED SEARCH MODELS?
    The ingest models (LogEntryInput, LogEntryStored) describe a log document.
    Search models describe the QUERY and the RESPONSE ENVELOPE.

    Keeping them separate:
    - Ingest schema can change without breaking search contracts
    - Search response can include metadata (total, page, aggregations)
      that doesn't exist on a single log document
    - Pydantic auto-generates accurate OpenAPI docs for /docs

DESIGN PHILOSOPHY:
    Every search parameter has a sensible default so callers only need to
    specify what they care about. A caller searching for "ERROR" logs needs
    to pass only level="ERROR" — all other parameters keep their defaults.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Request Models
# ─────────────────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    """
    Structured search request body (used when POSTing a search query).
    Mirrors the GET query parameters but supports richer structures.

    WHY BOTH GET AND POST for search?
        GET /search?q=error&level=ERROR  → simple, bookmark-able, shareable
        POST /search (body)              → complex queries with nested filters,
                                          long time ranges, multiple services

    We implement GET for Phase 5 (simpler, more common). POST is Phase 5+.
    """
    q: Optional[str] = Field(
        default=None,
        description="Full-text search query across message, service, host",
        examples=["database timeout", "authentication failed"],
    )
    level: Optional[str] = Field(
        default=None,
        description="Filter by log severity (DEBUG|INFO|WARN|ERROR|CRITICAL)",
        examples=["ERROR"],
    )
    service: Optional[str] = Field(
        default=None,
        description="Filter by service name (exact match, case-insensitive)",
        examples=["auth-service"],
    )
    environment: Optional[str] = Field(
        default=None,
        description="Filter by deployment environment",
        examples=["production"],
    )
    host: Optional[str] = Field(
        default=None,
        description="Filter by hostname (exact match)",
        examples=["prod-server-01"],
    )
    from_time: Optional[datetime] = Field(
        default=None,
        description="Start of time range (ISO 8601)",
        examples=["2024-01-15T00:00:00Z"],
    )
    to_time: Optional[datetime] = Field(
        default=None,
        description="End of time range (ISO 8601)",
        examples=["2024-01-15T23:59:59Z"],
    )
    page: int = Field(
        default=1,
        ge=1,
        le=10_000,
        description="Page number (1-indexed)",
    )
    size: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Results per page",
    )
    sort_by: str = Field(
        default="timestamp",
        description="Field to sort by (timestamp | level | service)",
    )
    sort_order: str = Field(
        default="desc",
        pattern="^(asc|desc)$",
        description="Sort direction: asc or desc",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Response Models
# ─────────────────────────────────────────────────────────────────────────────

class LogHit(BaseModel):
    """
    A single log document returned in search results.
    Maps directly from the Elasticsearch _source field.
    """
    id: str
    timestamp: str
    level: str
    service: str
    host: str
    message: str
    environment: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    anomaly_score: float = 0.0
    is_anomaly: bool = False
    indexed_at: str


class SearchResponse(BaseModel):
    """
    Envelope wrapping search results with pagination metadata.

    WHY AN ENVELOPE?
        Raw Elasticsearch returns {"hits": {"total": ..., "hits": [...]}}.
        We unwrap and reshape this into a cleaner API contract.
        Frontend devs shouldn't need to understand ES response structure.
    """
    total: int = Field(description="Total number of matching documents")
    page: int = Field(description="Current page number")
    size: int = Field(description="Page size requested")
    pages: int = Field(description="Total number of pages")
    results: list[dict[str, Any]] = Field(description="Log documents for this page")
    query_took_ms: int = Field(
        default=0,
        description="Time Elasticsearch spent executing the query (milliseconds)",
    )


class BucketItem(BaseModel):
    """A single bucket in a terms aggregation (e.g., ERROR: 42 logs)."""
    key: str
    count: int


class AggregationResponse(BaseModel):
    """
    Response for aggregation endpoints (summary, breakdown by level/service).
    """
    total: int = Field(description="Total log count across all filters")
    buckets: list[BucketItem] = Field(description="Aggregated counts per group")
    query_took_ms: int = Field(default=0)


class TimeSeriesBucket(BaseModel):
    """A single time bucket in a date-histogram aggregation."""
    timestamp: str   # ISO 8601 bucket start
    count: int


class TimeSeriesResponse(BaseModel):
    """
    Response for log-volume-over-time endpoints.
    Drives the time-series chart on the dashboard (Phase 6).
    """
    interval: str = Field(description="Bucket interval (e.g., 1h, 30m, 1d)")
    buckets: list[TimeSeriesBucket]
    total: int
    query_took_ms: int = Field(default=0)


class ServiceSummary(BaseModel):
    """Statistics for a single service."""
    service: str
    total: int
    errors: int
    warnings: int
    error_rate: float  # errors / total, rounded to 4 decimal places


class SummaryResponse(BaseModel):
    """
    High-level stats for the overview dashboard card.
    One API call returns everything the dashboard header needs.
    """
    total_logs: int
    error_count: int
    warn_count: int
    critical_count: int
    error_rate: float
    services: list[ServiceSummary]
    top_errors: list[dict[str, Any]]   # Most frequent error messages
    query_took_ms: int = Field(default=0)
