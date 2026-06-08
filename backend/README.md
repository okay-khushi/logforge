# FastAPI Backend — LogForge

This directory contains the Python FastAPI backend service.

## Structure

```
backend/
├── app/
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── ingest.py        # Log ingestion endpoints
│   │       ├── search.py        # Search endpoints
│   │       ├── analytics.py     # Analytics/aggregation endpoints
│   │       └── alerts.py        # Alert management endpoints
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py            # Settings from environment variables
│   │   └── logging.py           # Structured logging setup
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── log_entry.py         # Pydantic models for log entries
│   │   └── alert.py             # Pydantic models for alerts
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── elasticsearch.py     # ES client and operations
│   │   ├── kafka_producer.py    # Kafka message publishing
│   │   └── anomaly.py           # Anomaly detection service
│   │
│   └── main.py                  # FastAPI application entry point
│
├── tests/
│   ├── __init__.py
│   ├── test_ingest.py
│   └── test_search.py
│
├── Dockerfile
├── requirements.txt
└── README.md
```

## Running Locally (without Docker)

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Running Tests

```bash
cd backend
pytest tests/ -v
```
