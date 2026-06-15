"""
api/v1/search.py — Log Search Endpoints

Phase 3 Update: Connected to real Elasticsearch via ElasticsearchLogStore.
The route itself is unchanged — it calls store.search() which dispatches to
either ES (if available) or in-memory (if ES is down).

ROUTE: GET /api/v1/search

QUERY PARAMETERS:
    q           Full-text search across message, service, host
    level       Exact severity filter (DEBUG|INFO|WARN|ERROR|CRITICAL)
    service     Exact service name filter
    environment Exact environment filter (production|staging|development)
    from_time   ISO 8601 start of time range
    to_time     ISO 8601 end of time range
    page        Page number (1-indexed)
    size        Results per page (1–500)

ELASTICSEARCH UNDER THE HOOD:
    GET /api/v1/search?q=database&level=ERROR&environment=production
    ↓
    ES Query DSL:
    {
        "query": {
            "bool": {
                "must":   [{"multi_match": {"query": "database", "fields": ["message^3", ...]}}],
                "filter": [{"term": {"level": "ERROR"}},
                           {"term": {"environment": "production"}}]
            }
        },
        "sort": [{"timestamp": {"order": "desc"}}],
        "from": 0, "size": 50
    }
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.logging import get_logger
from app.services.log_store import get_log_store

logger = get_logger(__name__)

router = APIRouter(
    prefix="/search",
    tags=["Search"],
)


@router.get(
    "",
    summary="Search log entries",
    description="""
    Search logs with optional full-text query and filters.

    - **q**: Full-text search across message, service, and host fields (fuzzy match)
    - **level**: Filter by severity (DEBUG, INFO, WARN, ERROR, CRITICAL)
    - **service**: Filter by service name (exact match)
    - **environment**: Filter by deployment environment (production, staging, development)
    - **from_time / to_time**: ISO 8601 time range filter
    - **page / size**: Pagination controls (cursor-based pagination in Phase 5)

    Results are always sorted **newest first** by timestamp.
    """,
)
async def search_logs(
    q: Optional[str] = Query(default=None, description="Full-text search query (fuzzy)"),
    level: Optional[str] = Query(default=None, description="Log severity level (exact match)"),
    service: Optional[str] = Query(default=None, description="Service name filter (exact match)"),
    environment: Optional[str] = Query(
        default=None, description="Deployment environment (production|staging|development)"
    ),
    from_time: Optional[datetime] = Query(default=None, description="Start of time range (ISO 8601)"),
    to_time: Optional[datetime] = Query(default=None, description="End of time range (ISO 8601)"),
    page: int = Query(default=1, ge=1, description="Page number (starts at 1)"),
    size: int = Query(default=50, ge=1, le=500, description="Results per page"),
    store=Depends(get_log_store),
) -> dict:
    """Search log entries with filters and pagination."""

    logger.info(
        "Search request",
        query=q,
        level=level,
        service=service,
        environment=environment,
        page=page,
        size=size,
    )

    results = await store.search(
        query=q,
        level=level,
        service=service,
        environment=environment,
        from_time=from_time,
        to_time=to_time,
        page=page,
        size=size,
    )

    return results
