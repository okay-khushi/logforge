"""
api/v1/ingest.py — Log Ingestion Endpoints

Phase 4 Update: Ingestion now publishes to Kafka.
                The consumer reads from Kafka and writes to Elasticsearch.

NEW FLOW:
    POST /api/v1/ingest
        ↓ Validate batch with Pydantic
        ↓ Publish each log to Kafka topic "raw-logs"
        ↓ Return 202 Accepted immediately
        
    (background)
    Kafka Consumer polls "raw-logs"
        ↓ Deserialize + validate
        ↓ Bulk write to Elasticsearch

FALLBACK STRATEGY:
    If Kafka is unavailable (e.g., broker down), the ingest endpoint falls back
    to writing directly to Elasticsearch. This keeps the API resilient — logs
    are never silently dropped even if the streaming pipeline is degraded.

    Priority:
    1. Kafka → Consumer → Elasticsearch  (normal path)
    2. Direct → Elasticsearch            (Kafka unavailable)
    3. HTTP 500                          (both unavailable)

WHY 202 ACCEPTED (not 200 OK)?
    200 OK means "done". But the log isn't in ES yet — it's in Kafka.
    202 Accepted means "I've accepted your request and will process it".
    This is semantically correct for async/queued processing.
"""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.logging import get_logger
from app.models.log_entry import IngestResponse, LogBatchInput
from app.services.kafka_producer import get_producer, produce_log_batch
from app.services.log_store import get_log_store

logger = get_logger(__name__)

router = APIRouter(
    prefix="/ingest",
    tags=["Ingestion"],
)


@router.post(
    "",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a batch of log entries",
    description="""
    Accepts a batch of 1-1000 log entries and queues them for processing.

    **Phase 4 Flow**: Logs are published to a Kafka topic and consumed
    asynchronously by the log consumer service, which writes them to Elasticsearch.

    **Fallback**: If Kafka is unavailable, logs are written directly to Elasticsearch.

    Returns **202 Accepted** because indexing happens asynchronously.
    """,
)
async def ingest_logs(
    batch: LogBatchInput,
    store=Depends(get_log_store),
) -> IngestResponse:
    """
    Ingest a batch of log entries via Kafka (with direct-ES fallback).
    """
    batch_id = str(uuid4())

    logger.info(
        "Ingest request received",
        batch_id=batch_id,
        log_count=len(batch.logs),
    )

    # ── Attempt Kafka path ─────────────────────────────────────────────────────
    producer = get_producer()

    if producer is not None:
        # Serialize each validated log entry to a plain dict for Kafka.
        # We serialize AFTER Pydantic validation — the consumer receives
        # clean, validated data (not raw client JSON).
        log_dicts = [
            {
                "timestamp": log.timestamp.isoformat(),
                "level": log.level.value if hasattr(log.level, "value") else log.level,
                "service": log.service,
                "host": log.host,
                "message": log.message,
                "environment": log.environment,
                "metadata": log.metadata,
            }
            for log in batch.logs
        ]

        try:
            sent = await produce_log_batch(log_dicts, batch_id)

            if sent == len(batch.logs):
                # All messages published to Kafka successfully
                logger.info(
                    "Batch published to Kafka",
                    batch_id=batch_id,
                    count=sent,
                    path="kafka",
                )
                return IngestResponse(
                    status="accepted",
                    ingested=sent,
                    batch_id=batch_id,
                )
            elif sent > 0:
                # Partial Kafka publish — fall through to write remainder directly
                logger.warning(
                    "Partial Kafka publish — falling back to direct ES for remainder",
                    batch_id=batch_id,
                    kafka_sent=sent,
                    total=len(batch.logs),
                )
        except Exception as e:
            logger.warning(
                "Kafka publish failed — falling back to direct ES write",
                batch_id=batch_id,
                error=str(e),
            )

    # ── Fallback: Direct Elasticsearch write ───────────────────────────────────
    # Reached when: Kafka is down, producer not initialized, or partial failure.
    logger.info(
        "Writing batch directly to Elasticsearch (Kafka unavailable)",
        batch_id=batch_id,
        count=len(batch.logs),
        path="direct_es",
    )

    try:
        stored_entries = await store.add_batch(batch.logs)

        logger.info(
            "Batch written directly to Elasticsearch",
            batch_id=batch_id,
            ingested=len(stored_entries),
        )

        return IngestResponse(
            status="accepted",
            ingested=len(stored_entries),
            batch_id=batch_id,
        )

    except Exception as e:
        logger.error(
            "Both Kafka and Elasticsearch are unavailable",
            batch_id=batch_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest logs: storage unavailable. Error: {str(e)}",
        )


@router.get(
    "/stats",
    summary="Get ingestion statistics",
    description="Returns total log counts, level breakdown, and top services from Elasticsearch.",
)
async def get_ingestion_stats(
    store=Depends(get_log_store),
) -> dict:
    """Return statistics about all ingested logs."""
    stats = await store.get_stats()

    logger.info("Stats requested", stats=stats)

    return {
        "status": "ok",
        "data": stats,
    }
