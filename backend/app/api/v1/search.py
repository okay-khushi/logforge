"""
api/v1/search.py — Search and Analytics Endpoints (Phase 5 Complete Rewrite)

ENDPOINT DESIGN RATIONALE:

GET /api/v1/search
    Core log search. Query params are the standard REST idiom for filtering.
    Bookmarkable, shareable, cacheable by CDN/proxy.
    Returns paginated results with optional highlights.

GET /api/v1/search/summary
    Single-call dashboard overview. Returns totals, error rate, top services,
    top errors. Avoids 5 separate API calls from the frontend.

GET /api/v1/search/levels
    Returns count of logs per severity level.
    Drives pie chart / donut chart on the dashboard.

GET /api/v1/search/services
    Returns count of logs per service.
    Drives bar chart / heatmap on the dashboard.

GET /api/v1/search/timeline
    Returns log volume over time in buckets.
    Drives the sparkline / time-series chart on the dashboard.

GET /api/v1/search/filter-options
    Returns distinct service names, levels, environments for dropdowns.
    Single call populates all filter UI elements.

CLEAN ARCHITECTURE in route handlers:
    - Route validates HTTP params (FastAPI handles this via type hints)
    - Route calls service, not repository (never ES directly)
    - Route returns service result (service already built the response model)
    - Route catches exceptions, converts to appropriate HTTP errors
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.logging import get_logger
from app.models.search import (
    AggregationResponse,
    SearchResponse,
    SummaryResponse,
    TimeSeriesResponse,
)
from app.services.elasticsearch_client import get_es_client
from app.services.search_repository import SearchRepository
from app.services.search_service import SearchService

logger = get_logger(__name__)

router = APIRouter(
    prefix="/search",
    tags=["Search"],
)


def get_search_service() -> SearchService:
    """
    Dependency factory: creates SearchRepository → SearchService per request.

    WHY PER-REQUEST (not singleton)?
        The ES client (AsyncElasticsearch) is the singleton — it manages the
        connection pool. The repository and service are lightweight wrappers
        with no state. Creating them per-request is ~0.01ms and avoids
        any cross-request state bugs.

    FastAPI Depends() calls this function and injects the result.
    """
    client = get_es_client()
    repo = SearchRepository(client)
    return SearchService(repo)


# ─────────────────────────────────────────────────────────────────────────────
# Core Log Search
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=SearchResponse,
    summary="Search log entries",
    description="""
Search logs with full-text query and/or exact filters.

**Full-text search** (`q`): Searches `message`, `service`, and `host` fields
with fuzzy matching (handles typos) and relevance scoring.

**Filters**: Exact matches on `level`, `service`, `environment`, `host`.
Filters are faster than queries — Elasticsearch caches them.

**Time range**: ISO 8601 `from_time` and `to_time`.

**Pagination**: `page` (1-indexed) and `size` (1-500).

**Sorting**: `sort_by` (timestamp|level|service) and `sort_order` (asc|desc).

When `q` is provided, results include `message_highlight` with matching
terms wrapped in `<mark>` tags for the frontend to render.
    """,
)
async def search_logs(
    q: Optional[str] = Query(
        default=None,
        description="Full-text search query (fuzzy match across message, service, host)",
        examples=["database timeout"],
    ),
    level: Optional[str] = Query(
        default=None,
        description="Filter by severity level (DEBUG|INFO|WARN|ERROR|CRITICAL)",
        examples=["ERROR"],
    ),
    service: Optional[str] = Query(
        default=None,
        description="Filter by service name (exact match)",
        examples=["auth-service"],
    ),
    environment: Optional[str] = Query(
        default=None,
        description="Filter by environment (production|staging|development)",
        examples=["production"],
    ),
    host: Optional[str] = Query(
        default=None,
        description="Filter by hostname (exact match)",
        examples=["prod-server-01"],
    ),
    from_time: Optional[datetime] = Query(
        default=None,
        description="Start of time range (ISO 8601)",
        examples=["2024-01-15T00:00:00Z"],
    ),
    to_time: Optional[datetime] = Query(
        default=None,
        description="End of time range (ISO 8601)",
        examples=["2024-01-15T23:59:59Z"],
    ),
    page: int = Query(default=1, ge=1, le=10_000, description="Page number (1-indexed)"),
    size: int = Query(default=50, ge=1, le=500, description="Results per page"),
    sort_by: str = Query(
        default="timestamp",
        description="Sort field: timestamp | level | service | host",
    ),
    sort_order: str = Query(
        default="desc",
        pattern="^(asc|desc)$",
        description="Sort direction: asc (oldest first) | desc (newest first)",
    ),
    svc: SearchService = Depends(get_search_service),
) -> SearchResponse:
    """Search log entries with full-text and filter capabilities."""
    try:
        return await svc.search(
            q=q,
            level=level,
            service=service,
            environment=environment,
            host=host,
            from_time=from_time,
            to_time=to_time,
            page=page,
            size=size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except Exception as e:
        logger.error("Search endpoint error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard Summary
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/summary",
    response_model=SummaryResponse,
    summary="Get system health summary",
    description="""
Returns a complete health overview in a single request:

- **total_logs**: Total log count
- **error_count / warn_count / critical_count**: Counts by severity
- **error_rate**: Fraction of logs that are errors (0.0 – 1.0)
- **services**: Per-service stats sorted by error rate (most problematic first)
- **top_errors**: Most frequent error messages

Designed for the dashboard overview card. One API call replaces 5+ separate queries.
    """,
)
async def get_summary(
    environment: Optional[str] = Query(
        default=None,
        description="Filter by environment",
    ),
    from_time: Optional[datetime] = Query(default=None, description="Start of time range"),
    to_time: Optional[datetime] = Query(default=None, description="End of time range"),
    svc: SearchService = Depends(get_search_service),
) -> SummaryResponse:
    """Get overall system health summary and error analytics."""
    try:
        return await svc.get_summary(
            environment=environment,
            from_time=from_time,
            to_time=to_time,
        )
    except Exception as e:
        logger.error("Summary endpoint error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Summary failed: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Aggregations — Log Breakdown
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/levels",
    response_model=AggregationResponse,
    summary="Get log count by severity level",
    description="""
Returns the number of logs for each severity level.

Example response:
```json
{
  "total": 1500,
  "buckets": [
    {"key": "INFO",     "count": 900},
    {"key": "ERROR",    "count": 300},
    {"key": "WARN",     "count": 200},
    {"key": "CRITICAL", "count": 100}
  ]
}
```

Drives the severity breakdown pie chart on the dashboard.
    """,
)
async def get_level_breakdown(
    service: Optional[str] = Query(default=None, description="Filter by service"),
    environment: Optional[str] = Query(default=None, description="Filter by environment"),
    from_time: Optional[datetime] = Query(default=None),
    to_time: Optional[datetime] = Query(default=None),
    svc: SearchService = Depends(get_search_service),
) -> AggregationResponse:
    """Get log count grouped by severity level."""
    try:
        return await svc.get_level_breakdown(
            service=service,
            environment=environment,
            from_time=from_time,
            to_time=to_time,
        )
    except Exception as e:
        logger.error("Level breakdown error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Aggregation failed: {str(e)}",
        )


@router.get(
    "/services",
    response_model=AggregationResponse,
    summary="Get log count by service",
    description="""
Returns the number of logs from each service, sorted by count (descending).

Example response:
```json
{
  "total": 1500,
  "buckets": [
    {"key": "auth-service",      "count": 450},
    {"key": "payment-service",   "count": 320},
    {"key": "inventory-service", "count": 290}
  ]
}
```

Drives the service heatmap / bar chart on the dashboard.
    """,
)
async def get_service_breakdown(
    level: Optional[str] = Query(default=None, description="Filter by level"),
    environment: Optional[str] = Query(default=None, description="Filter by environment"),
    from_time: Optional[datetime] = Query(default=None),
    to_time: Optional[datetime] = Query(default=None),
    svc: SearchService = Depends(get_search_service),
) -> AggregationResponse:
    """Get log count grouped by service name."""
    try:
        return await svc.get_service_breakdown(
            level=level,
            environment=environment,
            from_time=from_time,
            to_time=to_time,
        )
    except Exception as e:
        logger.error("Service breakdown error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Aggregation failed: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Time Series
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/timeline",
    response_model=TimeSeriesResponse,
    summary="Get log volume over time",
    description="""
Returns log counts bucketed by time interval.

**interval** options: `1m`, `5m`, `15m`, `30m`, `1h`, `6h`, `12h`, `1d`, `7d`

Example response (interval=1h):
```json
{
  "interval": "1h",
  "total": 1500,
  "buckets": [
    {"timestamp": "2024-01-15T09:00:00.000Z", "count": 120},
    {"timestamp": "2024-01-15T10:00:00.000Z", "count": 203}
  ]
}
```

Drives the time-series sparkline chart on the dashboard.
    """,
)
async def get_timeline(
    interval: str = Query(
        default="1h",
        description="Time bucket interval: 1m|5m|15m|30m|1h|6h|12h|1d|7d",
    ),
    q: Optional[str] = Query(default=None, description="Full-text query"),
    level: Optional[str] = Query(default=None, description="Filter by level"),
    service: Optional[str] = Query(default=None, description="Filter by service"),
    environment: Optional[str] = Query(default=None, description="Filter by environment"),
    from_time: Optional[datetime] = Query(default=None),
    to_time: Optional[datetime] = Query(default=None),
    svc: SearchService = Depends(get_search_service),
) -> TimeSeriesResponse:
    """Get log volume over time."""
    try:
        return await svc.get_time_series(
            interval=interval,
            q=q,
            level=level,
            service=service,
            environment=environment,
            from_time=from_time,
            to_time=to_time,
        )
    except Exception as e:
        logger.error("Timeline error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Timeline aggregation failed: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Filter Options (Dropdown Autocomplete)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/filter-options",
    summary="Get available filter values",
    description="""
Returns distinct values for all filter fields.
One call populates all filter dropdowns in the UI.

Response:
```json
{
  "services":     ["auth-service", "payment-service"],
  "levels":       ["CRITICAL", "DEBUG", "ERROR", "INFO", "WARN"],
  "environments": ["production", "staging"]
}
```
    """,
)
async def get_filter_options(
    svc: SearchService = Depends(get_search_service),
) -> dict:
    """Return all distinct values for filter dropdowns."""
    try:
        services = await svc.get_available_services()
        levels = await svc.get_available_levels()
        environments = await svc._repo.get_distinct_values("environment")
        hosts = await svc.get_available_hosts()

        return {
            "services": services,
            "levels": levels,
            "environments": environments,
            "hosts": hosts,
        }
    except Exception as e:
        logger.error("Filter options error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch filter options: {str(e)}",
        )
