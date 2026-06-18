"""
services/search_repository.py — Elasticsearch Query Layer (Repository Pattern)

ARCHITECTURE: Clean separation of concerns
    Route   → receives HTTP params, returns HTTP response
    Service → orchestrates business logic, calls repository
    Repository → speaks Elasticsearch, knows nothing about HTTP

WHY THIS SEPARATION?
    Without it, Elasticsearch query DSL bleeds into route handlers.
    When you need to switch to OpenSearch (AWS) or add a second data source,
    you change one file (the repository) — nothing else touches.

    This pattern is called the Repository Pattern (from Domain-Driven Design).

──────────────────────────────────────────────────────────────────────────────
ELASTICSEARCH SEARCH CONCEPTS (taught inline)
──────────────────────────────────────────────────────────────────────────────

INVERTED INDEX:
    How ES makes full-text search fast. When a document is indexed:

    Input:  "Failed to connect to database"
    Tokens: [failed, connect, database]

    ES builds a lookup table:
    "failed"   → [doc1, doc7, doc15]
    "connect"  → [doc1, doc3, doc9]
    "database" → [doc1, doc4, doc12]

    Query for "database" → O(1) lookup → [doc1, doc4, doc12]
    No full table scan. Scales to billions of documents.

QUERY vs FILTER CONTEXT:
    Query context: "How well does this document match?" (computes relevance score)
    Filter context: "Does this document match? Yes or No" (no scoring)

    Filters are:
    1. Faster — no score computation
    2. Cacheable — ES caches filter results in memory for repeated queries
    3. Binary — true/false, never partial

    Rule: Use filter for exact matches (level, service, time range).
          Use query for full-text search (message content).

BOOL QUERY STRUCTURE:
    {
        "bool": {
            "must":    [],   # must match AND scores count
            "filter":  [],   # must match AND scores DON'T count (cacheable)
            "should":  [],   # should match (OR — boosts score if match)
            "must_not":[]    # must NOT match
        }
    }

AGGREGATIONS:
    Run analytics alongside search results in a single request.
    No separate query needed.

    Terms agg:      GROUP BY level → {ERROR: 42, INFO: 201, WARN: 15}
    Date histogram: GROUP BY 1h   → {09:00: 50, 10:00: 73, 11:00: 41}
    Filter agg:     Subset count  → {total_errors: 42}
"""

from datetime import datetime
from typing import Any, Optional

from elasticsearch import AsyncElasticsearch, NotFoundError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

INDEX_PATTERN = "logs-*"


def _build_bool_query(
    q: Optional[str] = None,
    level: Optional[str] = None,
    service: Optional[str] = None,
    environment: Optional[str] = None,
    host: Optional[str] = None,
    from_time: Optional[datetime] = None,
    to_time: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    Build an Elasticsearch bool query from search parameters.

    DESIGN PRINCIPLE:
        Full-text search (q) goes in `must` → contributes to relevance score.
        All other filters go in `filter` → faster, cached, no scoring overhead.

    MULTI_MATCH vs MATCH:
        match: searches a single field
        multi_match: searches multiple fields simultaneously, returns the
                     document if ANY field matches.

        "fields": ["message^3", "service", "host"]
        The ^3 is a boost — a match in "message" is 3x more relevant
        than a match in "service" or "host".

    FUZZINESS:
        AUTO: 0 edits for 1-2 char terms, 1 edit for 3-5 chars, 2 edits for 6+
        This handles typos: "databse" matches "database".
    """
    must_clauses: list[dict] = []
    filter_clauses: list[dict] = []

    # ── Full-text search ───────────────────────────────────────────────────────
    if q:
        must_clauses.append({
            "multi_match": {
                "query": q,
                "fields": ["message^3", "service^2", "host"],
                "type": "best_fields",
                "fuzziness": "AUTO",
                "minimum_should_match": "75%",
            }
        })

    # ── Exact keyword filters (go in filter context — fast, cached) ────────────
    if level:
        filter_clauses.append({"term": {"level": level.upper()}})

    if service:
        filter_clauses.append({"term": {"service": service.lower()}})

    if environment:
        filter_clauses.append({"term": {"environment": environment.lower()}})

    if host:
        filter_clauses.append({"term": {"host": host.lower()}})

    # ── Time range filter ──────────────────────────────────────────────────────
    # range query on a date field uses the BKD tree index (not inverted index)
    # BKD tree: O(log N) range queries, extremely fast for time-series data
    time_range: dict[str, Any] = {}
    if from_time:
        time_range["gte"] = from_time.isoformat()
    if to_time:
        time_range["lte"] = to_time.isoformat()
    if time_range:
        filter_clauses.append({"range": {"timestamp": time_range}})

    # ── Assemble bool query ────────────────────────────────────────────────────
    if not must_clauses and not filter_clauses:
        return {"match_all": {}}

    bool_query: dict[str, Any] = {}
    if must_clauses:
        bool_query["must"] = must_clauses
    if filter_clauses:
        bool_query["filter"] = filter_clauses

    return {"bool": bool_query}


class SearchRepository:
    """
    All Elasticsearch interactions for the search layer.
    The service layer calls this; route handlers never call this directly.
    """

    def __init__(self, client: AsyncElasticsearch) -> None:
        self._client = client

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
    ) -> dict[str, Any]:
        """
        Execute a search query against Elasticsearch.

        Returns raw ES response data — the service layer reshapes it.

        FROM/SIZE PAGINATION:
            page=1, size=50 → from=0,  size=50  (docs 1-50)
            page=2, size=50 → from=50, size=50  (docs 51-100)
            page=3, size=50 → from=100, size=50 (docs 101-150)

            LIMITATION: ES won't return results past max_result_window (50,000).
            For deep pagination (page > 1000), use search_after (cursor-based).
            Phase 5 uses from/size which is fine for log explorer UX.

        SORT FIELDS:
            timestamp → date field (BKD tree, very fast)
            level     → keyword field (alphabetical)
            service   → keyword field (alphabetical)
            Avoid sorting on text fields (not indexed for sorting).

        TRACK_TOTAL_HITS:
            True  = exact count (may be slow for >10,000 results)
            False = approximate count (instant, shown as "10000+")
            We use True for accurate pagination. Use False in dashboards.
        """
        query = _build_bool_query(
            q=q,
            level=level,
            service=service,
            environment=environment,
            host=host,
            from_time=from_time,
            to_time=to_time,
        )

        # Validate sort field to prevent injection
        allowed_sort_fields = {"timestamp", "level", "service", "host", "environment"}
        if sort_by not in allowed_sort_fields:
            sort_by = "timestamp"

        from_offset = (page - 1) * size

        try:
            response = await self._client.search(
                index=INDEX_PATTERN,
                query=query,
                sort=[{sort_by: {"order": sort_order}}],
                from_=from_offset,
                size=size,
                source=True,
                track_total_hits=True,
                # highlight: shows which part of the message matched the query.
                # Wraps matching tokens in <mark>...</mark> for the frontend.
                **({"highlight": {
                    "fields": {
                        "message": {
                            "pre_tags": ["<mark>"],
                            "post_tags": ["</mark>"],
                            "number_of_fragments": 1,
                            "fragment_size": 200,
                        }
                    }
                }} if q else {}),
            )
            return response
        except NotFoundError:
            return {"hits": {"total": {"value": 0}, "hits": []}, "took": 0}
        except Exception as e:
            logger.error("Search query failed", error=str(e), query=str(query)[:200])
            raise

    async def aggregate_by_field(
        self,
        field: str,
        q: Optional[str] = None,
        level: Optional[str] = None,
        service: Optional[str] = None,
        environment: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        size: int = 20,
    ) -> dict[str, Any]:
        """
        Terms aggregation: GROUP BY a keyword field.

        TERMS AGGREGATION EXPLAINED:
            SELECT field, COUNT(*) as count
            FROM logs
            WHERE <filters>
            GROUP BY field
            ORDER BY count DESC
            LIMIT size

        ES equivalent:
            {"aggs": {"by_field": {"terms": {"field": "level", "size": 20}}}}

        Returns the top N values by document count.

        WHY NOT EXACT COUNTS?
            Terms agg returns approximate counts for high-cardinality fields.
            For low-cardinality fields like "level" (5 values), counts are exact.
            For "service" (100s of values), small errors are acceptable.
        """
        query = _build_bool_query(
            q=q,
            level=level,
            service=service,
            environment=environment,
            from_time=from_time,
            to_time=to_time,
        )

        try:
            response = await self._client.search(
                index=INDEX_PATTERN,
                query=query,
                size=0,  # Don't return documents — only aggregation results
                aggregations={
                    "by_field": {
                        "terms": {
                            "field": field,
                            "size": size,
                            "order": {"_count": "desc"},
                            # min_doc_count=1: only return buckets with at least 1 doc
                            "min_doc_count": 1,
                        }
                    },
                    "total_docs": {
                        # value_count on our stored 'id' keyword field
                        # (not _id — ES disallows fielddata on that metadata field)
                        "value_count": {"field": "id"}
                    },
                },
                track_total_hits=True,
            )
            return response
        except NotFoundError:
            return {
                "aggregations": {"by_field": {"buckets": []}, "total_docs": {"value": 0}},
                "took": 0,
            }
        except Exception as e:
            logger.error("Aggregation failed", field=field, error=str(e))
            raise

    async def aggregate_over_time(
        self,
        interval: str = "1h",
        q: Optional[str] = None,
        level: Optional[str] = None,
        service: Optional[str] = None,
        environment: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """
        Date histogram aggregation: log volume over time.

        DATE HISTOGRAM EXPLAINED:
            SELECT date_trunc('hour', timestamp), COUNT(*)
            FROM logs
            WHERE <filters>
            GROUP BY 1
            ORDER BY 1

        Drives the time-series sparkline chart in the React dashboard.

        CALENDAR vs FIXED intervals:
            calendar_interval: "1d" = one calendar day (accounts for DST)
            fixed_interval:    "24h" = exactly 24 hours (predictable)
            Use calendar for "daily" dashboards, fixed for analytics windows.

        ── ROOT CAUSE OF THE TIMELINE BUG ───────────────────────────────────
        Error:   search_phase_execution_exception (HTTP 400)
        Cause:   too_many_buckets_exception
        Message: "Trying to create too many buckets. Must be less than or
                  equal to: [65536] but this number of buckets was exceeded."

        When no from_time / to_time is supplied, the original code only
        set extended_bounds conditionally:

            **({...} if (from_time or to_time) else {})

        Without extended_bounds, Elasticsearch spans the histogram from the
        EARLIEST document to NOW. This cluster has documents from 2024-01-15
        to 2026-06-17 — roughly 21,000 hours. Two separate date_histogram
        aggregations (over_time + by_level_over_time) with 6 sub-buckets each
        produced ~250,000+ logical buckets, exceeding the 65,536 limit.

        ── THE FIX ───────────────────────────────────────────────────────────
        Two changes:

        1. ALWAYS pass extended_bounds. When the caller does not supply a time
           range, fall back to an interval-appropriate default window that keeps
           bucket counts safe (<1,000):

             1m  → last 12h  (720 buckets)
             5m  → last 24h  (288 buckets)
             15m → last 24h   (96 buckets)
             30m → last 48h   (96 buckets)
             1h  → last 7d   (168 buckets)   ← default
             6h  → last 30d  (120 buckets)
             12h → last 60d  (120 buckets)
             1d  → last 90d   (90 buckets)
             7d  → last 365d  (52 buckets)

        2. Remove the redundant by_level_over_time aggregation. It was an
           additional date_histogram that doubled bucket count. The service
           layer did not consume it.
        """
        query = _build_bool_query(
            q=q,
            level=level,
            service=service,
            environment=environment,
            from_time=from_time,
            to_time=to_time,
        )

        # ── Interval → ES aggregation key ─────────────────────────────────────
        # calendar_interval: respects DST boundaries (1d, 7d, 1M, 1y)
        # fixed_interval:    exact SI durations (1h, 30m, 5m, 1m)
        calendar_intervals = {"1d", "7d", "1M", "1y"}
        use_calendar = interval in calendar_intervals
        agg_interval_key = "calendar_interval" if use_calendar else "fixed_interval"

        # ── Always-bounded window ──────────────────────────────────────────────
        # extended_bounds MUST always be set. Without it, ES creates buckets
        # spanning the full document history → TooManyBucketsException.
        default_windows: dict[str, str] = {
            "1m":  "now-12h",
            "5m":  "now-24h",
            "15m": "now-24h",
            "30m": "now-48h",
            "1h":  "now-7d",
            "6h":  "now-30d",
            "12h": "now-60d",
            "1d":  "now-90d",
            "7d":  "now-365d",
        }
        bounds_min: str = (
            from_time.isoformat() if from_time
            else default_windows.get(interval, "now-7d")
        )
        bounds_max: str = to_time.isoformat() if to_time else "now"

        try:
            response = await self._client.search(
                index=INDEX_PATTERN,
                query=query,
                size=0,
                aggregations={
                    "over_time": {
                        "date_histogram": {
                            "field": "timestamp",
                            agg_interval_key: interval,
                            "min_doc_count": 0,
                            # extended_bounds: fills in empty time slots within the window
                            # so the chart shows a continuous line (no gaps).
                            "extended_bounds": {
                                "min": bounds_min,
                                "max": bounds_max,
                            },
                            # hard_bounds (ES 7.10+): clips the histogram to this range.
                            # Without this, ES ALSO creates buckets for documents that
                            # fall OUTSIDE the extended_bounds window. For fine-grained
                            # intervals (5m, 15m) with historical data spanning years,
                            # those out-of-range buckets alone exceed the 65,536 limit:
                            #   860 days × 288 buckets/day (5m) = ~247,680 buckets → 400 error
                            #   860 days × 24  buckets/day (1h) =  ~20,640 buckets → OK
                            # hard_bounds ensures NO bucket is created outside this range.
                            "hard_bounds": {
                                "min": bounds_min,
                                "max": bounds_max,
                            },
                        }
                    },
                },
                track_total_hits=True,
            )
            return response
        except NotFoundError:
            return {
                "aggregations": {"over_time": {"buckets": []}},
                "took": 0,
                "hits": {"total": {"value": 0}},
            }
        except Exception as e:
            logger.error("Time-series aggregation failed", interval=interval, error=str(e))
            raise

    async def get_summary(
        self,
        environment: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """
        Single-request summary: total counts, error rate, top services, top errors.

        Uses MULTIPLE AGGREGATIONS in ONE request — Elasticsearch computes all
        of them in a single pass over the data. Far more efficient than separate queries.

        FILTER AGGREGATION:
            Allows a sub-aggregation on a filtered subset without changing the
            outer query. Used here to count errors/warnings independently.

            Example:
            {
                "aggs": {
                    "errors_only": {
                        "filter": {"term": {"level": "ERROR"}},
                        "aggs": {
                            "top_messages": {"terms": {"field": "message.keyword"}}
                        }
                    }
                }
            }
        """
        query = _build_bool_query(
            environment=environment,
            from_time=from_time,
            to_time=to_time,
        )

        try:
            response = await self._client.search(
                index=INDEX_PATTERN,
                query=query,
                size=0,
                aggregations={
                    # Count per severity level
                    "by_level": {
                        "terms": {"field": "level", "size": 10}
                    },
                    # Count per service (top 10)
                    "by_service": {
                        "terms": {"field": "service", "size": 10, "order": {"_count": "desc"}}
                    },
                    # Filter agg: errors only → top error messages
                    "errors_only": {
                        "filter": {"term": {"level": "ERROR"}},
                        "aggs": {
                            "top_messages": {
                                "terms": {
                                    "field": "message.keyword",
                                    "size": 5,
                                    "order": {"_count": "desc"},
                                }
                            },
                            "by_service": {
                                "terms": {"field": "service", "size": 10}
                            },
                        },
                    },
                    # Filter agg: warnings only
                    "warnings_only": {
                        "filter": {"term": {"level": "WARN"}}
                    },
                    # Filter agg: critical only
                    "critical_only": {
                        "filter": {"term": {"level": "CRITICAL"}}
                    },
                },
                track_total_hits=True,
            )
            return response
        except NotFoundError:
            return {
                "hits": {"total": {"value": 0}},
                "aggregations": {},
                "took": 0,
            }
        except Exception as e:
            logger.error("Summary aggregation failed", error=str(e))
            raise

    async def get_distinct_values(
        self,
        field: str,
        prefix: Optional[str] = None,
        size: int = 50,
    ) -> list[str]:
        """
        Return distinct values for a keyword field.
        Used by the service filter dropdown and autocomplete.

        Uses terms aggregation (not a SELECT DISTINCT).
        For autocomplete with prefix filtering, a prefix query on the keyword
        field would be more accurate but slower for many unique values.
        """
        try:
            agg_query: dict[str, Any] = {"match_all": {}}
            if prefix:
                agg_query = {
                    "prefix": {field: {"value": prefix.lower()}}
                }

            response = await self._client.search(
                index=INDEX_PATTERN,
                query=agg_query,
                size=0,
                aggregations={
                    "distinct_values": {
                        "terms": {
                            "field": field,
                            "size": size,
                            "order": {"_key": "asc"},
                        }
                    }
                },
            )
            buckets = (
                response.get("aggregations", {})
                .get("distinct_values", {})
                .get("buckets", [])
            )
            return [b["key"] for b in buckets]
        except NotFoundError:
            return []
        except Exception as e:
            logger.error("Distinct values query failed", field=field, error=str(e))
            return []
