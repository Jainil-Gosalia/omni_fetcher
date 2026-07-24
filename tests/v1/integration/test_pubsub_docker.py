"""Real integration test for the v1 ``pubsub`` connector against the emulator.

Drives ``PubSubConnector`` through its real ``google-cloud-pubsub`` code path
against the Pub/Sub emulator. The emulator is selected via the standard
``PUBSUB_EMULATOR_HOST`` environment variable, so the connector itself is
unchanged. Skipped unless the emulator is reachable at
``$PUBSUB_EMULATOR_HOST`` (default ``localhost:8681``).

Spin one up with Docker:

    docker run -d --name omni-pubsub -p 8681:8681 \
        -e "PUBSUB_PROJECT1=omni-proj,omni-topic:omni-sub" \
        messagebird/gcloud-pubsub-emulator:latest
"""

from __future__ import annotations

import asyncio
import os

import pytest

from omni_fetcher.v1.auth import OAuth2Auth
from omni_fetcher.v1.connectors.pubsub import PubSubConnector
from omni_fetcher.v1.result import Success

pytest.importorskip("google.cloud.pubsub_v1", reason="the 'pubsub' extra is not installed")
from google.cloud import pubsub_v1  # noqa: E402

_EMULATOR = os.environ.get("PUBSUB_EMULATOR_HOST", "localhost:8681")
_PROJECT = "omni-proj"
_TOPIC = "omni-topic"
_SUB = "omni-sub"


async def _take(stream, n, timeout=20):
    iterator = stream.__aiter__()
    items = []
    try:
        for _ in range(n):
            items.append(await asyncio.wait_for(iterator.__anext__(), timeout))
    finally:
        await iterator.aclose()
    return items


@pytest.fixture
def published():
    """Publish one message to the emulator topic; skip if unreachable."""
    os.environ["PUBSUB_EMULATOR_HOST"] = _EMULATOR
    try:
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(_PROJECT, _TOPIC)
        future = publisher.publish(topic_path, b"hello-pubsub")
        future.result(timeout=8)
    except Exception as exc:  # noqa: BLE001 - any failure = emulator not usable
        pytest.skip(f"Pub/Sub emulator not usable at {_EMULATOR}: {exc}")
    yield


async def test_consume_message(published):
    uri = f"pubsub://{_PROJECT}/{_SUB}"

    results = await _take(
        PubSubConnector().stream(uri, auth=OAuth2Auth(access_token="emulator")), 1
    )

    assert isinstance(results[0], Success), results[0]
    node = results[0].tree
    assert node.metadata.kind == "message"
    assert list(node.iter_atoms())[0].content == "hello-pubsub"
    assert node.metadata.source_extra["pubsub"]["subscription"] == _SUB
