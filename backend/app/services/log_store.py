"""
services/log_store.py — Log Store Protocol + Dependency Provider

Phase 3 Update: Added LogStoreProtocol and smart dependency routing.

THE DEPENDENCY INJECTION UPGRADE:
    Phase 2: get_log_store() always returned InMemoryLogStore
    Phase 3: get_log_store() returns ElasticsearchLogStore if ES is reachable,
             falls back to InMemoryLogStore if ES is not available.

    The routes (ingest.py, search.py) don't know or care which store they get.
    They just call await store.add_batch(...) and it works.

PROTOCOL vs ABC:
    We use typing.Protocol instead of ABC (Abstract Base Class) because:
    - Protocol = structural subtyping ("duck typing" with type hints)
    - A class satisfies the Protocol if it has the right methods — no need to
      explicitly inherit from the Protocol
    - This means InMemoryLogStore works as a LogStore without changing its code
"""

from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.models.log_entry import LogEntryInput, LogEntryStored

logger = get_logger(__name__)

MAX_IN_MEMORY_LOGS = 10_000


# ── Protocol Definition ───────────────────────────────────────────────────────
@runtime_checkable
class LogStoreProtocol(Protocol):
    """
    The interface that all log store implementations must satisfy.

    Any class with these async methods is a valid log store:
    - InMemoryLogStore (Phase 2)
    - ElasticsearchLogStore (Phase 3+)
    - MockLogStore (for testing)

    Runtime-checkable means isinstance(obj, LogStoreProtocol) works.
    """

    async def add_batch(self, entries: list[LogEntryInput]) -> list[LogEntryStored]:
        """Store a batch of log entries. Returns the stored entries with IDs."""
        ...

    async def search(
        self,
        query: Optional[str],
        level: Optional[str],
        service: Optional[str],
        environment: Optional[str],
        from_time: Optional[datetime],
        to_time: Optional[datetime],
        page: int,
        size: int,
    ) -> dict[str, Any]:
        """Search logs with optional filters. Returns paginated results."""
        ...

    async def get_stats(self) -> dict[str, Any]:
        """Return statistics about stored logs."""
        ...

    async def health_check(self) -> dict[str, Any]:
        """Return health status of the store."""
        ...


# ── InMemoryLogStore (Phase 2 fallback) ──────────────────────────────────────
class InMemoryLogStore:
    """
    Temporary in-memory log store.
    Used when Elasticsearch is not available (local dev without Docker).

    REPLACED IN PRODUCTION by ElasticsearchLogStore (Phase 3).
    Still useful for: unit tests, quick local demos, CI without Docker.
    """

    def __init__(self, max_size: int = MAX_IN_MEMORY_LOGS) -> None:
        self._store: deque[LogEntryStored] = deque(maxlen=max_size)
        self._total_ingested: int = 0
        logger.info("InMemoryLogStore initialized", max_size=max_size)

    async def add_batch(self, entries: list[LogEntryInput]) -> list[LogEntryStored]:
        stored_entries: list[LogEntryStored] = []

        for entry in entries:
            stored = LogEntryStored.from_input(entry)
            self._store.append(stored)
            stored_entries.append(stored)

        self._total_ingested += len(stored_entries)

        logger.info(
            "Logs stored in memory",
            count=len(stored_entries),
            total_in_store=len(self._store),
        )

        return stored_entries

    async def search(
        self,
        query: Optional[str] = None,
        level: Optional[str] = None,
        service: Optional[str] = None,
        environment: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        page: int = 1,
        size: int = 50,
    ) -> dict[str, Any]:
        results = list(self._store)

        if query:
            query_lower = query.lower()
            results = [
                r for r in results
                if query_lower in r.message.lower()
                or query_lower in r.service.lower()
                or query_lower in r.host.lower()
            ]

        if level:
            results = [r for r in results if r.level == level.upper()]

        if service:
            results = [r for r in results if r.service == service.lower()]

        if environment:
            results = [r for r in results if r.environment.lower() == environment.lower()]

        if from_time:
            from_time_aware = from_time.replace(tzinfo=timezone.utc) if from_time.tzinfo is None else from_time
            results = [r for r in results if r.timestamp >= from_time_aware]
        if to_time:
            to_time_aware = to_time.replace(tzinfo=timezone.utc) if to_time.tzinfo is None else to_time
            results = [r for r in results if r.timestamp <= to_time_aware]

        results.sort(key=lambda r: r.timestamp, reverse=True)

        total = len(results)
        start = (page - 1) * size
        paginated = results[start:start + size]

        return {
            "total": total,
            "page": page,
            "size": size,
            "results": [r.model_dump() for r in paginated],
        }

    async def get_stats(self) -> dict[str, Any]:
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
        return {
            "status": "healthy",
            "type": "in_memory",
            "stored_count": len(self._store),
            "max_capacity": self._store.maxlen,
        }


# ── Singleton instances ────────────────────────────────────────────────────────
_in_memory_store: Optional[InMemoryLogStore] = None

# The active store (set during app startup in main.py)
# This is the ONLY place where the concrete implementation is chosen.
_active_store: Optional[Any] = None


def set_active_store(store: Any) -> None:
    """
    Called from main.py startup to set the active store.
    Either an ElasticsearchLogStore or InMemoryLogStore.
    """
    global _active_store
    _active_store = store
    logger.info(
        "Active log store set",
        store_type=type(store).__name__,
    )


def get_log_store() -> Any:
    """
    FastAPI Depends() function — returns the active log store.

    Priority:
    1. ElasticsearchLogStore (if ES connected, set during startup)
    2. InMemoryLogStore (fallback if ES not available)
    """
    global _active_store, _in_memory_store

    if _active_store is not None:
        return _active_store

    # Fallback: create in-memory store if nothing set yet
    if _in_memory_store is None:
        _in_memory_store = InMemoryLogStore()
    return _in_memory_store
