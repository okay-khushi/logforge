# API Design — LogForge v1

Base URL: `http://localhost:8000/api/v1`

---

## Ingestion Endpoints

### POST /ingest
Ingest a single log entry or batch of logs.

**Request Body:**
```json
{
  "logs": [
    {
      "timestamp": "2024-01-15T10:30:00Z",
      "level": "ERROR",
      "service": "auth-service",
      "host": "prod-server-01",
      "message": "Failed to connect to database",
      "metadata": {
        "user_id": "u123",
        "request_id": "req-456"
      }
    }
  ]
}
```

**Response:**
```json
{
  "status": "accepted",
  "ingested": 1,
  "batch_id": "batch-uuid"
}
```

---

## Search Endpoints

### GET /search
Full-text search across logs with filters.

**Query Parameters:**
- `q` (string) — full-text search query
- `level` (string) — filter by severity: DEBUG, INFO, WARN, ERROR, CRITICAL
- `service` (string) — filter by service name
- `from_time` (ISO8601) — start of time range
- `to_time` (ISO8601) — end of time range
- `page` (int, default: 1) — pagination
- `size` (int, default: 50, max: 500) — results per page

**Response:**
```json
{
  "total": 1543,
  "page": 1,
  "size": 50,
  "results": [
    {
      "id": "log-uuid",
      "timestamp": "2024-01-15T10:30:00Z",
      "level": "ERROR",
      "service": "auth-service",
      "host": "prod-server-01",
      "message": "Failed to connect to database",
      "anomaly_score": 0.87,
      "is_anomaly": true
    }
  ]
}
```

---

## Analytics Endpoints

### GET /analytics/volume
Log volume over time (for trend charts).

**Query Parameters:**
- `interval` — `1m`, `5m`, `1h`, `1d`
- `from_time`, `to_time`

### GET /analytics/severity
Breakdown of logs by severity level.

### GET /analytics/services
Top services by log volume and error rate.

### GET /analytics/anomalies
Anomaly trend over time.

---

## Alerts Endpoints

### GET /alerts
List all triggered alerts.

### POST /alerts/rules
Create a new alert rule.

**Request Body:**
```json
{
  "name": "High Error Rate",
  "condition": "error_count > 100",
  "window_minutes": 5,
  "service": "auth-service",
  "notify_email": "ops@company.com"
}
```

### PUT /alerts/rules/{rule_id}
Update an existing rule.

### DELETE /alerts/rules/{rule_id}
Delete an alert rule.

---

## Health & Observability

### GET /health
System health check.

**Response:**
```json
{
  "status": "healthy",
  "elasticsearch": "connected",
  "kafka": "connected",
  "version": "1.0.0"
}
```

### GET /metrics
Prometheus-compatible metrics endpoint.
