"""
core/es_index.py — Elasticsearch Index Template and Mapping Definitions

WHY EXPLICIT MAPPINGS MATTER:
    By default, Elasticsearch uses "dynamic mapping" — it guesses field types
    from the first document it sees. This causes production disasters:

    If your first log has anomaly_score=0 (integer), ES maps it as `long`.
    Later logs have anomaly_score=0.87 (float) → indexing error!

    Explicit mappings prevent this by telling ES exactly what each field is.

DAILY INDEX ROLLOVER:
    We create a new index every day: logs-2024.01.15, logs-2024.01.16, etc.

    WHY NOT ONE BIG INDEX?
    - Deleting old logs: drop the whole index (instant) vs deleting docs (slow)
    - Query performance: searching logs-2024.01.* only hits relevant shards
    - Index lifecycle management: ES can auto-delete indexes older than N days

INDEX TEMPLATE:
    Instead of creating mappings for each daily index, we register a template
    that auto-applies to any new index matching the pattern logs-*.
    This means the first document of each day auto-creates a correctly-mapped index.

LOG SCHEMA FIELD EXPLANATIONS:
    ┌─────────────────┬──────────────┬───────────────────────────────────────────────────┐
    │ Field           │ ES Type      │ Purpose                                           │
    ├─────────────────┼──────────────┼───────────────────────────────────────────────────┤
    │ id              │ keyword      │ UUID — unique doc ID for deduplication            │
    │ timestamp       │ date         │ When the event occurred (ISO 8601, UTC)           │
    │ level           │ keyword      │ Severity: DEBUG|INFO|WARN|ERROR|CRITICAL          │
    │ service         │ keyword+text │ Producing service name (filterable + searchable)  │
    │ host            │ keyword      │ Server/container hostname — infra correlation      │
    │ message         │ text         │ The log line — full-text search primary target    │
    │ environment     │ keyword      │ prod|staging|dev — prevents cross-env noise       │
    │ metadata        │ object       │ Arbitrary key-value pairs (flexible schema)       │
    │ anomaly_score   │ float        │ Isolation Forest score 0.0–1.0 (Phase 8)         │
    │ is_anomaly      │ boolean      │ True when anomaly_score > threshold               │
    │ indexed_at      │ date         │ When LogForge received the document               │
    └─────────────────┴──────────────┴───────────────────────────────────────────────────┘
"""

from typing import Any

# ── Index Analysis Settings ───────────────────────────────────────────────────
# Normalizers are like analyzers but for keyword fields.
# They apply character filters (like lowercasing) at index time.
# This lets us do case-insensitive exact matches on keyword fields.
INDEX_ANALYSIS: dict[str, Any] = {
    "normalizer": {
        "lowercase": {
            "type": "custom",
            "char_filter": [],
            "filter": ["lowercase"],  # lowercase everything stored as keyword
        }
    }
}

# ── Index Settings ─────────────────────────────────────────────────────────────
INDEX_SETTINGS: dict[str, Any] = {
    "number_of_shards": 1,       # 1 shard for local dev (use 3+ in production)
    "number_of_replicas": 0,     # 0 replicas for local dev (use 1+ in production)
    "refresh_interval": "5s",    # How often new docs become searchable
                                  # (lower = faster search visibility, higher = better write perf)
    "max_result_window": 50_000, # Max results from a single search (default: 10,000)
    "analysis": INDEX_ANALYSIS,  # Register the lowercase normalizer
}

# ── Field Mappings ─────────────────────────────────────────────────────────────
INDEX_MAPPINGS: dict[str, Any] = {
    "dynamic": "strict",  # Reject documents with unmapped fields (strict data integrity)
    "properties": {

        # ── id ──────────────────────────────────────────────────────────────────
        # UUID generated at ingest time. Used as the Elasticsearch _id so we get
        # idempotent indexing — re-sending the same log won't create a duplicate.
        # Type: keyword (exact match only, never tokenized for full-text search)
        "id": {
            "type": "keyword",
        },

        # ── timestamp ───────────────────────────────────────────────────────────
        # When the log EVENT occurred — NOT when we indexed it.
        # Stored as ISO 8601 UTC. All queries sort/filter on this field.
        # Type: date — enables range queries (gte/lte) and time aggregations.
        # Format: strict_date_optional_time accepts:
        #   - "2024-01-15T10:30:00Z"
        #   - "2024-01-15T10:30:00+05:30"
        #   - "2024-01-15"  (midnight UTC assumed)
        "timestamp": {
            "type": "date",
            "format": "strict_date_optional_time",
        },

        # ── level ────────────────────────────────────────────────────────────────
        # Log severity — must be one of: DEBUG, INFO, WARN, ERROR, CRITICAL
        # Type: keyword — exact match for term filters, supports aggregations.
        # Why not text? We never need to search for "ERR" and match "ERROR" —
        # we need exact values for GROUP BY and dashboard pie charts.
        # The .lowercase sub-field allows case-insensitive matching:
        #   term: {level.lowercase: "error"} matches "ERROR"
        "level": {
            "type": "keyword",
            "fields": {
                "lowercase": {"type": "keyword", "normalizer": "lowercase"}
            },
        },

        # ── service ──────────────────────────────────────────────────────────────
        # Name of the microservice that produced the log (e.g., "auth-service").
        # Type: keyword — for exact filtering and aggregations (top 10 services).
        # Also indexed as .text for fuzzy search ("auth" matches "auth-service").
        "service": {
            "type": "keyword",
            "fields": {
                "text": {"type": "text"}
            },
        },

        # ── host ─────────────────────────────────────────────────────────────────
        # Hostname or IP address of the server/container that generated the log.
        # Used for infrastructure correlation — "which host had most errors?".
        # Type: keyword — exact match + aggregations only.
        "host": {
            "type": "keyword",
        },

        # ── message ──────────────────────────────────────────────────────────────
        # The actual log message — the most important searchable field.
        # Type: text — Elasticsearch tokenizes this for full-text search.
        # The standard analyzer: lowercases, splits on whitespace/punctuation.
        # Example: "Failed to connect to DB" → tokens: [failed, connect, db]
        # A user searching "connect" or "DB" or "failed db" will find this log.
        # The .keyword sub-field stores the raw value for exact phrase matching
        # and prefix/wildcard queries (limited to 512 chars to save space).
        "message": {
            "type": "text",
            "analyzer": "standard",
            "fields": {
                "keyword": {
                    "type": "keyword",
                    "ignore_above": 512,
                }
            },
        },

        # ── environment ──────────────────────────────────────────────────────────
        # Deployment environment: production | staging | development
        # This is critical in a real system — you never want to mix production
        # error counts with staging noise in your dashboards.
        # Type: keyword — exact match for filtering and aggregations.
        # Queries: filter: {term: {environment: "production"}}
        "environment": {
            "type": "keyword",
            "fields": {
                "lowercase": {"type": "keyword", "normalizer": "lowercase"}
            },
        },

        # ── anomaly_score ────────────────────────────────────────────────────────
        # Machine learning anomaly score from Isolation Forest (added in Phase 8).
        # Range: 0.0 (completely normal) → 1.0 (highly anomalous).
        # Type: float — enables range queries: {range: {anomaly_score: {gte: 0.7}}}
        # Defaulted to 0.0 at ingest; updated asynchronously by the ML pipeline.
        "anomaly_score": {
            "type": "float",
        },

        # ── is_anomaly ───────────────────────────────────────────────────────────
        # Boolean flag set when anomaly_score exceeds the configured threshold.
        # Type: boolean — fast binary filter for alert dashboard queries.
        # Query: {term: {is_anomaly: true}} → returns all anomalous logs.
        "is_anomaly": {
            "type": "boolean",
        },

        # ── metadata ─────────────────────────────────────────────────────────────
        # Flexible key-value object for service-specific context.
        # Examples: {"user_id": "u123"}, {"order_id": "ORD-456"}, {"trace_id": "..."}
        # Type: object with dynamic: true — any fields are accepted and indexed.
        # Unlike the parent document (strict), metadata allows arbitrary keys.
        # This gives you the flexibility of schema-less storage for one field.
        "metadata": {
            "type": "object",
            "dynamic": True,
            "enabled": True,
        },

        # ── indexed_at ───────────────────────────────────────────────────────────
        # When LogForge received and indexed this document.
        # May differ from `timestamp` for late-arriving logs.
        # Useful for diagnosing pipeline lag: indexed_at - timestamp = latency.
        "indexed_at": {
            "type": "date",
            "format": "strict_date_optional_time",
        },
    },
}

# ── Index Template Name ────────────────────────────────────────────────────────
INDEX_TEMPLATE_NAME = "logforge-logs-template"

# ── Index Template Body ────────────────────────────────────────────────────────
# This template auto-applies to any index matching "logs-*"
# When the first log of a new day arrives, ES auto-creates logs-YYYY.MM.DD
# with exactly these settings and mappings — no manual index creation needed.
INDEX_TEMPLATE_BODY: dict[str, Any] = {
    "index_patterns": ["logs-*"],   # Apply to any index starting with logs-
    "template": {
        "settings": INDEX_SETTINGS,
        "mappings": INDEX_MAPPINGS,
    },
    "priority": 200,  # 200 > built-in ES 8.x 'logs' template (priority 100) — avoids conflict
}
