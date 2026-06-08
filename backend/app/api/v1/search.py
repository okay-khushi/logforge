"""
api/v1/search.py — Log Search Endpoint (Phase 2 stub)

This is a thin wrapper around the store's search method.
Phase 5 will expand this with full Elasticsearch DSL queries,
wildcard search, fuzzy matching, and highlighting.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.logging import get_logger
from app.services.log_store import InMemoryLogStore, get_log_store

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

    - **q**: Full-text search across message, service, and host fields
    - **level**: Filter by severity (DEBUG, INFO, WARN, ERROR, CRITICAL)
    - **service**: Filter by service name
    - **from_time / to_time**: ISO 8601 time range filter
    - **page / size**: Pagination controls
    """,
)
async def search_logs(
    q: Optional[str] = Query(default=None, description="Full-text search query"),
    level: Optional[str] = Query(default=None, description="Log severity level"),
    service: Optional[str] = Query(default=None, description="Service name filter"),
    from_time: Optional[datetime] = Query(default=None, description="Start of time range (ISO 8601)"),
    to_time: Optional[datetime] = Query(default=None, description="End of time range (ISO 8601)"),
    page: int = Query(default=1, ge=1, description="Page number (starts at 1)"),
    size: int = Query(default=50, ge=1, le=500, description="Results per page"),
    store: InMemoryLogStore = Depends(get_log_store),
) -> dict:
    """Search log entries with filters and pagination."""

    logger.info(
        "Search request",
        query=q,
        level=level,
        service=service,
        page=page,
        size=size,
    )

    results = await store.search(
        query=q,
        level=level,
        service=service,
        from_time=from_time,
        to_time=to_time,
        page=page,
        size=size,
    )

    return results
