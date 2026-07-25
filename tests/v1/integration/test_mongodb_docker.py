"""Real integration tests for the v1 ``mongodb`` connectors against a live MongoDB.

Unlike the seam-based unit tests, these drive the connectors through their real
``motor`` code path against an actual MongoDB (a single-node replica set, so the
change stream works). The suite is **skipped** unless a MongoDB is reachable at
``$OMNI_TEST_MONGO_URI`` (default ``mongodb://localhost:27017``), so it is inert
in an environment without one.

Spin one up with Docker:

    docker run -d --name omni-mongo-test -p 27017:27017 mongo:7 --replSet rs0
    docker exec omni-mongo-test mongosh --quiet --eval "rs.initiate()"

Then run:

    pytest tests/v1/integration/test_mongodb_docker.py
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import quote, urlsplit

import pytest

from omni_fetcher.v1.connectors.mongodb import (
    MongoChangeStreamConnector,
    MongoQueryConnector,
)
from omni_fetcher.v1.result import Success

pytest.importorskip("motor", reason="motor (the 'mongodb' extra) is not installed")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

_MONGO_URI = os.environ.get("OMNI_TEST_MONGO_URI", "mongodb://localhost:27017")
_DB = "omni_test"
_COLLECTION = "things"


def _host_port() -> str:
    """The ``host:port`` authority the connectors address (from the test URI)."""
    parts = urlsplit(_MONGO_URI)
    return f"{parts.hostname}:{parts.port or 27017}"


@pytest.fixture
async def seeded():
    """Reset the test collection and seed two documents; yield a live client."""
    client = AsyncIOMotorClient(_MONGO_URI, serverSelectionTimeoutMS=2000)
    try:
        await client.admin.command("ping")
    except Exception:
        pytest.skip(f"no MongoDB reachable at {_MONGO_URI}")
    collection = client[_DB][_COLLECTION]
    await collection.delete_many({})
    await collection.insert_many(
        [
            {"_id": 1, "name": "one", "kind": "widget"},
            {"_id": 2, "name": "two", "kind": "gadget"},
        ]
    )
    try:
        yield client
    finally:
        await collection.delete_many({})
        client.close()


async def test_find_all_documents(seeded):
    uri = f"mongodb://{_host_port()}/{_DB}.{_COLLECTION}"

    result = await MongoQueryConnector().fetch(uri)

    assert isinstance(result, Success), result
    node = result.tree
    assert node.metadata.kind == "documents"
    assert node.metadata.source_extra["mongodb"]["document_count"] == 2
    ids = {child.metadata.source_extra["mongodb"]["id"] for child in node.children}
    assert ids == {"1", "2"}
    # Each child carries the document JSON as its atom.
    first = list(node.children[0].iter_atoms())[0]
    assert "name" in first.content


async def test_find_with_filter(seeded):
    query = quote('{"kind": "widget"}')
    uri = f"mongodb://{_host_port()}/{_DB}.{_COLLECTION}?query={query}"

    result = await MongoQueryConnector().fetch(uri)

    assert isinstance(result, Success), result
    assert result.tree.metadata.source_extra["mongodb"]["document_count"] == 1
    body = list(result.tree.children[0].iter_atoms())[0].content
    assert "one" in body


async def test_find_with_limit_truncates(seeded):
    uri = f"mongodb://{_host_port()}/{_DB}.{_COLLECTION}?limit=1"

    result = await MongoQueryConnector().fetch(uri)

    # fetch() collects the bounded stream; a truncation is a Partial.
    assert result.tree.metadata.source_extra["mongodb"]["document_count"] == 1


async def test_change_stream_sees_an_insert(seeded):
    uri = f"mongodb+changestream://{_host_port()}/{_DB}.{_COLLECTION}"
    collection = seeded[_DB][_COLLECTION]

    iterator = MongoChangeStreamConnector().stream(uri).__aiter__()
    # Start awaiting the first change, then let the watch establish before writing.
    first = asyncio.ensure_future(iterator.__anext__())
    await asyncio.sleep(1.5)
    await collection.insert_one({"_id": 99, "name": "fresh"})

    try:
        result = await asyncio.wait_for(first, timeout=15)
    finally:
        if not first.done():
            first.cancel()
        await iterator.aclose()

    assert isinstance(result, Success), result
    assert result.tree.metadata.kind == "change"
    extra = result.tree.metadata.source_extra["mongodb"]
    assert extra["operation_type"] == "insert"
    assert extra["resume_token"] is not None
