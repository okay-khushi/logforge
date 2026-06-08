"""
pytest.ini / conftest.py equivalent — pytest configuration

WHY A conftest.py?
    pytest automatically discovers and loads conftest.py files.
    They define "fixtures" — reusable setup/teardown code shared across tests.

    Our test fixtures:
    1. `client` — a TestClient that calls our FastAPI app in-process (no HTTP needed)
    2. `sample_log` — a valid log entry dict reused across tests
    3. `sample_batch` — a valid batch of logs for ingest tests

IMPORTANT — pytest-asyncio:
    FastAPI route handlers are async functions.
    pytest-asyncio lets pytest `await` them in tests.
    We use `asyncio_mode = "auto"` in pytest.ini so every test
    is automatically treated as async.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_application


@pytest.fixture(scope="module")
def client():
    """
    Create a TestClient for the FastAPI app.

    scope="module" means the app is created ONCE per test file,
    not once per test function. This is more efficient.

    TestClient uses httpx under the hood and runs the app in-process,
    so tests are fast (no network overhead).
    """
    app = create_application()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_log() -> dict:
    """A single valid log entry."""
    return {
        "timestamp": "2024-01-15T10:30:00Z",
        "level": "ERROR",
        "service": "auth-service",
        "host": "prod-server-01",
        "message": "Failed to connect to database after 3 retries",
        "metadata": {"user_id": "u123", "request_id": "req-456"},
    }


@pytest.fixture
def sample_batch(sample_log) -> dict:
    """A batch with multiple log entries."""
    return {
        "logs": [
            sample_log,
            {
                "timestamp": "2024-01-15T10:31:00Z",
                "level": "INFO",
                "service": "api-gateway",
                "host": "prod-server-02",
                "message": "Request processed successfully",
            },
            {
                "timestamp": "2024-01-15T10:32:00Z",
                "level": "CRITICAL",
                "service": "payment-service",
                "host": "prod-server-01",
                "message": "Database connection pool exhausted",
                "metadata": {"alert": True},
            },
        ]
    }
