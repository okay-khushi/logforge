"""
services/elasticsearch_store.py — Elasticsearch Log Store

This is the Phase 3 replacement for InMemoryLogStore.
It implements EXACTLY the same interface — all routes work without changes.

KEY ELASTICSEARCH CONCEPTS USED:

1. BULK API:
   Instead of indexing one document at a time (N network round-trips),
   we batch documents into a single request. This is 10-50x faster.
   
   Single doc:  POST /logs-2024.01.15/_doc  (for each log)
   Bulk:        POST /_bulk  (all logs in one request body)

2. QUERY DSL (Domain Specific Language):
   Elasticsearch uses JSON to describe queries — not SQL.
   
   SQL:   SELECT * FROM logs WHERE message LIKE '%error%' AND level='ERROR'
   ES:    {"query": {"bool": {"must": [{"match": {"message": "error"}},
          {"term": {"level": "ERROR"}}]}}}

3. AGGREGATIONS:
   Elasticsearch can compute GROUP BY + COUNT in one query alongside results.
   This powers the analytics charts in Phase 7.

4. INDEX NAMING STRATEGY:
   Daily indexes: logs-2024.01.15
   Query across all: logs-*  (wildcard)
   Query this month: logs-2024.01.*

5. SCROLL vs SEARCH_AFTER:
   For deep pagination (page 1000+), Elasticsearch recommends search_after
   (cursor-based) over from/size (offset-based) due to memory efficiency.
   We use from/size for now (adequate for page < 200) and will add
   search_after in Phase 5.
"""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from elasticsearch import AsyncElasticsearch, NotFoundError, RequestError

from app.core.es_index import INDEX_TEMPLATE_BODY, INDEX_TEMPLATE_NAME
from app.core.logging import get_logger
from app.models.log_entry import LogEntryInput, LogEntryStored

logger = get_logger(__name__)


def _get_index_name(timestamp: Optional[datetime] = None) -> str:
    """
    Generate the daily index name for a given timestamp.

    Format: logs-YYYY.MM.DD
    Example: logs-2024.01.15

    Using the log's timestamp (not now) ensures a log from yesterday
    lands in yesterday's index even if it arrives late.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    return f"logs-{timestamp.strftime('%Y.%m.%d')}"


def _get_index_pattern(days_back: int = 30) -> str:
    """
    Return an index wildcard that covers recent days.
    Uses '*' wildcard when no specific range is needed.
    """
    return "logs-*"


class ElasticsearchLogStore:
    """
    Production log store backed by Elasticsearch.

    Same interface as InMemoryLogStore — routes use this transparently
    via FastAPI's Depends() dependency injection.
    """

    def __init__(self, client: AsyncElasticsearch) -> None:
        self._client = client
        self._template_initialized = False

    async def ensure_index_template(self) -> None:
        """
        Register the index template with Elasticsearch.

        IDEMPOTENT: Safe to call multiple times.
        If the template already exists, this updates it.

        Called once at startup via the FastAPI lifespan.
        """
        if self._template_initialized:
            return

        try:
            await self._client.indices.put_index_template(
                name=INDEX_TEMPLATE_NAME,
                body=INDEX_TEMPLATE_BODY,
            )
            self._template_initialized = True
            logger.info(
                "Elasticsearch index template registered",
                template=INDEX_TEMPLATE_NAME,
            )
        except Exception as e:
            logger.error(
                "Failed to register index template",
                error=str(e),
                template=INDEX_TEMPLATE_NAME,
            )
            # Don't raise — the store still works, it just uses dynamic mapping
            self._template_initialized = True

    async def add_batch(self, entries: list[LogEntryInput]) -> list[LogEntryStored]:
        """
        Index a batch of log entries using the Elasticsearch Bulk API.

        BULK API FORMAT:
        The bulk body alternates between action + document pairs:
        
        { "index": { "_index": "logs-2024.01.15", "_id": "uuid-1" } }
        { "timestamp": "...", "level": "ERROR", ... }
        { "index": { "_index": "logs-2024.01.15", "_id": "uuid-2" } }
        { "timestamp": "...", "level": "INFO", ... }
        
        One HTTP request for any number of documents.
        """
        stored_entries: list[LogEntryStored] = []
        bulk_operations: list[dict] = []

        for entry in entries:
            stored = LogEntryStored.from_input(entry)
            stored_entries.append(stored)

            # Determine which daily index this log belongs in
            index_name = _get_index_name(stored.timestamp)
            doc = stored.to_es_document()

            # Bulk action: tells ES where to index this document
            bulk_operations.append({
                "index": {
                    "_index": index_name,
                    "_id": stored.id,   # Use our UUID as the ES document ID
                }
            })
            # The document itself
            bulk_operations.append(doc)

        if not bulk_operations:
            return []

        try:
            response = await self._client.bulk(
                operations=bulk_operations,
                refresh="wait_for",  # Wait until docs are searchable before returning
                                      # "wait_for" = up to refresh_interval (5s)
                                      # "true" = force immediate refresh (slow in prod)
                                      # "false" = don't wait (use in high-throughput prod)
            )

            # Check for per-document errors in the bulk response
            if response.get("errors"):
                error_count = sum(
                    1 for item in response["items"]
                    if item.get("index", {}).get("error")
                )
                logger.warning(
                    "Bulk index had partial errors",
                    total=len(entries),
                    errors=error_count,
                    successful=len(entries) - error_count,
                )
            else:
                logger.info(
                    "Bulk indexed successfully",
                    count=len(stored_entries),
                    index=_get_index_name(stored_entries[0].timestamp) if stored_entries else "none",
                )

        except Exception as e:
            logger.error("Bulk index failed", error=str(e), count=len(entries))
            raise

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
        """
        Search logs using the Elasticsearch Query DSL.

        BOOL QUERY STRUCTURE:
        must:   All conditions MUST match (AND) — affects relevance score
        filter: All conditions MUST match (AND) — does NOT affect score (faster)
        should: At least one condition should match (OR)
        must_not: Condition must NOT match

        For log search we use FILTER (not must) for level/service/time because:
        - We don't need relevance scoring for filter conditions
        - Filter results are cached by ES for repeated queries
        - 20-30% faster for high-cardinality filter fields
        """
        # Build the bool query
        must_clauses = []
        filter_clauses = []

        # Full-text search on message field (uses relevance scoring)
        if query:
            must_clauses.append({
                "multi_match": {
                    "query": query,
                    "fields": ["message^3", "service", "host"],
                    # ^3 boosts the message field — matches in message score 3x higher
                    "type": "best_fields",
                    "fuzziness": "AUTO",  # Auto-correct typos (1-2 char edits)
                }
            })

        # Exact filter for log level (keyword field → no scoring)
        if level:
            filter_clauses.append({"term": {"level": level.upper()}})

        # Exact filter for service name
        if service:
            filter_clauses.append({"term": {"service": service.lower()}})

        # Exact filter for deployment environment
        if environment:
            filter_clauses.append({"term": {"environment": environment.lower()}})

        # Time range filter
        time_range: dict[str, Any] = {}
        if from_time:
            time_range["gte"] = from_time.isoformat()
        if to_time:
            time_range["lte"] = to_time.isoformat()
        if time_range:
            filter_clauses.append({"range": {"timestamp": time_range}})

        # Assemble the complete query
        if must_clauses or filter_clauses:
            es_query = {
                "bool": {
                    **({"must": must_clauses} if must_clauses else {}),
                    **({"filter": filter_clauses} if filter_clauses else {}),
                }
            }
        else:
            # No filters — match all documents
            es_query = {"match_all": {}}

        # Pagination: from = (page - 1) * size
        from_offset = (page - 1) * size

        try:
            response = await self._client.search(
                index=_get_index_pattern(),
                query=es_query,
                sort=[{"timestamp": {"order": "desc"}}],  # Newest first
                from_=from_offset,
                size=size,
                source=True,  # Include _source (the actual document)
                track_total_hits=True,  # Get exact count (not approximate)
            )
        except NotFoundError:
            # Index doesn't exist yet (no logs ingested) — return empty
            return {"total": 0, "page": page, "size": size, "results": []}
        except Exception as e:
            logger.error("Search failed", error=str(e), query=query)
            raise

        # Parse hits
        hits = response.get("hits", {})
        total_value = hits.get("total", {})
        total = total_value.get("value", 0) if isinstance(total_value, dict) else total_value

        results = []
        for hit in hits.get("hits", []):
            doc = hit.get("_source", {})
            results.append(doc)

        return {
            "total": total,
            "page": page,
            "size": size,
            "results": results,
        }

    async def get_stats(self) -> dict[str, Any]:
        """
        Return log statistics using Elasticsearch Aggregations.

        AGGREGATIONS replace SQL GROUP BY + COUNT:
        SELECT level, COUNT(*) FROM logs GROUP BY level
        →
        {"aggs": {"by_level": {"terms": {"field": "level"}}}}

        This runs alongside the query in a single request — no separate query needed.
        """
        try:
            response = await self._client.search(
                index=_get_index_pattern(),
                query={"match_all": {}},
                size=0,  # We only want aggregation results, not actual documents
                aggregations={
                    "by_level": {
                        "terms": {
                            "field": "level",
                            "size": 10,
                        }
                    },
                    "by_service": {
                        "terms": {
                            "field": "service",
                            "size": 10,
                            "order": {"_count": "desc"},
                        }
                    },
                    "total_anomalies": {
                        "filter": {"term": {"is_anomaly": True}}
                    },
                },
            )
        except NotFoundError:
            return {
                "total_in_store": 0,
                "total_ingested_all_time": 0,
                "level_breakdown": {},
                "top_services": {},
                "total_anomalies": 0,
            }
        except Exception as e:
            logger.error("Failed to get stats", error=str(e))
            raise

        # Parse aggregation results
        aggs = response.get("aggregations", {})

        level_breakdown = {
            bucket["key"]: bucket["doc_count"]
            for bucket in aggs.get("by_level", {}).get("buckets", [])
        }

        top_services = {
            bucket["key"]: bucket["doc_count"]
            for bucket in aggs.get("by_service", {}).get("buckets", [])
        }

        total_anomalies = aggs.get("total_anomalies", {}).get("doc_count", 0)
        total_docs = response.get("hits", {}).get("total", {})
        total_count = total_docs.get("value", 0) if isinstance(total_docs, dict) else total_docs

        return {
            "total_in_store": total_count,
            "total_ingested_all_time": total_count,  # ES has no concept of "all time" without a separate counter
            "level_breakdown": level_breakdown,
            "top_services": top_services,
            "total_anomalies": total_anomalies,
        }

    async def health_check(self) -> dict[str, Any]:
        """Check Elasticsearch connectivity and cluster health."""
        try:
            health = await self._client.cluster.health(timeout="5s")
            return {
                "status": "healthy",
                "type": "elasticsearch",
                "cluster_status": health.get("status", "unknown"),
                "cluster_name": health.get("cluster_name", "unknown"),
                "active_shards": health.get("active_shards", 0),
                "url": "configured",
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "type": "elasticsearch",
                "error": str(e),
            }

    async def delete_index(self, index_pattern: str) -> bool:
        """
        Delete an index or pattern of indexes.
        DANGEROUS — used only in tests for cleanup.
        """
        try:
            await self._client.indices.delete(index=index_pattern, ignore_unavailable=True)
            logger.warning("Index deleted", pattern=index_pattern)
            return True
        except Exception as e:
            logger.error("Failed to delete index", pattern=index_pattern, error=str(e))
            return False

    async def count_documents(self, index_pattern: str = "logs-*") -> int:
        """Count total documents in the given index pattern."""
        try:
            response = await self._client.count(index=index_pattern)
            return response.get("count", 0)
        except NotFoundError:
            return 0
        except Exception:
            return 0
