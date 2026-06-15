"""
services/kafka_producer.py — Async Kafka Producer (Singleton)

WHY A SINGLETON PRODUCER?
    Creating a Kafka producer per-request is catastrophic for performance.
    Each producer opens TCP connections to every broker, negotiates metadata,
    and allocates internal send buffers. That takes ~100-500ms.

    A singleton opens ONE connection pool at startup and reuses it for every
    request. Sending a message then takes ~1ms.

WHY AIOKAFKA (not kafka-python)?
    FastAPI is fully async. kafka-python uses blocking I/O — calling it inside
    an async route would block the event loop, stalling ALL concurrent requests.
    aiokafka is built on asyncio and never blocks.

PRODUCER INTERNALS:
    When you call producer.send(), the message goes to an internal in-memory
    buffer (not directly to the broker). A background thread batches messages
    and flushes to the broker every `linger_ms` milliseconds.

    This is why Kafka producers are so fast — the API call returns in ~0.1ms
    even though the network round-trip takes 10ms.

    Buffer → Batch → Broker → Ack

DELIVERY GUARANTEES:
    acks=1 (default): Leader broker writes the message and acknowledges.
                      If the leader crashes before replicating, message is lost.
    acks=all:         All in-sync replicas must acknowledge. Slower but durable.
                      Use in production for critical logs.
    acks=0:           Fire-and-forget. Fastest, but no durability guarantee.

    We use acks=1 for development. Change to acks="all" in production.
"""

import json
from typing import Optional

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError, KafkaTimeoutError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Module-level singleton ─────────────────────────────────────────────────────
_producer: Optional[AIOKafkaProducer] = None


def _json_serializer(value: dict) -> bytes:
    """
    Serialize a Python dict to UTF-8 encoded JSON bytes.

    Kafka messages are raw bytes — the protocol doesn't know about JSON.
    Both producer and consumer must agree on the serialization format.
    JSON is human-readable and easy to debug in Kafka UI.

    Alternative: Use Apache Avro or Protobuf for schema enforcement and
    smaller message sizes in high-throughput production systems.
    """
    return json.dumps(value, default=str).encode("utf-8")


async def start_producer() -> None:
    """
    Initialize and start the Kafka producer.
    Called once from the FastAPI lifespan startup hook.

    WHY start() is required:
        AIOKafkaProducer is not ready after __init__().
        start() connects to brokers, fetches cluster metadata (which topics
        exist, how many partitions, which broker is the leader for each).
        Without this, the first send() would fail.
    """
    global _producer

    if _producer is not None:
        logger.warning("Kafka producer already initialized — skipping")
        return

    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        # ── Serialization ──────────────────────────────────────────────────────────────────
        value_serializer=_json_serializer,
        key_serializer=lambda k: k.encode("utf-8") if k else None,

        # ── Batching & Throughput ────────────────────────────────────────────────────────
        # linger_ms: How long to wait for more messages before flushing a batch.
        linger_ms=10,

        # max_batch_size: Maximum bytes in a single batch sent to a partition.
        max_batch_size=16_384,

        # compression_type: Compress batches. "gzip" reduces size by 60-80%.
        compression_type="gzip",

        # ── Reliability ──────────────────────────────────────────────────────────────────
        # acks=1: Leader acknowledges. Use acks="all" in production.
        acks=1,

        # max_request_size: Hard limit on a single request.
        max_request_size=1_048_576,  # 1 MB

        # request_timeout_ms: How long to wait for broker response.
        request_timeout_ms=30_000,

        # retry_backoff_ms: Wait between retries on transient errors.
        retry_backoff_ms=100,

        # Prevents duplicate messages on retries. Requires acks="all".
        # Disabled here because acks=1; enable in production with acks="all".
        enable_idempotence=False,
    )

    try:
        await _producer.start()
        logger.info(
            "Kafka producer started",
            bootstrap_servers=settings.kafka_bootstrap_servers,
        )
    except KafkaConnectionError as e:
        logger.error(
            "Failed to connect to Kafka broker",
            error=str(e),
            bootstrap_servers=settings.kafka_bootstrap_servers,
            hint="Is Kafka running? Check: docker-compose ps",
        )
        _producer = None
        raise


async def stop_producer() -> None:
    """
    Flush pending messages and close the producer gracefully.
    Called from the FastAPI lifespan shutdown hook.

    WHY FLUSH ON SHUTDOWN?
        Messages in the internal buffer are NOT sent yet.
        If you kill the process without flushing, those messages are lost.
        flush() blocks until all buffered messages are delivered (or timeout).
    """
    global _producer

    if _producer is None:
        return

    try:
        await _producer.flush()       # Drain the send buffer
        await _producer.stop()        # Close TCP connections
        logger.info("Kafka producer stopped gracefully")
    except Exception as e:
        logger.error("Error stopping Kafka producer", error=str(e))
    finally:
        _producer = None


def get_producer() -> Optional[AIOKafkaProducer]:
    """Return the singleton producer instance (may be None if Kafka is down)."""
    return _producer


async def produce_log_batch(logs: list[dict], batch_id: str) -> int:
    """
    Publish a batch of log dictionaries to the raw-logs Kafka topic.

    Each log is published as a separate Kafka message.
    The batch_id is used as the message key — this ensures all logs from
    the same API request land in the same partition (ordering guarantee).

    Returns the number of messages successfully sent.

    KEY → PARTITION MAPPING:
        Kafka uses hash(key) % num_partitions to select a partition.
        Using batch_id as key means all logs in a batch go to the same
        partition → consumed in order by a single consumer thread.

        If key=None, messages are round-robin distributed across partitions
        (higher parallelism but no ordering guarantee within a batch).
    """
    producer = get_producer()

    if producer is None:
        logger.warning(
            "Kafka producer not available — logs will not be streamed",
            batch_id=batch_id,
            hint="Falling back to direct Elasticsearch write",
        )
        return 0

    sent_count = 0
    topic = settings.kafka_topic_raw_logs

    for log in logs:
        try:
            # send() is non-blocking — it enqueues to the internal buffer.
            # The actual network send happens in the background.
            # await send_and_wait() would block until broker acknowledges —
            # we don't do that here to keep the API fast.
            await producer.send(
                topic=topic,
                value=log,
                key=batch_id,           # Route all batch messages to same partition
                headers=[               # Optional metadata headers (not in message body)
                    ("source", b"logforge-api"),
                    ("batch_id", batch_id.encode()),
                ],
            )
            sent_count += 1
        except KafkaTimeoutError as e:
            logger.error(
                "Kafka send timeout",
                topic=topic,
                batch_id=batch_id,
                error=str(e),
            )
        except Exception as e:
            logger.error(
                "Failed to send log to Kafka",
                topic=topic,
                batch_id=batch_id,
                error=str(e),
            )

    if sent_count > 0:
        logger.info(
            "Logs published to Kafka",
            topic=topic,
            count=sent_count,
            batch_id=batch_id,
        )

    return sent_count


async def check_kafka_health() -> dict:
    """
    Check Kafka connectivity for the health endpoint.
    Returns a dict with status and metadata.

    Uses partitions_for() which queries the broker for topic metadata.
    If the broker is unreachable, it raises an exception within the timeout.
    """
    producer = get_producer()

    if producer is None:
        return {
            "status": "unavailable",
            "error": "Producer not initialized — Kafka may be down at startup",
        }

    try:
        # partitions_for() fetches metadata for the topic from the broker.
        # It's a real broker round-trip — a valid liveness check.
        # Returns a set of partition IDs (e.g., {0, 1, 2} for 3 partitions).
        partitions = await producer.partitions_for(settings.kafka_topic_raw_logs)
        return {
            "status": "connected",
            "topic": settings.kafka_topic_raw_logs,
            "partitions": len(partitions),
            "bootstrap_servers": settings.kafka_bootstrap_servers,
        }
    except Exception as e:
        return {
            "status": "unreachable",
            "error": str(e),
            "bootstrap_servers": settings.kafka_bootstrap_servers,
        }
