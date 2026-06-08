#!/usr/bin/env python3
"""
seed_logs.py — Generate and ingest synthetic log data for testing.

This script creates realistic-looking log entries and sends them
to the LogForge API. Use this to populate Elasticsearch with data
before building dashboards.

Usage:
    python scripts/seed_logs.py
    python scripts/seed_logs.py --count 5000 --api http://localhost:8000
"""

import argparse
import random
import json
import time
from datetime import datetime, timedelta, timezone

# ─── Configuration ────────────────────────────────────────────────────────────

SERVICES = [
    "auth-service",
    "api-gateway",
    "payment-service",
    "user-service",
    "notification-service",
    "inventory-service",
    "order-service",
    "search-service",
]

LEVELS = ["DEBUG", "INFO", "INFO", "INFO", "WARN", "ERROR", "CRITICAL"]

HOSTS = [
    "prod-server-01",
    "prod-server-02",
    "prod-server-03",
    "prod-server-04",
    "worker-01",
    "worker-02",
]

LOG_MESSAGES = {
    "DEBUG": [
        "Cache hit for key: user_{id}",
        "Processing request: GET /api/v1/users/{id}",
        "Database query executed in {ms}ms",
        "Session token refreshed for user {id}",
    ],
    "INFO": [
        "User {id} logged in successfully",
        "Payment of ${amount} processed for order {order_id}",
        "Email notification sent to user {id}",
        "Order {order_id} status updated to 'shipped'",
        "Health check passed",
        "Service started on port {port}",
        "Connected to database successfully",
        "Cache warmed with {count} entries",
    ],
    "WARN": [
        "High memory usage detected: {pct}%",
        "Slow database query: {ms}ms for user_{id}",
        "Rate limit approaching for IP {ip}",
        "Retry attempt {n}/3 for payment gateway",
        "Deprecated API endpoint called: /api/v0/users",
    ],
    "ERROR": [
        "Failed to connect to database after 3 retries",
        "Payment gateway timeout for order {order_id}",
        "Invalid authentication token for user {id}",
        "File upload failed: disk quota exceeded",
        "External API call failed: status 503",
        "Queue consumer dead-lettered message",
    ],
    "CRITICAL": [
        "Database connection pool exhausted!",
        "Service {service} is DOWN — circuit breaker OPEN",
        "Disk usage at 99% on {host}",
        "Memory leak detected — OOM kill imminent",
    ],
}


def generate_log_entry(base_time: datetime, offset_seconds: int) -> dict:
    """Generate a single realistic log entry."""
    level = random.choices(
        LEVELS, weights=[5, 50, 20, 10, 10, 4, 1], k=1
    )[0]

    service = random.choice(SERVICES)
    host = random.choice(HOSTS)
    template = random.choice(LOG_MESSAGES[level])

    # Fill in template placeholders
    message = template.format(
        id=random.randint(1000, 9999),
        ms=random.randint(1, 5000),
        amount=round(random.uniform(10, 500), 2),
        order_id=f"ORD-{random.randint(10000, 99999)}",
        pct=random.randint(80, 99),
        ip=f"192.168.{random.randint(1,254)}.{random.randint(1,254)}",
        n=random.randint(1, 3),
        port=random.choice([8000, 8080, 3000, 5000]),
        count=random.randint(100, 10000),
        host=host,
        service=service,
    )

    timestamp = base_time + timedelta(seconds=offset_seconds)

    return {
        "timestamp": timestamp.isoformat(),
        "level": level,
        "service": service,
        "host": host,
        "message": message,
        "metadata": {
            "thread_id": f"thread-{random.randint(1, 32)}",
            "request_id": f"req-{random.randint(100000, 999999)}",
            "environment": "production",
        },
    }


def seed_logs(api_url: str, count: int, batch_size: int = 100) -> None:
    """Send log entries to the LogForge API in batches."""
    try:
        import urllib.request
    except ImportError:
        print("ERROR: urllib not available")
        return

    base_time = datetime.now(timezone.utc) - timedelta(hours=24)
    total_sent = 0
    batches = (count + batch_size - 1) // batch_size

    print(f"→ Seeding {count} log entries in {batches} batches of {batch_size}...")

    for batch_num in range(batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, count)
        batch_logs = []

        for i in range(start_idx, end_idx):
            offset = int((i / count) * 86400)  # spread across 24 hours
            log = generate_log_entry(base_time, offset)
            batch_logs.append(log)

        payload = json.dumps({"logs": batch_logs}).encode("utf-8")
        req = urllib.request.Request(
            f"{api_url}/api/v1/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                total_sent += len(batch_logs)
                print(f"  Batch {batch_num + 1}/{batches}: {len(batch_logs)} logs ingested")
        except Exception as e:
            print(f"  ERROR in batch {batch_num + 1}: {e}")

        time.sleep(0.05)  # slight throttle

    print(f"\n✓ Done! Total logs seeded: {total_sent}")
    print(f"  Check your dashboard at: http://localhost:3000")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed LogForge with test data")
    parser.add_argument(
        "--count", type=int, default=1000, help="Number of logs to generate (default: 1000)"
    )
    parser.add_argument(
        "--api", type=str, default="http://localhost:8000", help="API base URL"
    )
    parser.add_argument(
        "--batch-size", type=int, default=100, help="Batch size (default: 100)"
    )
    args = parser.parse_args()

    seed_logs(api_url=args.api, count=args.count, batch_size=args.batch_size)
