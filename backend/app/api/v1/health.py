"""
api/v1/health.py — Health Check Endpoints

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

    The distinction matters during startup: a process is ALIVE (not crashed)
    before it's READY (all connections established, warmed up).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.log_store import InMemoryLogStore, get_log_store

logger = get_logger(__name__)

router = APIRouter(
    prefix="/health",
    tags=["Health & Observability"],
)


@router.get(
    "",
    summary="Full health check",
    description="Returns the health status of all connected services.",
)
async def health_check(
    store: InMemoryLogStore = Depends(get_log_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Comprehensive health check.
    Checks every dependency: log store, (Elasticsearch in Phase 3), (Kafka in Phase 4).
    """
    store_health = await store.health_check()

    # Phase 3: add elasticsearch health check here
    # Phase 4: add kafka health check here

    overall_status = "healthy" if store_health["status"] == "healthy" else "degraded"

    health_response = {
        "status": overall_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": settings.app_version,
        "environment": settings.app_env,
        "services": {
            "log_store": store_health,
            "elasticsearch": "not_connected_until_phase_3",
            "kafka": "not_connected_until_phase_4",
        },
    }

    logger.info("Health check", status=overall_status)

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
    description="Returns 200 if the service is ready to accept traffic.",
)
async def readiness(
    store: InMemoryLogStore = Depends(get_log_store),
) -> dict:
    """
    Readiness check — verifies dependencies are available.
    In Phase 3 this will also verify Elasticsearch connectivity.
    """
    store_health = await store.health_check()
    is_ready = store_health["status"] == "healthy"

    return {
        "status": "ready" if is_ready else "not_ready",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "log_store": store_health["status"],
        },
    }
