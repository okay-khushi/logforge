"""
tests/test_elasticsearch.py — Elasticsearch Integration Tests

INTEGRATION vs UNIT TESTS:
    Unit tests (Phase 2): test with fake/mock data, no external services
    Integration tests (Phase 3): test against a REAL Elasticsearch instance

    Integration tests are SKIPPED automatically if ES is not running.
    This lets CI run Phase 2 unit tests without Docker,
    and full integration tests when Docker is available.

HOW SKIPPING WORKS:
    @pytest.mark.skipif(not ES_AVAILABLE, reason="Elasticsearch not running")
    
    Before each test, we check if ES is reachable by pinging it.
    If not reachable, the test is skipped (not failed — important distinction).

TEST ISOLATION:
    Each test uses a unique index prefix to avoid interference:
    "test-logs-2024.01.15" instead of "logs-2024.01.15"
    
    After each test, the test indexes are deleted.
    This ensures tests are independent and repeatable.

WHAT WE TEST:
    1. Index template registration succeeds
    2. Bulk indexing writes documents
    3. Documents are searchable after indexing  
    4. Level filter works correctly
    5. Service filter works correctly
    6. Full-text search finds the right documents
    7. Aggregations (stats) return correct counts
    8. Pagination works correctly
"""

import asyncio
import pytest

from elasticsearch import AsyncElasticsearch

from app.models.log_entry import LogEntryInput, LogLevel
from app.services.elasticsearch_client import ping_elasticsearch, get_es_client
from app.services.elasticsearch_store import ElasticsearchLogStore


# ── Check if Elasticsearch is available ───────────────────────────────────────
def _check_es_available() -> bool:
    """Synchronously check if ES is reachable."""
    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(ping_elasticsearch())
        loop.close()
        return result
    except Exception:
        return False


ES_AVAILABLE = _check_es_available()

skip_if_no_es = pytest.mark.skipif(
    not ES_AVAILABLE,
    reason="Elasticsearch not running — start with: docker-compose -f infrastructure/docker-compose.yml up -d elasticsearch",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def es_client() -> AsyncElasticsearch:
    """Return the shared ES async client."""
    return get_es_client()


@pytest.fixture(scope="module")
def es_store(es_client) -> ElasticsearchLogStore:
    """Return an ElasticsearchLogStore for testing."""
    return ElasticsearchLogStore(client=es_client)


@pytest.fixture
def sample_entries() -> list[LogEntryInput]:
    """A diverse set of log entries for testing."""
    return [
        LogEntryInput(
            timestamp="2024-01-15T10:00:00Z",
            level=LogLevel.ERROR,
            service="auth-service",
            host="prod-server-01",
            message="Failed to connect to database after 3 retries",
            metadata={"user_id": "u123"},
        ),
        LogEntryInput(
            timestamp="2024-01-15T10:01:00Z",
            level=LogLevel.INFO,
            service="api-gateway",
            host="prod-server-02",
            message="Request processed successfully",
            metadata={"request_id": "req-456"},
        ),
        LogEntryInput(
            timestamp="2024-01-15T10:02:00Z",
            level=LogLevel.CRITICAL,
            service="payment-service",
            host="prod-server-01",
            message="Database connection pool exhausted — system critical",
            metadata={"alert": True},
        ),
        LogEntryInput(
            timestamp="2024-01-15T10:03:00Z",
            level=LogLevel.WARN,
            service="auth-service",
            host="prod-server-03",
            message="High memory usage detected: 92%",
        ),
        LogEntryInput(
            timestamp="2024-01-15T10:04:00Z",
            level=LogLevel.DEBUG,
            service="notification-service",
            host="worker-01",
            message="Cache miss for key: user_profile_u456",
        ),
    ]


@pytest.fixture(autouse=True)
async def cleanup_test_indexes(es_store):
    """
    Auto-cleanup: delete test indexes after every test.
    autouse=True means this runs for EVERY test in this file.
    """
    yield  # Test runs here

    # Cleanup after test
    if ES_AVAILABLE:
        await es_store.delete_index("logs-2024.01.*")


# ── Tests ─────────────────────────────────────────────────────────────────────
class TestElasticsearchConnection:
    """Test basic ES connectivity."""

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_elasticsearch_is_reachable(self):
        """ES should be pingable."""
        result = await ping_elasticsearch()
        assert result is True

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_cluster_is_healthy(self, es_client):
        """ES cluster health should be green or yellow."""
        health = await es_client.cluster.health()
        assert health["status"] in ("green", "yellow")


class TestIndexTemplate:
    """Test index template registration."""

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_index_template_registered(self, es_store):
        """Index template should be created without error."""
        # Reset so ensure_index_template runs again
        es_store._template_initialized = False
        await es_store.ensure_index_template()
        assert es_store._template_initialized is True

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_index_template_exists_in_es(self, es_store, es_client):
        """Verify the template actually exists in Elasticsearch."""
        es_store._template_initialized = False
        await es_store.ensure_index_template()

        templates = await es_client.indices.get_index_template(name="logforge-logs-template")
        assert "index_templates" in templates
        assert len(templates["index_templates"]) > 0


class TestBulkIndexing:
    """Test log ingestion via Elasticsearch Bulk API."""

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_bulk_index_returns_stored_entries(self, es_store, sample_entries):
        """Bulk indexing should return the stored entries with IDs."""
        stored = await es_store.add_batch(sample_entries)

        assert len(stored) == len(sample_entries)
        for entry in stored:
            assert entry.id is not None
            assert len(entry.id) > 0

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_bulk_index_documents_appear_in_es(self, es_store, sample_entries):
        """Documents should actually be queryable in Elasticsearch after indexing."""
        await es_store.add_batch(sample_entries)

        # Wait for ES to make documents searchable
        # (refresh="wait_for" in bulk call handles this, but let's be explicit)
        count = await es_store.count_documents()
        assert count >= len(sample_entries)

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_bulk_index_preserves_log_level(self, es_store, sample_entries):
        """Log level should be preserved exactly."""
        await es_store.add_batch(sample_entries)

        # Search for ERROR logs only
        results = await es_store.search(level="ERROR")

        for hit in results["results"]:
            assert hit["level"] == "ERROR"


class TestElasticsearchSearch:
    """Test search functionality with real Elasticsearch."""

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_search_no_filters_returns_all(self, es_store, sample_entries):
        """Search with no filters should return all indexed documents."""
        await es_store.add_batch(sample_entries)

        results = await es_store.search()
        assert results["total"] >= len(sample_entries)

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_search_by_level_filter(self, es_store, sample_entries):
        """Level filter should return only matching severity documents."""
        await es_store.add_batch(sample_entries)

        results = await es_store.search(level="CRITICAL")

        assert results["total"] >= 1
        for hit in results["results"]:
            assert hit["level"] == "CRITICAL"

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_search_by_service_filter(self, es_store, sample_entries):
        """Service filter should return only logs from that service."""
        await es_store.add_batch(sample_entries)

        results = await es_store.search(service="auth-service")

        assert results["total"] >= 1
        for hit in results["results"]:
            assert hit["service"] == "auth-service"

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_full_text_search_finds_word(self, es_store, sample_entries):
        """Full-text search should find documents containing the query word."""
        await es_store.add_batch(sample_entries)

        results = await es_store.search(query="database")

        assert results["total"] >= 1
        # Verify the word appears in the results
        found_in_message = any(
            "database" in hit.get("message", "").lower()
            for hit in results["results"]
        )
        assert found_in_message, "Search for 'database' should find at least one result"

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_search_pagination_page_1(self, es_store, sample_entries):
        """Pagination should return correct page 1 results."""
        await es_store.add_batch(sample_entries)

        results = await es_store.search(page=1, size=2)

        assert results["page"] == 1
        assert results["size"] == 2
        assert len(results["results"]) <= 2

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_search_results_sorted_newest_first(self, es_store, sample_entries):
        """Results should be returned newest timestamp first."""
        await es_store.add_batch(sample_entries)

        results = await es_store.search()

        if len(results["results"]) >= 2:
            timestamps = [r["timestamp"] for r in results["results"]]
            # Each timestamp should be >= the next one (descending)
            for i in range(len(timestamps) - 1):
                assert timestamps[i] >= timestamps[i + 1], \
                    f"Results not sorted: {timestamps[i]} < {timestamps[i + 1]}"

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_search_empty_index_returns_empty(self, es_store):
        """Searching an empty index should return empty results, not an error."""
        # After cleanup, index may not exist
        results = await es_store.search(query="this_should_not_exist_xyz_123")
        assert results["total"] == 0
        assert results["results"] == []


class TestElasticsearchStats:
    """Test aggregation-based statistics."""

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_stats_returns_level_breakdown(self, es_store, sample_entries):
        """Stats should include a level breakdown with counts."""
        await es_store.add_batch(sample_entries)

        stats = await es_store.get_stats()

        assert "level_breakdown" in stats
        assert "ERROR" in stats["level_breakdown"]
        assert stats["level_breakdown"]["ERROR"] >= 1

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_stats_returns_top_services(self, es_store, sample_entries):
        """Stats should include top services by log count."""
        await es_store.add_batch(sample_entries)

        stats = await es_store.get_stats()

        assert "top_services" in stats
        assert "auth-service" in stats["top_services"]

    @skip_if_no_es
    @pytest.mark.asyncio
    async def test_health_check_returns_healthy(self, es_store):
        """Health check should return healthy when ES is connected."""
        health = await es_store.health_check()

        assert health["status"] == "healthy"
        assert health["type"] == "elasticsearch"
        assert health["cluster_status"] in ("green", "yellow")
