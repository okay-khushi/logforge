# LogForge — Enterprise Log Analytics Platform

A simplified Splunk-like platform built with FastAPI, Elasticsearch, Kafka, React, and AWS.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend API | Python FastAPI |
| Log Storage | Elasticsearch |
| Message Streaming | Apache Kafka |
| ML / Anomaly Detection | scikit-learn Isolation Forest |
| Frontend | React + Tailwind CSS + Recharts |
| Containerization | Docker + Docker Compose |
| Cloud | AWS (ECS, MSK, OpenSearch, RDS) |

## Project Structure

```
logforge/
├── backend/                  # FastAPI backend service
│   ├── app/
│   │   ├── api/              # Route handlers
│   │   ├── core/             # Config, settings
│   │   ├── models/           # Pydantic schemas
│   │   ├── services/         # Business logic
│   │   └── main.py           # FastAPI app entry point
│   ├── Dockerfile
│   └── requirements.txt
│
├── frontend/                 # React dashboard
│   ├── src/
│   │   ├── components/       # Reusable UI components
│   │   ├── pages/            # Dashboard pages
│   │   ├── services/         # API calls
│   │   └── App.jsx
│   ├── Dockerfile
│   └── package.json
│
├── kafka/                    # Kafka consumer workers
│   ├── consumer.py
│   └── Dockerfile
│
├── ml/                       # Anomaly detection model
│   ├── train.py
│   ├── predict.py
│   └── models/               # Saved model artifacts
│
├── infrastructure/           # Cloud and deployment configs
│   ├── docker-compose.yml    # Local development
│   ├── docker-compose.prod.yml
│   └── aws/                  # AWS CloudFormation / Terraform
│
├── scripts/                  # Utility scripts
│   ├── seed_logs.py          # Generate fake logs for testing
│   └── health_check.sh
│
├── docs/                     # Documentation
│   ├── architecture.md
│   ├── api-design.md
│   ├── er-diagram.md
│   ├── milestones.md
│   └── problem-statement.md
│
├── tests/                    # Test suites
│   ├── backend/
│   └── integration/
│
├── .env.example              # Environment variable template
├── .gitignore
├── Makefile                  # Developer commands
└── README.md
```

## Quick Start (Local)

```bash
# 1. Clone the repo
git clone https://github.com/yourname/logforge.git
cd logforge

# 2. Copy env file
cp .env.example .env

# 3. Start all services
docker-compose -f infrastructure/docker-compose.yml up -d

# 4. Seed test data
python scripts/seed_logs.py

# 5. Open dashboard
open http://localhost:3000
```

## API Documentation

Interactive docs available at: http://localhost:8000/docs

## Phases

See [docs/milestones.md](docs/milestones.md) for the full development roadmap.
