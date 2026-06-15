"""
services/elasticsearch_client.py — Elasticsearch Async Client (Singleton)

WHY A SEPARATE CLIENT MODULE?
    The Elasticsearch client manages a connection pool (think: a pool of HTTP
    connections that are reused instead of opened/closed per request).

    Opening/closing a connection for every API call is 10-100x slower.
    By making the client a module-level singleton, the pool is created ONCE
    at startup and shared across all requests.

CONNECTION POOL vs SINGLE CONNECTION:
    - Pool: multiple connections open simultaneously
    - Handles concurrent requests without waiting
    - If one connection is busy, another from the pool handles the request
    - Elasticsearch client manages this automatically

ASYNC vs SYNC:
    We use AsyncElasticsearch (not Elasticsearch) because our FastAPI routes
    are async. Using a sync client inside an async route would block the
    event loop — every other request waits until the sync call finishes.
    The async client uses aiohttp under the hood and never blocks.

RETRY LOGIC:
    The client has built-in retry on connection failures (configurable via
    max_retries). This handles transient network blips in production.
"""

from typing import Optional

from elasticsearch import AsyncElasticsearch, ConnectionError as ESConnectionError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Module-level singleton
_es_client: Optional[AsyncElasticsearch] = None


def get_es_client() -> AsyncElasticsearch:
    """
    Return the singleton AsyncElasticsearch client.
    Called once at startup — subsequent calls return the same instance.

    The client is NOT a FastAPI Depends() here because:
    - ES client should be created ONCE, not per-request
    - Connection pool lives for the lifetime of the application
    - We want to close it gracefully in the lifespan shutdown hook
    """
    global _es_client

    if _es_client is None:
        _es_client = _create_client()

    return _es_client


def _create_client() -> AsyncElasticsearch:
    """Build the AsyncElasticsearch client from settings."""

    # Build connection kwargs
    kwargs: dict = {
        "hosts": [settings.elasticsearch_url],
        "max_retries": 3,             # Retry 3 times on connection error
        "retry_on_timeout": True,     # Retry on timeout (not just connection error)
        "request_timeout": 30,        # 30 second per-request timeout
        "sniff_on_start": False,      # Don't auto-discover cluster nodes (not needed for single-node)
    }

    # Add authentication if configured (needed for AWS OpenSearch)
    if settings.elasticsearch_username and settings.elasticsearch_password:
        kwargs["basic_auth"] = (
            settings.elasticsearch_username,
            settings.elasticsearch_password,
        )

    client = AsyncElasticsearch(**kwargs)

    logger.info(
        "Elasticsearch client created",
        url=settings.elasticsearch_url,
        authenticated=bool(settings.elasticsearch_username),
    )

    return client


async def close_es_client() -> None:
    """
    Close the client and its connection pool gracefully.
    Called from the FastAPI shutdown lifespan hook.

    Without this, you get ResourceWarning: Unclosed client session errors.
    """
    global _es_client

    if _es_client is not None:
        await _es_client.close()
        _es_client = None
        logger.info("Elasticsearch client closed")


async def ping_elasticsearch() -> bool:
    """
    Check if Elasticsearch is reachable.
    Used by health check endpoints.

    Returns True if ES responds, False if not reachable.

    NOTE: We use client.info() (GET /) instead of client.ping() (HEAD /)
    because Elasticsearch 8.x returns HTTP 400 for HEAD / requests, which
    causes ping() to always return False even when ES is healthy.
    """
    try:
        client = get_es_client()
        await client.info()
        return True
    except (ESConnectionError, Exception) as e:
        logger.warning("Elasticsearch ping failed", error=str(e))
        return False


async def get_cluster_health() -> dict:
    """
    Get detailed Elasticsearch cluster health.
    Returns a dict with status (green/yellow/red), node counts, etc.
    """
    try:
        client = get_es_client()
        health = await client.cluster.health()
        return {
            "status": health.get("status", "unknown"),
            "cluster_name": health.get("cluster_name", "unknown"),
            "number_of_nodes": health.get("number_of_nodes", 0),
            "active_shards": health.get("active_shards", 0),
            "url": settings.elasticsearch_url,
        }
    except Exception as e:
        return {
            "status": "unreachable",
            "error": str(e),
            "url": settings.elasticsearch_url,
        }
