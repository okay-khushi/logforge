"""
tests/test_ingest.py — Tests for the Log Ingestion API

TEST STRATEGY:
    We test at the HTTP level using FastAPI's TestClient.
    This means we test the FULL stack: routing → validation → business logic → response.
    This is more valuable than unit-testing each function in isolation.

    Test categories:
    1. Happy path — valid inputs produce expected outputs
    2. Validation errors — invalid inputs produce 422 with meaningful errors
    3. Edge cases — empty batch, batch at max size, missing optional fields
    4. Response structure — assert response schema matches documented API
"""

import pytest
from fastapi.testclient import TestClient


class TestIngestEndpoint:
    """Tests for POST /api/v1/ingest"""

    def test_ingest_single_log_returns_202(self, client: TestClient, sample_log: dict):
        """
        GIVEN a valid single log entry
        WHEN POST /api/v1/ingest is called
        THEN the response is 202 Accepted with correct fields
        """
        payload = {"logs": [sample_log]}
        response = client.post("/api/v1/ingest", json=payload)

        assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"

    def test_ingest_response_has_required_fields(self, client: TestClient, sample_log: dict):
        """
        GIVEN a valid ingest request
        WHEN the response is received
        THEN it contains status, ingested count, and batch_id
        """
        payload = {"logs": [sample_log]}
        response = client.post("/api/v1/ingest", json=payload)
        data = response.json()

        assert "status" in data
        assert "ingested" in data
        assert "batch_id" in data
        assert data["status"] == "accepted"
        assert data["ingested"] == 1
        assert len(data["batch_id"]) > 0  # Non-empty UUID string

    def test_ingest_batch_counts_correctly(self, client: TestClient, sample_batch: dict):
        """
        GIVEN a batch of 3 log entries
        WHEN POST /api/v1/ingest is called
        THEN the ingested count equals 3
        """
        response = client.post("/api/v1/ingest", json=sample_batch)

        assert response.status_code == 202
        assert response.json()["ingested"] == 3

    def test_ingest_without_optional_metadata(self, client: TestClient):
        """
        GIVEN a log entry without the optional 'metadata' field
        WHEN POST /api/v1/ingest is called
        THEN it succeeds (metadata defaults to empty dict)
        """
        payload = {
            "logs": [{
                "timestamp": "2024-01-15T10:30:00Z",
                "level": "INFO",
                "service": "test-service",
                "host": "test-host",
                "message": "No metadata provided",
            }]
        }
        response = client.post("/api/v1/ingest", json=payload)
        assert response.status_code == 202

    def test_ingest_without_timestamp_uses_current_time(self, client: TestClient):
        """
        GIVEN a log entry without a timestamp
        WHEN POST /api/v1/ingest is called
        THEN it succeeds (timestamp defaults to current time)
        """
        payload = {
            "logs": [{
                "level": "WARN",
                "service": "test-service",
                "host": "test-host",
                "message": "No timestamp provided",
            }]
        }
        response = client.post("/api/v1/ingest", json=payload)
        assert response.status_code == 202

    def test_ingest_accepts_all_valid_log_levels(self, client: TestClient):
        """
        GIVEN log entries with each valid level
        WHEN POST /api/v1/ingest is called
        THEN all are accepted
        """
        levels = ["DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL"]
        for level in levels:
            payload = {
                "logs": [{
                    "level": level,
                    "service": "test-service",
                    "host": "test-host",
                    "message": f"Test log at level {level}",
                }]
            }
            response = client.post("/api/v1/ingest", json=payload)
            assert response.status_code == 202, f"Level {level} was rejected"

    def test_ingest_empty_batch_returns_422(self, client: TestClient):
        """
        GIVEN an empty logs array
        WHEN POST /api/v1/ingest is called
        THEN it returns 422 Unprocessable Entity
        """
        payload = {"logs": []}
        response = client.post("/api/v1/ingest", json=payload)
        assert response.status_code == 422

    def test_ingest_invalid_level_returns_422(self, client: TestClient):
        """
        GIVEN a log with an invalid level like "TRACE"
        WHEN POST /api/v1/ingest is called
        THEN it returns 422 with a validation error
        """
        payload = {
            "logs": [{
                "level": "TRACE",  # Not a valid LogLevel
                "service": "test-service",
                "host": "test-host",
                "message": "This should fail",
            }]
        }
        response = client.post("/api/v1/ingest", json=payload)
        assert response.status_code == 422

    def test_ingest_missing_required_field_returns_422(self, client: TestClient):
        """
        GIVEN a log entry missing the required 'message' field
        WHEN POST /api/v1/ingest is called
        THEN it returns 422 with a clear error
        """
        payload = {
            "logs": [{
                "level": "INFO",
                "service": "test-service",
                "host": "test-host",
                # message is missing
            }]
        }
        response = client.post("/api/v1/ingest", json=payload)
        assert response.status_code == 422
        error_detail = response.json()
        assert "detail" in error_detail

    def test_ingest_invalid_timestamp_returns_422(self, client: TestClient):
        """
        GIVEN a log with an unparseable timestamp string
        WHEN POST /api/v1/ingest is called
        THEN it returns 422
        """
        payload = {
            "logs": [{
                "timestamp": "not-a-real-date",
                "level": "INFO",
                "service": "test-service",
                "host": "test-host",
                "message": "Bad timestamp",
            }]
        }
        response = client.post("/api/v1/ingest", json=payload)
        assert response.status_code == 422

    def test_ingest_missing_body_returns_422(self, client: TestClient):
        """
        GIVEN no request body at all
        WHEN POST /api/v1/ingest is called
        THEN it returns 422
        """
        response = client.post("/api/v1/ingest")
        assert response.status_code == 422


class TestIngestStats:
    """Tests for GET /api/v1/ingest/stats"""

    def test_stats_endpoint_returns_200(self, client: TestClient):
        """Stats endpoint should always return 200."""
        response = client.get("/api/v1/ingest/stats")
        assert response.status_code == 200

    def test_stats_response_structure(self, client: TestClient):
        """Stats response should have expected fields."""
        response = client.get("/api/v1/ingest/stats")
        data = response.json()

        assert "status" in data
        assert "data" in data
        assert data["status"] == "ok"
        assert "total_in_store" in data["data"]
        assert "level_breakdown" in data["data"]
