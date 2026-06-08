# LogForge — System Architecture

## Overview

LogForge is an enterprise-grade log analytics platform modeled after Splunk. It ingests,
stores, searches, analyzes, and visualizes log data from multiple sources at scale.

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                             │
│   [Server Logs]    [Application Logs]    [System Metrics]       │
└──────────────┬──────────────────────────────────────────────────┘
               │ HTTP POST / File Upload / Agent
               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      INGESTION LAYER                            │
│   [FastAPI Ingest API]          [Kafka Message Broker]          │
│    /api/v1/ingest                 Topic: raw-logs               │
└──────────────┬──────────────────────────────────────────────────┘
               │ Consume from Kafka
               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     PROCESSING LAYER                            │
│   [Log Parser / Transformer]   [Isolation Forest ML Model]      │
│    Parse, enrich, normalize     Anomaly score per log entry      │
└──────────────┬──────────────────────────────────────────────────┘
               │ Bulk index
               ▼
┌─────────────────────────────────────────────────────────────────┐
│                       STORAGE LAYER                             │
│   [Elasticsearch Cluster]      [PostgreSQL (metadata/alerts)]   │
│    Index: logs-YYYY.MM.DD        Tables: alerts, rules          │
└──────────────┬──────────────────────────────────────────────────┘
               │ REST queries
               ▼
┌─────────────────────────────────────────────────────────────────┐
│                       SERVING LAYER                             │
│              [FastAPI REST API — Search, Alerts, Analytics]     │
│     /api/v1/search   /api/v1/alerts   /api/v1/analytics         │
└──────────────┬──────────────────────────────────────────────────┘
               │ HTTP / WebSocket
               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FRONTEND LAYER                             │
│         [React Dashboard — Recharts — Tailwind CSS]             │
│   Search UI | Volume Trends | Severity Charts | Alert Manager   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    CLOUD INFRASTRUCTURE (AWS)                   │
│  ECS Fargate | MSK (Kafka) | OpenSearch | RDS | ECR | ALB      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow (End-to-End)

### Write Path (Ingestion)
1. A log producer (server, app, agent) sends logs via HTTP POST to `/api/v1/ingest`
2. FastAPI validates and publishes raw JSON to **Kafka topic `raw-logs`**
3. A **Kafka Consumer** (background worker) reads messages from `raw-logs`
4. The **Log Parser** normalizes fields: timestamp, level, service, host, message
5. The **Isolation Forest** model scores each log for anomaly probability
6. The enriched log is **bulk-indexed into Elasticsearch** under `logs-YYYY.MM.DD`
7. If anomaly score exceeds threshold → Alert Engine evaluates rules → stores alert

### Read Path (Search & Visualization)
1. React Dashboard sends search query to `/api/v1/search`
2. FastAPI translates query to Elasticsearch DSL
3. Elasticsearch returns paginated results
4. FastAPI returns JSON to frontend
5. Recharts renders the data as charts

---

## Technology Choices & Rationale

| Technology | Role | Why Chosen | Alternative |
|---|---|---|---|
| FastAPI | Backend API | Async, auto-docs, fast | Flask (sync, older), Django (heavy) |
| Kafka | Message queue | High throughput, replay, decoupling | RabbitMQ (lower throughput), Redis Streams |
| Elasticsearch | Log storage | Full-text search, time-series, aggregations | Loki (less query power), ClickHouse |
| Isolation Forest | Anomaly detection | Unsupervised, no labels needed, fast | LSTM Autoencoder (needs more data), LOF |
| React + Recharts | Frontend | Component-based, large ecosystem | Grafana (less customizable) |
| Docker Compose | Local orchestration | Simple multi-service dev setup | Kubernetes (overkill for local) |
| AWS ECS Fargate | Cloud containers | Serverless containers, managed | EC2 (manual management), Lambda (cold start) |
| PostgreSQL | Metadata / alerts | ACID, relational alerts/rules | SQLite (not production-grade) |

---

## Non-Functional Requirements

| Requirement | Target |
|---|---|
| Log ingestion throughput | 10,000 logs/second |
| Search latency (p95) | < 500ms |
| Data retention | 30 days (configurable) |
| Anomaly detection latency | < 2 seconds per batch |
| API availability | 99.9% uptime |
| Dashboard load time | < 2 seconds |
