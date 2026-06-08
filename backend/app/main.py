"""
main.py — FastAPI Application Entry Point

This is the heart of the backend. It:
1. Creates the FastAPI application instance
2. Configures middleware (CORS, request logging)
3. Registers all routers (routes/endpoints)
4. Sets up startup/shutdown lifecycle events
5. Provides the ASGI app object that uvicorn serves

EXECUTION ORDER:
    uvicorn app.main:app
        → Creates FastAPI app
        → Runs @app.on_event("startup") handlers
        → Starts serving HTTP requests
        → On CTRL+C: runs @app.on_event("shutdown") handlers

WHY LIFESPAN EVENTS?
    - startup: connect to databases, warm up ML model, create Kafka producers
    - shutdown: flush buffers, close connections gracefully

    Without shutdown cleanup, you get:
    - Kafka messages lost in the producer buffer
    - Database connection pool warnings
    - Elasticsearch pending writes dropped
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import health, ingest, search
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

# Configure logging FIRST — before any other imports that might log
configure_logging()
logger = get_logger(__name__)


# ─── Lifespan Context Manager ─────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages startup and shutdown lifecycle.

    This is the modern replacement for @app.on_event("startup").
    Code BEFORE yield runs on startup.
    Code AFTER yield runs on shutdown.
    """
    # ── STARTUP ──────────────────────────────────────────────────────────────
    logger.info(
        "LogForge API starting up",
        version=settings.app_version,
        environment=settings.app_env,
        debug=settings.debug,
    )

    # Phase 3: Initialize Elasticsearch connection pool here
    # Phase 4: Initialize Kafka producer here
    # Phase 8: Load ML model here

    logger.info("LogForge API ready to serve requests", port=settings.api_port)

    yield  # Application runs here

    # ── SHUTDOWN ──────────────────────────────────────────────────────────────
    logger.info("LogForge API shutting down gracefully")

    # Phase 3: Close Elasticsearch connection pool here
    # Phase 4: Flush and close Kafka producer here

    logger.info("Shutdown complete")


# ─── Application Factory ──────────────────────────────────────────────────────
def create_application() -> FastAPI:
    """
    Application factory — creates and configures the FastAPI app.

    WHY A FACTORY FUNCTION instead of a module-level variable?
    - Testability: tests can call create_application() with test settings
    - Multiple instances: different configs for different environments
    - Explicit initialization order
    """
    app = FastAPI(
        title="LogForge API",
        description="""
## Enterprise Log Analytics Platform

Ingest, search, analyze, and visualize log data at scale.

### Features
- 📥 **Batch log ingestion** — accept up to 1000 logs per request
- 🔍 **Full-text search** — find any log in milliseconds
- 🤖 **Anomaly detection** — Isolation Forest ML model
- 🚨 **Alerting** — rule-based alert engine
- 📊 **Analytics** — time-series and aggregation queries

### Authentication
Phase 2 has no authentication. JWT auth will be added in Phase 11.

### Rate Limiting
Currently unlimited. Rate limiting will be added in Phase 11.
        """,
        version=settings.app_version,
        docs_url="/docs",              # Swagger UI at /docs
        redoc_url="/redoc",            # ReDoc UI at /redoc
        openapi_url="/openapi.json",   # OpenAPI schema at /openapi.json
        lifespan=lifespan,
    )

    # ── CORS Middleware ───────────────────────────────────────────────────────
    # CORS = Cross-Origin Resource Sharing
    # Without this, your React frontend (localhost:3000) can't call the API
    # (localhost:8000) — browsers block cross-origin requests by default.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],           # GET, POST, PUT, DELETE, OPTIONS, etc.
        allow_headers=["*"],           # Authorization, Content-Type, etc.
    )

    # ── Request Timing Middleware ─────────────────────────────────────────────
    @app.middleware("http")
    async def add_request_timing(request: Request, call_next):
        """
        Log every request with its duration.
        This is the simplest form of API observability.

        In production, this data feeds into Prometheus metrics and dashboards.
        """
        start_time = time.perf_counter()

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        logger.info(
            "HTTP request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            client=request.client.host if request.client else "unknown",
        )

        # Add timing header so frontend can see API latency in DevTools
        response.headers["X-Process-Time-Ms"] = str(duration_ms)
        return response

    # ── Global Exception Handler ──────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """
        Catch any unhandled exception and return a clean JSON error response.

        Without this, FastAPI returns an HTML 500 error page, which is
        useless for API clients that expect JSON.
        """
        logger.error(
            "Unhandled exception",
            path=request.url.path,
            method=request.method,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "message": str(exc) if settings.debug else "An unexpected error occurred",
                "type": type(exc).__name__,
            },
        )

    # ── Register API Routers ──────────────────────────────────────────────────
    # All routes get the API prefix: /api/v1
    prefix = settings.api_prefix

    app.include_router(health.router, prefix=prefix)
    app.include_router(ingest.router, prefix=prefix)
    app.include_router(search.router, prefix=prefix)

    # Future routers (registered in later phases):
    # app.include_router(analytics.router, prefix=prefix)  # Phase 7
    # app.include_router(alerts.router, prefix=prefix)     # Phase 9

    # ── Root Redirect ─────────────────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    async def root():
        """Redirect root to API docs."""
        return {
            "message": "LogForge API",
            "version": settings.app_version,
            "docs": "/docs",
            "health": f"{prefix}/health",
        }

    return app


# ─── App Instance ──────────────────────────────────────────────────────────────
# This is what uvicorn imports: `uvicorn app.main:app`
app = create_application()
