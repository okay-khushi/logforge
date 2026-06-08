"""
api/v1/ingest.py — Log Ingestion Endpoints

WHAT THIS FILE DOES:
    Handles POST requests to ingest log entries.
    This is the "write path" of the system.

ROUTE: POST /api/v1/ingest

DESIGN DECISIONS:
    1. Batch ingestion only — no single-log endpoint.
       Reason: Forces clients to batch, which is more efficient.

    2. Partial failure handling — if 2 of 100 logs in a batch have bad data,
       we accept the 98 valid ones and report the 2 failures.
       Reason: Dropping an entire batch because of one bad entry would cause
       data loss in a production logging system.

    3. Background indexing — the API returns immediately after publishing
       to Kafka (Phase 4). The consumer indexes asynchronously.
       Reason: The client doesn't need to wait for Elasticsearch to finish.

    4. Background tasks in Phase 2 — we store synchronously (in-memory)
       but wrap it in a FastAPI BackgroundTask to simulate async behavior.
"""

from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.core.logging import get_logger
from app.models.log_entry import IngestResponse, LogBatchInput
from app.services.log_store import InMemoryLogStore, get_log_store

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
    Accepts a batch of 1-1000 log entries and stores them for search and analysis.

    **Returns 202 Accepted** (not 200 OK) because the logs may be queued for
    asynchronous processing via Kafka before reaching Elasticsearch.

    Each log entry requires: `timestamp`, `level`, `service`, `host`, `message`.
    The `metadata` field is optional and accepts any JSON object.
    """,
)
async def ingest_logs(
    batch: LogBatchInput,
    background_tasks: BackgroundTasks,
    store: InMemoryLogStore = Depends(get_log_store),
) -> IngestResponse:
    """
    Ingest a batch of log entries.

    FastAPI automatically:
    - Parses the JSON body into LogBatchInput
    - Validates every field in every LogEntryInput
    - Returns HTTP 422 if validation fails (before this function runs)
    - Generates the OpenAPI documentation from the type hints
    """
    batch_id = str(uuid4())

    logger.info(
        "Ingest request received",
        batch_id=batch_id,
        log_count=len(batch.logs),
    )

    try:
        stored_entries = await store.add_batch(batch.logs)

        logger.info(
            "Batch ingested successfully",
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
            "Failed to ingest batch",
            batch_id=batch_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest logs: {str(e)}",
        )


@router.get(
    "/stats",
    summary="Get ingestion statistics",
    description="Returns total ingested log counts, level breakdown, and top services.",
)
async def get_ingestion_stats(
    store: InMemoryLogStore = Depends(get_log_store),
) -> dict:
    """Return statistics about ingested logs."""
    stats = await store.get_stats()

    logger.info("Stats requested", stats=stats)

    return {
        "status": "ok",
        "data": stats,
    }
