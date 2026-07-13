"""Live-broker integration tests for the v1 kafka connector.

Excluded from CI (``--ignore=tests/integration``); run locally against a
real broker. Point ``OMNI_KAFKA_BOOTSTRAP`` at one (default
``localhost:9092``) -- e.g. a single-node container:

    docker run -d -p 9092:9092 --name kafka \
      -e KAFKA_NODE_ID=1 -e KAFKA_PROCESS_ROLES=broker,controller \
      -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093 \
      -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
      -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
      -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093 \
      -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \
      -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
      -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
      -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 apache/kafka:3.8.0

These exercise the real ``_AioKafkaAdapter`` path the unit suite (scripted
fake) cannot -- including assign-mode partition-metadata discovery, which a
regression would hang on.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from typing import AsyncIterator, List

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.result import Result, Success

aiokafka = pytest.importorskip("aiokafka")

pytestmark = pytest.mark.asyncio

BOOTSTRAP = os.environ.get("OMNI_KAFKA_BOOTSTRAP", "localhost:9092")


def _broker_up() -> bool:
    host, _, port = BOOTSTRAP.partition(":")
    try:
        with socket.create_connection((host, int(port or "9092")), timeout=1.0):
            return True
    except OSError:
        return False


requires_broker = pytest.mark.skipif(not _broker_up(), reason=f"no Kafka broker at {BOOTSTRAP}")


async def _produce(topic: str, values: List[bytes]) -> None:
    producer = aiokafka.AIOKafkaProducer(bootstrap_servers=BOOTSTRAP)
    await producer.start()
    try:
        for value in values:
            await producer.send_and_wait(topic, value=value)
    finally:
        await producer.stop()


async def _consume(uri: str, count: int, timeout: float = 30.0) -> List[Result]:
    from omni_fetcher.v1.connectors.kafka import KafkaConnector

    items: List[Result] = []
    stream: AsyncIterator[Result] = KafkaConnector().stream(uri)

    async def _run() -> None:
        async for item in stream:
            items.append(item)
            if len(items) >= count:
                break

    try:
        await asyncio.wait_for(_run(), timeout=timeout)
    finally:
        await stream.aclose()  # type: ignore[attr-defined]
    return items


def _texts(items: List[Result]) -> List[str]:
    out = []
    for item in items:
        assert isinstance(item, Success), item
        out.append(item.tree.find_atoms(AtomKind.TEXT)[0].content)
    return out


@requires_broker
async def test_stateless_produce_consume_and_resume() -> None:
    """Assign-mode: consume from earliest, then resume from an offset."""
    topic = f"omni-it-stateless-{int(time.time() * 1000)}"
    await _produce(topic, [b"a", b"b", b"c"])

    items = await _consume(f"kafka://{BOOTSTRAP}/{topic}?offset=earliest", 3)
    assert _texts(items) == ["a", "b", "c"]

    extra = items[0].tree.metadata.source_extra["kafka"]  # type: ignore[union-attr]
    assert extra["topic"] == topic and extra["offset"] == 0

    resumed = await _consume(f"kafka://{BOOTSTRAP}/{topic}?offsets=0:1", 2)
    assert _texts(resumed) == ["b", "c"]


@requires_broker
async def test_group_commit_persists_across_reconnect() -> None:
    """Group mode: committed offsets survive a same-group reconnect."""
    topic = f"omni-it-group-{int(time.time() * 1000)}"
    group = f"omni-it-cg-{int(time.time() * 1000)}"
    await _produce(topic, [b"g0", b"g1", b"g2"])

    uri = f"kafka://{BOOTSTRAP}/{topic}?group={group}&offset=earliest"
    first = await _consume(uri, 3)
    assert _texts(first) == ["g0", "g1", "g2"]

    # g0/g1 committed on advance; g2 received-but-not-advanced -> its offset
    # is uncommitted, so a same-group reconnect redelivers g2 then the new
    # g3 (at-least-once), never the committed g0/g1.
    await _produce(topic, [b"g3"])
    resumed = await _consume(uri, 2)
    assert _texts(resumed) == ["g2", "g3"]
