"""
services/kafka_consumer.py — Async Kafka Consumer

WHY A SEPARATE CONSUMER PROCESS?
    The API (producer) and the consumer are intentionally decoupled.
    The API's job is to accept HTTP requests as fast as possible.
    The consumer's job is to read from Kafka and write to Elasticsearch.

    If Elasticsearch is slow (or temporarily down), the API is not affected.
    Logs buffer in Kafka until ES recovers — no data loss, no slowdown.

    This is the key value proposition of the Kafka architecture:
    ┌─────────┐     ┌───────┐     ┌────────────────┐
    │  API    │────▶│ Kafka │────▶│    Consumer    │
    │ (fast)  │     │(buffer│     │ (ES write path)│
    └─────────┘     └───────┘     └────────────────┘
         ↑                              │
    Returns 202                   Indexes to ES
    immediately                   asynchronously

HOW THE CONSUMER RUNS:
    We run the consumer as a background asyncio task inside the same FastAPI
    process. This is fine for development and moderate production loads.

    For high-throughput production (10M+ logs/day):
    - Run the consumer as a SEPARATE process/service in Docker Compose
    - Scale horizontally: 3 consumer instances × 3 Kafka partitions = 3x throughput
    - Each partition is consumed by exactly one consumer in a group

CONSUMER GROUP CONCEPT:
    All consumers sharing the same group_id cooperate to consume a topic.
    Kafka assigns each partition to exactly one consumer in the group.

    Topic: raw-logs (3 partitions)
    Consumer Group: logforge-consumers (3 instances)

    Partition 0 → Consumer Instance A
    Partition 1 → Consumer Instance B
    Partition 2 → Consumer Instance C

    → 3x parallelism. Adding more consumers beyond partition count has no effect.

OFFSET MANAGEMENT:
    An offset is the position of the last consumed message in a partition.
    The consumer commits its offset after successfully processing a message.
    If the consumer crashes, it restarts from the last committed offset —
    no messages are lost (they may be reprocessed, so make indexing idempotent).

    auto_offset_reset="earliest": On first start, read from the beginning.
    auto_offset_reset="latest":   On first start, skip historical messages.
"""

import asyncio
import json
from typing import Optional

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError

from app.core.config import settings
from app.core.logging import get_logger
from app.models.log_entry import LogEntryInput

logger = get_logger(__name__)

# ── Consumer State ─────────────────────────────────────────────────────────────
_consumer_task: Optional[asyncio.Task] = None
_consumer_running: bool = False


def _deserialize_message(raw: bytes) -> Optional[dict]:
    """
    Deserialize raw bytes from Kafka into a Python dict.
    Returns None if the message is malformed — we log and skip it.

    WHY GRACEFUL DESERIALIZATION FAILURE?
        A bad message in the topic must never crash the consumer.
        If it did, the consumer would restart, hit the same bad message,
        restart again — infinite crash loop = no logs ever processed.
        Skip bad messages, log them, and keep consuming.
    """
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(
            "Failed to deserialize Kafka message",
            error=str(e),
            raw_preview=raw[:100] if raw else b"",
        )
        return None


async def _process_message(raw_message: bytes, offset: int, partition: int, store) -> bool:
    """
    Process a single Kafka message: deserialize → validate → store in ES.

    Returns True if the message was processed successfully, False otherwise.
    The caller decides whether to commit the offset based on this.

    IDEMPOTENCY:
        We use the log's `id` field as the Elasticsearch document ID.
        Re-processing the same message (on consumer restart) will overwrite
        the existing document instead of creating a duplicate.
        This is idempotent — safe to reprocess.
    """
    data = _deserialize_message(raw_message)
    if data is None:
        # Poison pill — can't parse, skip it
        return False

    try:
        # Validate with Pydantic — same validation as the API layer
        log_entry = LogEntryInput(**data)
    except Exception as e:
        logger.warning(
            "Invalid log schema in Kafka message — skipping",
            error=str(e),
            offset=offset,
            partition=partition,
        )
        return False

    try:
        await store.add_batch([log_entry])
        return True
    except Exception as e:
        logger.error(
            "Failed to write log to Elasticsearch",
            error=str(e),
            offset=offset,
            partition=partition,
        )
        return False


async def _consume_loop(store) -> None:
    """
    Main consumer loop. Runs indefinitely until _consumer_running is set False.

    BATCH PROCESSING STRATEGY:
        Instead of processing one message at a time (N round-trips to ES),
        we accumulate a batch of messages from Kafka and write them to ES
        with a single Bulk API call. This is 10-50x more efficient.

        getmany() fetches up to max_records messages in a single poll,
        waiting up to timeout_ms for messages to arrive.
    """
    global _consumer_running

    consumer = AIOKafkaConsumer(
        settings.kafka_topic_raw_logs,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group,

        # ── Offset Management ──────────────────────────────────────────────────
        # earliest: Read all messages from the beginning
        # latest:   Start from now — skip historical messages
        auto_offset_reset="earliest",

        # enable_auto_commit=False: commit manually after successful ES write
        # This gives us "at-least-once" delivery guarantee.
        enable_auto_commit=False,

        # session_timeout_ms: If the consumer doesn't heartbeat in this time,
        # the broker considers it dead and reassigns its partitions.
        # Must be between group.min.session.timeout.ms (6000) and
        # group.max.session.timeout.ms (1800000) on the broker.
        session_timeout_ms=10_000,

        # heartbeat_interval_ms: How often to send heartbeats.
        # Should be < session_timeout_ms / 3.
        heartbeat_interval_ms=3_000,

        # fetch_max_wait_ms: Max time to wait for messages.
        fetch_max_wait_ms=500,

        # request_timeout_ms: Total request timeout.
        request_timeout_ms=40_000,
    )

    try:
        await consumer.start()
        logger.info(
            "Kafka consumer started",
            topic=settings.kafka_topic_raw_logs,
            group_id=settings.kafka_consumer_group,
            bootstrap_servers=settings.kafka_bootstrap_servers,
        )
    except KafkaConnectionError as e:
        logger.error(
            "Kafka consumer failed to connect",
            error=str(e),
            bootstrap_servers=settings.kafka_bootstrap_servers,
        )
        _consumer_running = False
        return

    try:
        while _consumer_running:
            # ── Fetch a batch of messages ──────────────────────────────────────
            # getmany() returns a dict: {TopicPartition → [messages]}
            # It blocks for up to timeout_ms waiting for messages.
            try:
                batch = await consumer.getmany(
                    timeout_ms=1_000,   # 1 second poll timeout
                    max_records=100,    # At most 100 messages per batch
                )
            except Exception as e:
                logger.error("Consumer poll failed", error=str(e))
                await asyncio.sleep(1)  # Brief pause before retry
                continue

            if not batch:
                # No messages available — loop back and poll again
                continue

            # ── Process each partition's messages ──────────────────────────────
            all_log_entries: list[LogEntryInput] = []
            messages_processed = 0

            for tp, messages in batch.items():
                for msg in messages:
                    data = _deserialize_message(msg.value)
                    if data is None:
                        continue

                    try:
                        log_entry = LogEntryInput(**data)
                        all_log_entries.append(log_entry)
                        messages_processed += 1
                    except Exception as e:
                        logger.warning(
                            "Skipping invalid log message",
                            error=str(e),
                            partition=tp.partition,
                            offset=msg.offset,
                        )

            # ── Bulk write valid logs to Elasticsearch ─────────────────────────
            if all_log_entries:
                try:
                    await store.add_batch(all_log_entries)
                    logger.info(
                        "Consumer batch written to Elasticsearch",
                        count=len(all_log_entries),
                        partitions=list({tp.partition for tp in batch}),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to write batch to Elasticsearch — messages will be reprocessed",
                        error=str(e),
                        count=len(all_log_entries),
                    )
                    # Do NOT commit offsets — reprocess this batch on next poll
                    continue

            # ── Commit offsets only after successful ES write ──────────────────
            # This is "at-least-once" delivery:
            # - If ES write succeeds: commit offset → message processed exactly once
            # - If ES write fails: don't commit → consumer retries the same batch
            # - If consumer crashes after ES write but before commit: message reprocessed
            #   (idempotent ES indexing handles this correctly)
            try:
                await consumer.commit()
            except Exception as e:
                logger.error("Failed to commit Kafka offsets", error=str(e))

    except asyncio.CancelledError:
        logger.info("Kafka consumer task cancelled — shutting down")
    except Exception as e:
        logger.error("Unexpected consumer error", error=str(e))
    finally:
        try:
            await consumer.stop()
            logger.info("Kafka consumer stopped")
        except Exception as e:
            logger.error("Error stopping consumer", error=str(e))


async def start_consumer(store) -> None:
    """
    Start the Kafka consumer as a background asyncio Task.

    WHY asyncio.create_task() instead of await?
        await would block the startup forever (the consumer loop runs indefinitely).
        create_task() schedules the loop to run concurrently with the FastAPI server.
        The task runs in the same event loop — no threads, no GIL issues.
    """
    global _consumer_task, _consumer_running

    if _consumer_task is not None and not _consumer_task.done():
        logger.warning("Kafka consumer already running — skipping")
        return

    _consumer_running = True
    _consumer_task = asyncio.create_task(
        _consume_loop(store),
        name="kafka-consumer-raw-logs",
    )

    logger.info(
        "Kafka consumer task created",
        topic=settings.kafka_topic_raw_logs,
        group_id=settings.kafka_consumer_group,
    )


async def stop_consumer() -> None:
    """
    Signal the consumer loop to stop and wait for it to finish.
    Called from the FastAPI lifespan shutdown hook.
    """
    global _consumer_task, _consumer_running

    _consumer_running = False  # Signal the loop to exit on next iteration

    if _consumer_task is not None and not _consumer_task.done():
        _consumer_task.cancel()
        try:
            await asyncio.wait_for(_consumer_task, timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("Consumer task did not stop cleanly within timeout")
        finally:
            _consumer_task = None

    logger.info("Kafka consumer shutdown complete")


def is_consumer_running() -> bool:
    """Return True if the consumer background task is active."""
    return (
        _consumer_task is not None
        and not _consumer_task.done()
        and _consumer_running
    )
