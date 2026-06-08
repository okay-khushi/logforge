"""
services/log_store.py — In-Memory Log Storage (Phase 2 Stub)

WHY AN IN-MEMORY STORE FOR PHASE 2?
    We want a working, testable API before Elasticsearch is integrated (Phase 3).
    This stub implements the exact same interface that the Elasticsearch service
    will use later. When Phase 3 is complete, we swap this class out — the
    routes don't need to change at all.

    This is the DEPENDENCY INJECTION pattern:
    - Routes depend on an abstract interface (store logs, search logs)
    - The concrete implementation (in-memory vs Elasticsearch) is injected
    - Tests can inject a fake implementation without touching the real one

    This is how every major FastAPI production codebase is structured.
"""

from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.core.logging import get_logger
from app.models.log_entry import LogEntryInput, LogEntryStored

logger = get_logger(__name__)

# Maximum entries in the in-memory store
# (Prevents unbounded memory growth in Phase 2 before ES is connected)
MAX_IN_MEMORY_LOGS = 10_000


class InMemoryLogStore:
    """
    Temporary in-memory log store.

    Uses a deque (double-ended queue) for O(1) append and efficient
    trimming of old entries when the max size is reached.

    REPLACED IN PHASE 3 by ElasticsearchLogStore with the same interface.
    """

    def __init__(self, max_size: int = MAX_IN_MEMORY_LOGS) -> None:
        # deque with maxlen automatically drops the oldest entry when full
        self._store: deque[LogEntryStored] = deque(maxlen=max_size)
        self._total_ingested: int = 0
        logger.info("InMemoryLogStore initialized", max_size=max_size)

    async def add_batch(self, entries: list[LogEntryInput]) -> list[LogEntryStored]:
        """
        Validate and store a batch of log entries.
        Returns the stored entries with their assigned IDs.
        """
        stored_entries: list[LogEntryStored] = []

        for entry in entries:
            stored = LogEntryStored.from_input(entry)
            self._store.append(stored)
            stored_entries.append(stored)

        self._total_ingested += len(stored_entries)

        logger.info(
            "Logs stored",
            count=len(stored_entries),
            total_in_store=len(self._store),
            total_ingested_all_time=self._total_ingested,
        )

        return stored_entries

    async def search(
        self,
        query: Optional[str] = None,
        level: Optional[str] = None,
        service: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        page: int = 1,
        size: int = 50,
    ) -> dict[str, Any]:
        """
        Simple in-memory search with filtering.

        NOTE: This is O(n) — fine for Phase 2 testing but
        Elasticsearch (Phase 3) makes this O(log n) with inverted indexes.
        """
        results = list(self._store)

        # Filter by text query (case-insensitive substring match)
        if query:
            query_lower = query.lower()
            results = [
                r for r in results
                if query_lower in r.message.lower()
                or query_lower in r.service.lower()
                or query_lower in r.host.lower()
            ]

        # Filter by log level
        if level:
            results = [r for r in results if r.level == level.upper()]

        # Filter by service name
        if service:
            results = [r for r in results if r.service == service.lower()]

        # Filter by time range
        if from_time:
            results = [r for r in results if r.timestamp >= from_time]
        if to_time:
            results = [r for r in results if r.timestamp <= to_time]

        # Sort by timestamp descending (newest first)
        results.sort(key=lambda r: r.timestamp, reverse=True)

        # Paginate
        total = len(results)
        start = (page - 1) * size
        end = start + size
        paginated = results[start:end]

        return {
            "total": total,
            "page": page,
            "size": size,
            "results": [r.model_dump() for r in paginated],
        }

    async def get_stats(self) -> dict[str, Any]:
        """Return basic statistics about stored logs."""
        all_logs = list(self._store)

        level_counts: dict[str, int] = {}
        service_counts: dict[str, int] = {}

        for log in all_logs:
            level_counts[log.level] = level_counts.get(log.level, 0) + 1
            service_counts[log.service] = service_counts.get(log.service, 0) + 1

        return {
            "total_in_store": len(all_logs),
            "total_ingested_all_time": self._total_ingested,
            "level_breakdown": level_counts,
            "top_services": dict(
                sorted(service_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            ),
        }

    async def health_check(self) -> dict[str, Any]:
        """Return health status of the store."""
        return {
            "status": "healthy",
            "type": "in_memory",
            "stored_count": len(self._store),
            "max_capacity": self._store.maxlen,
        }


# ─── Singleton instance ────────────────────────────────────────────────────────
# FastAPI's Depends() system will inject this into routes.
# In Phase 3 we'll replace this with an Elasticsearch-backed store.
_log_store = InMemoryLogStore()


def get_log_store() -> InMemoryLogStore:
    """
    FastAPI dependency — returns the singleton log store.

    Usage in routes:
        from fastapi import Depends
        from app.services.log_store import get_log_store, InMemoryLogStore

        @router.post("/ingest")
        async def ingest(
            batch: LogBatchInput,
            store: InMemoryLogStore = Depends(get_log_store)
        ):
            await store.add_batch(batch.logs)
    """
    return _log_store
