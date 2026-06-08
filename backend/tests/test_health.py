"""
tests/test_health.py — Tests for Health Check Endpoints
"""

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoints:
    """Tests for /api/v1/health"""

    def test_health_returns_200(self, client: TestClient):
        """Main health endpoint should return 200."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_health_response_has_status_field(self, client: TestClient):
        """Health response must have a 'status' field."""
        response = client.get("/api/v1/health")
        data = response.json()
        assert "status" in data
        assert data["status"] in ("healthy", "degraded", "unhealthy")

    def test_health_response_has_version(self, client: TestClient):
        """Health response should include the API version."""
        response = client.get("/api/v1/health")
        data = response.json()
        assert "version" in data

    def test_health_response_has_timestamp(self, client: TestClient):
        """Health response should include a timestamp."""
        response = client.get("/api/v1/health")
        data = response.json()
        assert "timestamp" in data

    def test_health_response_has_services(self, client: TestClient):
        """Health response should have a services breakdown."""
        response = client.get("/api/v1/health")
        data = response.json()
        assert "services" in data

    def test_liveness_returns_200(self, client: TestClient):
        """Liveness probe should always return 200 if process is running."""
        response = client.get("/api/v1/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"

    def test_readiness_returns_200(self, client: TestClient):
        """Readiness probe should return 200 when all deps are healthy."""
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "checks" in data


class TestRootEndpoint:
    """Tests for the root / endpoint."""

    def test_root_returns_200(self, client: TestClient):
        """Root endpoint should return 200."""
        response = client.get("/")
        assert response.status_code == 200

    def test_root_includes_docs_link(self, client: TestClient):
        """Root response should include a link to /docs."""
        response = client.get("/")
        data = response.json()
        assert "docs" in data


class TestSearchEndpoint:
    """Basic tests for GET /api/v1/search"""

    def test_search_returns_200_with_no_params(self, client: TestClient):
        """Search with no parameters should return 200."""
        response = client.get("/api/v1/search")
        assert response.status_code == 200

    def test_search_response_has_required_fields(self, client: TestClient):
        """Search response should have total, page, size, results."""
        response = client.get("/api/v1/search")
        data = response.json()
        assert "total" in data
        assert "page" in data
        assert "size" in data
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_search_with_query_param(self, client: TestClient):
        """Search with q parameter should return 200."""
        response = client.get("/api/v1/search?q=database")
        assert response.status_code == 200

    def test_search_with_level_filter(self, client: TestClient):
        """Search with level filter should return 200."""
        response = client.get("/api/v1/search?level=ERROR")
        assert response.status_code == 200

    def test_search_invalid_size_returns_422(self, client: TestClient):
        """Search with size > 500 should return 422."""
        response = client.get("/api/v1/search?size=9999")
        assert response.status_code == 422
