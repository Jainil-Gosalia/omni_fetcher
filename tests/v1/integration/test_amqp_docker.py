"""Real integration test for the v1 ``amqp`` connector against a live RabbitMQ.

Drives ``AMQPConnector`` through its real ``aio-pika`` code path against an actual
broker. Skipped unless a broker is reachable at ``$OMNI_TEST_AMQP_URL`` (default
``amqp://guest:guest@localhost:5672/``).

Spin one up with Docker:

    docker run -d --name omni-rabbit -p 5672:5672 rabbitmq:3
"""

from __future__ import annotations

import asyncio
import os

import pytest

from omni_fetcher.v1.connectors.amqp import AMQPConnector
from omni_fetcher.v1.result import Success

pytest.importorskip("aio_pika", reason="aio-pika (the 'amqp' extra) is not installed")
import aio_pika  # noqa: E402

_AMQP_URL = os.environ.get("OMNI_TEST_AMQP_URL", "amqp://guest:guest@localhost:5672/")
_QUEUE = "omni_jobs"


async def _take(stream, n, timeout=15):
    """Take the first ``n`` items from an unbounded stream, then close it."""
    iterator = stream.__aiter__()
    items = []
    try:
        for _ in range(n):
            items.append(await asyncio.wait_for(iterator.__anext__(), timeout))
    finally:
        await iterator.aclose()
    return items


@pytest.fixture
async def rabbit():
    """Declare the queue and publish two messages; skip if no broker."""
    try:
        connection = await aio_pika.connect_robust(_AMQP_URL, timeout=3)
    except Exception:
        pytest.skip(f"no RabbitMQ reachable at {_AMQP_URL}")
    channel = await connection.channel()
    queue = await channel.declare_queue(_QUEUE, durable=True)
    await queue.purge()
    for i in range(2):
        await channel.default_exchange.publish(
            aio_pika.Message(body=f"payload-{i}".encode()), routing_key=_QUEUE
        )
    yield
    await channel.default_exchange.publish(aio_pika.Message(body=b"__drain__"), routing_key=_QUEUE)
    await queue.purge()
    await connection.close()


async def test_consume_messages(rabbit):
    uri = "amqp://guest:guest@localhost:5672/" + _QUEUE

    results = await _take(AMQPConnector().stream(uri), 2)

    assert all(isinstance(r, Success) for r in results), results
    contents = {list(r.tree.iter_atoms())[0].content for r in results}
    assert contents == {"payload-0", "payload-1"}
    for r in results:
        assert r.tree.metadata.kind == "message"
        assert r.tree.metadata.source_extra["amqp"]["queue"] == _QUEUE
