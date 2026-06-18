"""
services/search_service.py — Business Logic Layer for Search

LAYER RESPONSIBILITIES:
    Route    → HTTP concerns (params, response codes, error handling)
    Service  → Business logic (shaping, enriching, computing derived fields)
    Repository → Database concerns (query building, ES API calls)

WHY A SERVICE LAYER BETWEEN ROUTE AND REPOSITORY?
    The route knows about HTTP. The repository knows about ES.
    The service knows about the BUSINESS DOMAIN — what a "summary" means,
    how to compute error_rate, how to reshape ES bucket format to BucketItem.

    Without the service layer, your route handlers balloon to 200+ lines
    mixing HTTP parameter parsing, ES response reshaping, and math.
    With it, each concern is isolated and testable independently.

    This is called the "Service Layer" pattern from Patterns of Enterprise
    Application Architecture (Fowler, 2002). Still the gold standard.

DEPENDENCY INJECTION:
    SearchService receives a SearchRepository in __init__().
    In tests, you can inject a MockSearchRepository that returns fixed data.
    No ES cluster needed to test business logic.
"""

import math
from datetime import datetime
from typing import Any, Optional

from app.core.logging import get_logger
from app.models.search import (
    AggregationResponse,
    BucketItem,
    SearchResponse,
    ServiceSummary,
    SummaryResponse,
    TimeSeriesBucket,
    TimeSeriesResponse,
)
from app.services.search_repository import SearchRepository

logger = get_logger(__name__)


def _hit_to_dict(hit: dict[str, Any]) -> dict[str, Any]:
    """
    Reshape an Elasticsearch hit into a clean API response dict.

    ES hit structure:
    {
        "_index": "logs-2024.01.15",
        "_id": "abc123",
        "_score": 1.5,
        "_source": {timestamp, level, service, ...},
        "highlight": {"message": ["...matched <mark>text</mark>..."]}
    }

    We flatten _source and add highlight if present.
    """
    source = hit.get("_source", {})
    result = dict(source)
    result["_score"] = hit.get("_score")

    # Inject highlighted message if query was a full-text search
    highlight = hit.get("highlight", {})
    if highlight and "message" in highlight:
        # Replace message with highlighted version (has <mark> tags)
        fragments = highlight["message"]
        result["message_highlight"] = fragments[0] if fragments else source.get("message", "")

    return result


class SearchService:
    """
    Orchestrates search operations.
    Calls SearchRepository, reshapes results into API response models.
    """

    def __init__(self, repository: SearchRepository) -> None:
        self._repo = repository

    async def search(
        self,
        q: Optional[str] = None,
        level: Optional[str] = None,
        service: Optional[str] = None,
        environment: Optional[str] = None,
        host: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        page: int = 1,
        size: int = 50,
        sort_by: str = "timestamp",
        sort_order: str = "desc",
    ) -> SearchResponse:
        """
        Execute a search and return a paginated SearchResponse.

        Reshaping logic:
        - ES total.value → total
        - Compute pages = ceil(total / size)
        - Flatten each hit._source into a clean dict
        - Inject _score and highlight into each result
        """
        logger.info(
            "Search request",
            q=q,
            level=level,
            service=service,
            environment=environment,
            page=page,
            size=size,
        )

        raw = await self._repo.search(
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

        hits = raw.get("hits", {})
        total = hits.get("total", {}).get("value", 0)
        took_ms = raw.get("took", 0)
        results = [_hit_to_dict(h) for h in hits.get("hits", [])]
        pages = math.ceil(total / size) if total > 0 else 0

        return SearchResponse(
            total=total,
            page=page,
            size=size,
            pages=pages,
            results=results,
            query_took_ms=took_ms,
        )

    async def get_level_breakdown(
        self,
        service: Optional[str] = None,
        environment: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> AggregationResponse:
        """
        Returns log count grouped by severity level.

        Response shape:
        {
            "total": 1500,
            "buckets": [
                {"key": "ERROR",    "count": 200},
                {"key": "INFO",     "count": 1100},
                {"key": "WARN",     "count": 150},
                {"key": "CRITICAL", "count": 50}
            ]
        }

        Used by: pie chart, level filter dropdown, anomaly banner.
        """
        raw = await self._repo.aggregate_by_field(
            field="level",
            service=service,
            environment=environment,
            from_time=from_time,
            to_time=to_time,
        )

        aggs = raw.get("aggregations", {})
        total = raw.get("hits", {}).get("total", {}).get("value", 0)
        took_ms = raw.get("took", 0)

        buckets = [
            BucketItem(key=b["key"], count=b["doc_count"])
            for b in aggs.get("by_field", {}).get("buckets", [])
        ]

        return AggregationResponse(
            total=total,
            buckets=buckets,
            query_took_ms=took_ms,
        )

    async def get_service_breakdown(
        self,
        level: Optional[str] = None,
        environment: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> AggregationResponse:
        """
        Returns log count grouped by service name.

        Response shape:
        {
            "total": 1500,
            "buckets": [
                {"key": "auth-service",      "count": 450},
                {"key": "payment-service",   "count": 320},
                {"key": "inventory-service", "count": 290}
            ]
        }

        Used by: service filter dropdown, service health heatmap.
        """
        raw = await self._repo.aggregate_by_field(
            field="service",
            level=level,
            environment=environment,
            from_time=from_time,
            to_time=to_time,
            size=20,
        )

        aggs = raw.get("aggregations", {})
        total = raw.get("hits", {}).get("total", {}).get("value", 0)
        took_ms = raw.get("took", 0)

        buckets = [
            BucketItem(key=b["key"], count=b["doc_count"])
            for b in aggs.get("by_field", {}).get("buckets", [])
        ]

        return AggregationResponse(
            total=total,
            buckets=buckets,
            query_took_ms=took_ms,
        )

    async def get_time_series(
        self,
        interval: str = "1h",
        q: Optional[str] = None,
        level: Optional[str] = None,
        service: Optional[str] = None,
        environment: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> TimeSeriesResponse:
        """
        Returns log volume over time, bucketed by interval.

        Response shape:
        {
            "interval": "1h",
            "total": 1500,
            "buckets": [
                {"timestamp": "2024-01-15T09:00:00", "count": 120},
                {"timestamp": "2024-01-15T10:00:00", "count": 203},
                {"timestamp": "2024-01-15T11:00:00", "count": 187}
            ]
        }

        Used by: time-series sparkline chart on the dashboard.

        INTERVAL VALIDATION:
            Accepts: 1m, 5m, 15m, 30m, 1h, 6h, 12h, 1d, 7d
            Invalid intervals default to 1h.
        """
        valid_intervals = {"1m", "5m", "15m", "30m", "1h", "6h", "12h", "1d", "7d"}
        if interval not in valid_intervals:
            interval = "1h"

        raw = await self._repo.aggregate_over_time(
            interval=interval,
            q=q,
            level=level,
            service=service,
            environment=environment,
            from_time=from_time,
            to_time=to_time,
        )

        aggs = raw.get("aggregations", {})
        total = raw.get("hits", {}).get("total", {}).get("value", 0)
        took_ms = raw.get("took", 0)

        buckets: list[TimeSeriesBucket] = []
        for b in aggs.get("over_time", {}).get("buckets", []):
            buckets.append(TimeSeriesBucket(
                timestamp=b["key_as_string"],
                count=b["doc_count"],
            ))

        return TimeSeriesResponse(
            interval=interval,
            buckets=buckets,
            total=total,
            query_took_ms=took_ms,
        )

    async def get_summary(
        self,
        environment: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> SummaryResponse:
        """
        Compute a complete system health summary in a single ES query.

        Returns:
        - Total log count
        - Error/warning/critical counts
        - Error rate
        - Per-service statistics (total, errors, error_rate)
        - Top 5 error messages (most frequent)

        Used by: dashboard overview cards, system health banner.

        ERROR RATE CALCULATION:
            error_rate = errors / total (where total > 0)
            A service with 100 logs and 20 errors has error_rate = 0.20 (20%).
        """
        raw = await self._repo.get_summary(
            environment=environment,
            from_time=from_time,
            to_time=to_time,
        )

        total = raw.get("hits", {}).get("total", {}).get("value", 0)
        took_ms = raw.get("took", 0)
        aggs = raw.get("aggregations", {})

        # Extract level counts from by_level terms agg
        level_counts: dict[str, int] = {}
        for b in aggs.get("by_level", {}).get("buckets", []):
            level_counts[b["key"]] = b["doc_count"]

        error_count = level_counts.get("ERROR", 0)
        warn_count = level_counts.get("WARN", 0)
        critical_count = level_counts.get("CRITICAL", 0)
        error_rate = round(error_count / total, 4) if total > 0 else 0.0

        # ── Per-service stats ──────────────────────────────────────────────────
        # by_service has total per service; errors_only.by_service has error count per service
        service_totals: dict[str, int] = {}
        for b in aggs.get("by_service", {}).get("buckets", []):
            service_totals[b["key"]] = b["doc_count"]

        service_errors: dict[str, int] = {}
        for b in (
            aggs.get("errors_only", {})
                .get("by_service", {})
                .get("buckets", [])
        ):
            service_errors[b["key"]] = b["doc_count"]

        services: list[ServiceSummary] = []
        for svc, svc_total in service_totals.items():
            svc_errors = service_errors.get(svc, 0)
            services.append(ServiceSummary(
                service=svc,
                total=svc_total,
                errors=svc_errors,
                warnings=0,  # Could add per-service warning agg in Phase 6
                error_rate=round(svc_errors / svc_total, 4) if svc_total > 0 else 0.0,
            ))

        # Sort services by error_rate descending (most problematic first)
        services.sort(key=lambda s: s.error_rate, reverse=True)

        # ── Top error messages ─────────────────────────────────────────────────
        top_errors: list[dict[str, Any]] = []
        for b in (
            aggs.get("errors_only", {})
                .get("top_messages", {})
                .get("buckets", [])
        ):
            top_errors.append({
                "message": b["key"],
                "count": b["doc_count"],
            })

        return SummaryResponse(
            total_logs=total,
            error_count=error_count,
            warn_count=warn_count,
            critical_count=critical_count,
            error_rate=error_rate,
            services=services,
            top_errors=top_errors,
            query_took_ms=took_ms,
        )

    async def get_available_services(self, prefix: Optional[str] = None) -> list[str]:
        """Return all service names (for filter dropdown / autocomplete)."""
        return await self._repo.get_distinct_values("service", prefix=prefix)

    async def get_available_levels(self) -> list[str]:
        """Return all observed severity levels."""
        return await self._repo.get_distinct_values("level")

    async def get_available_hosts(self, prefix: Optional[str] = None) -> list[str]:
        """Return all observed hostnames."""
        return await self._repo.get_distinct_values("host", prefix=prefix)
