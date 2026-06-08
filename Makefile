# ─────────────────────────────────────────────────────────────────────────────
# LogForge Makefile — Developer Commands
# Usage: make <target>
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: help setup up down logs seed test lint format clean

# Default target
help:
	@echo ""
	@echo "LogForge — Available Commands"
	@echo "─────────────────────────────"
	@echo "  make setup     — Copy .env.example to .env"
	@echo "  make up        — Start all services with Docker Compose"
	@echo "  make down      — Stop all services"
	@echo "  make logs      — Follow logs from all services"
	@echo "  make seed      — Seed Elasticsearch with sample log data"
	@echo "  make test      — Run backend tests"
	@echo "  make lint      — Lint Python code (ruff)"
	@echo "  make format    — Format Python code (black)"
	@echo "  make clean     — Remove containers and volumes"
	@echo ""

setup:
	@echo "→ Setting up environment..."
	@cp -n .env.example .env || true
	@echo "✓ .env file ready. Edit it before running 'make up'"

up:
	@echo "→ Starting LogForge services..."
	docker-compose -f infrastructure/docker-compose.yml up -d
	@echo "✓ Services started"
	@echo "  API:           http://localhost:8000"
	@echo "  API Docs:      http://localhost:8000/docs"
	@echo "  Dashboard:     http://localhost:3000"
	@echo "  Elasticsearch: http://localhost:9200"
	@echo "  Kibana:        http://localhost:5601"

down:
	docker-compose -f infrastructure/docker-compose.yml down

logs:
	docker-compose -f infrastructure/docker-compose.yml logs -f

seed:
	@echo "→ Seeding test log data..."
	python scripts/seed_logs.py
	@echo "✓ Sample logs ingested"

test:
	@echo "→ Running tests..."
	cd backend && python -m pytest tests/ -v --tb=short

lint:
	cd backend && ruff check app/

format:
	cd backend && black app/

clean:
	@echo "→ Cleaning up containers and volumes..."
	docker-compose -f infrastructure/docker-compose.yml down -v --remove-orphans
	@echo "✓ Cleaned"
