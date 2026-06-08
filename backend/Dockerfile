# ─────────────────────────────────────────────────────────────────────────────
# LogForge Backend — Dockerfile
#
# This is a multi-stage build:
#   Stage 1 (builder): Install dependencies in an isolated environment
#   Stage 2 (runtime): Copy only what's needed to run (smaller image)
#
# WHY MULTI-STAGE?
#   Build dependencies (compilers, headers for psycopg2, etc.) add hundreds
#   of MB to the image. The runtime stage doesn't need them.
#   A smaller image = faster deploys, less attack surface, lower ECR storage cost.
#
# BUILD:  docker build -t logforge-backend .
# RUN:    docker run -p 8000:8000 --env-file .env logforge-backend
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# Set working directory inside the container
WORKDIR /build

# Install build dependencies for packages that need C compilation
# (psycopg2-binary, numpy, etc.)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements FIRST (Docker layer caching)
# WHY: If requirements.txt doesn't change, Docker reuses the cached
# pip install layer — builds go from 2 minutes to 10 seconds.
COPY requirements.txt .

# Install all Python dependencies into a virtual environment
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Security: Don't run as root
# Create a non-root user for the application process
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy ONLY the virtual environment from the builder stage
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

# Install only runtime system dependencies (no build tools)
RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy application source code
COPY app/ ./app/

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# ── Container Configuration ───────────────────────────────────────────────────
# Port the application listens on (must match API_PORT in .env)
EXPOSE 8000

# Health check — Docker/ECS checks this to decide if container is healthy
# --interval: check every 30 seconds
# --timeout: fail if no response in 10 seconds
# --retries: mark unhealthy after 3 consecutive failures
# --start-period: give the app 30 seconds to start before checking
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -f http://localhost:8000/api/v1/health/live || exit 1

# ── Startup Command ───────────────────────────────────────────────────────────
# uvicorn: ASGI server
# app.main:app — import app from app/main.py
# --host 0.0.0.0 — listen on all interfaces (required in containers)
# --port 8000 — match EXPOSE above
# --workers 4 — 4 parallel worker processes (use CPU count in production)
# NO --reload in production (--reload is for development only)
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--log-level", "info"]
