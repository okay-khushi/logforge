# Development Milestones & Roadmap

## MVP Definition

The **Minimum Viable Product** is a working system where:
- Logs can be ingested via API ✓
- Logs are stored and searchable in Elasticsearch ✓
- A basic React dashboard shows logs and supports search ✓
- Docker Compose runs the full stack locally ✓

## Stretch Goals

- Kafka streaming pipeline
- Isolation Forest anomaly detection
- Alert engine with notifications
- AWS cloud deployment
- Advanced visualizations (heatmaps, correlation views)
- Log agent (like Filebeat) that tails files automatically

---

## Phase Roadmap

| Phase | Name | Duration | Skills Demonstrated |
|---|---|---|---|
| 1 | System Design + Project Setup | 1 day | System Design, Architecture |
| 2 | FastAPI Backend Foundation | 2 days | Backend Engineering, REST APIs |
| 3 | Elasticsearch Integration | 2 days | Big Data, Search Engineering |
| 4 | Kafka Log Streaming Pipeline | 2 days | Distributed Systems, Streaming |
| 5 | Search APIs | 1 day | Backend Engineering, DSL |
| 6 | React Dashboard Setup | 2 days | Frontend, UI/UX |
| 7 | Analytics Visualizations | 2 days | Data Engineering, Charts |
| 8 | Isolation Forest Anomaly Detection | 3 days | Machine Learning |
| 9 | Alert Engine | 2 days | Backend, Event-driven Systems |
| 10 | Dockerization | 1 day | Cloud, DevOps |
| 11 | AWS Deployment | 3 days | Cloud Computing, AWS |
| 12 | Resume Enhancements & Docs | 1 day | System Design Documentation |

**Total Estimated Duration: ~22 working days (~5 weeks)**

---

## Phase Dependency Graph

```
Phase 1 (Setup)
    └── Phase 2 (FastAPI)
            └── Phase 3 (Elasticsearch)
                    └── Phase 4 (Kafka)
                    └── Phase 5 (Search APIs)
                            └── Phase 6 (React)
                                    └── Phase 7 (Visualizations)
                                            └── Phase 8 (ML/Anomaly)
                                                    └── Phase 9 (Alerts)
                                                            └── Phase 10 (Docker)
                                                                    └── Phase 11 (AWS)
                                                                            └── Phase 12 (Docs)
```

---

## Resume Impact Per Phase

| Phase | Resume Bullet |
|---|---|
| 1-3 | Built a scalable log storage and search system using Elasticsearch and FastAPI |
| 4 | Designed a real-time log streaming pipeline processing 10K events/sec with Kafka |
| 5-7 | Created a full-stack analytics dashboard with React, Recharts, and Tailwind CSS |
| 8 | Implemented unsupervised anomaly detection using Isolation Forest on live log streams |
| 9 | Built an event-driven alerting engine with rule evaluation and notification dispatch |
| 10 | Containerized a 6-service application using Docker and Docker Compose |
| 11 | Deployed a production-grade platform on AWS using ECS Fargate, MSK, and OpenSearch |
| 12 | Authored system design documentation for a distributed log analytics platform |
