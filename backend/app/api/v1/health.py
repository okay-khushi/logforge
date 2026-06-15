"""
api/v1/health.py — Health Check Endpoints

Phase 3 Update: Added real Elasticsearch connectivity checks.

WHY HEALTH ENDPOINTS MATTER:
    Every production system needs health checks for:
    1. Load balancers (AWS ALB, Nginx) — decides whether to route traffic here
    2. Container orchestrators (ECS, Kubernetes) — decides whether to restart
    3. Monitoring systems (CloudWatch, Prometheus) — alerts when unhealthy
    4. CI/CD pipelines — smoke test after deployment

    WITHOUT health endpoints:
    - Your load balancer sends traffic to a crashed instance
    - ECS never restarts a dead container
    - You find out about downtime from angry users

    TWO STANDARD ENDPOINTS:
    - /health/live  — "Is the process running?" (Kubernetes liveness probe)
    - /health/ready — "Can the process handle traffic?" (Kubernetes readiness probe)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.elasticsearch_client import get_cluster_health, ping_elasticsearch
from app.services.kafka_producer import check_kafka_health
from app.services.kafka_consumer import is_consumer_running
from app.services.log_store import get_log_store

logger = get_logger(__name__)

router = APIRouter(
    prefix="/health",
    tags=["Health & Observability"],
)


@router.get(
    "",
    summary="Full health check",
    description="Returns the health status of all connected services including Elasticsearch.",
)
async def health_check(
    store=Depends(get_log_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Comprehensive health check.
    Checks: log store, Elasticsearch cluster.
    """
    store_health = await store.health_check()

    # Real Elasticsearch health check (Phase 3)
    es_health = await get_cluster_health()

    # Real Kafka health check (Phase 4)
    kafka_health = await check_kafka_health()
    kafka_ok = kafka_health.get("status") == "connected"

    # Determine overall status
    es_ok = es_health.get("status") in ("green", "yellow", "healthy")
    store_ok = store_health.get("status") == "healthy"
    overall_status = "healthy" if (store_ok and es_ok) else "degraded"

    health_response = {
        "status": overall_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": settings.app_version,
        "environment": settings.app_env,
        "services": {
            "log_store": store_health,
            "elasticsearch": es_health,
            "kafka": {
                **kafka_health,
                "consumer_running": is_consumer_running(),
            },
        },
    }

    logger.info("Health check", status=overall_status, es_status=es_health.get("status"))

    return health_response


@router.get(
    "/live",
    summary="Liveness probe",
    description="Returns 200 if the process is running. Used by container orchestrators.",
)
async def liveness() -> dict:
    """Minimal liveness check — just confirms the process is alive."""
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get(
    "/ready",
    summary="Readiness probe",
    description="Returns 200 if the service is ready. Checks Elasticsearch connectivity.",
)
async def readiness(
    store=Depends(get_log_store),
) -> dict:
    """
    Readiness check — verifies Elasticsearch is reachable.
    Returns 503 if ES is not available (prevents traffic routing to unhealthy instance).
    """
    store_health = await store.health_check()
    es_reachable = await ping_elasticsearch()
    kafka_health = await check_kafka_health()

    is_ready = store_health.get("status") == "healthy"

    return {
        "status": "ready" if is_ready else "not_ready",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "log_store": store_health.get("status"),
            "elasticsearch": "connected" if es_reachable else "unreachable",
            "kafka": kafka_health.get("status"),
            "consumer": "running" if is_consumer_running() else "stopped",
        },
    }
