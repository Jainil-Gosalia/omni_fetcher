"""Real integration test for the v1 ``kinesis`` connector against LocalStack.

Drives ``KinesisConnector`` through its real ``boto3`` code path against a
LocalStack Kinesis. The endpoint is supplied to boto3 via the standard
``AWS_ENDPOINT_URL`` environment variable (botocore's global override), so the
connector itself is unchanged. Skipped unless LocalStack is reachable at
``$OMNI_TEST_AWS_ENDPOINT`` (default ``http://localhost:4566``).

Spin one up with Docker:

    docker run -d --name omni-localstack -p 4566:4566 -e SERVICES=kinesis localstack/localstack:3.0
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from omni_fetcher.v1.auth import AwsAuth
from omni_fetcher.v1.connectors.kinesis import KinesisConnector
from omni_fetcher.v1.result import Success

_ENDPOINT = os.environ.get("OMNI_TEST_AWS_ENDPOINT", "http://localhost:4566")
# Set the global endpoint override BEFORE boto3's default session is created, so
# every client in this process (setup + the connector's own) resolves to
# LocalStack. The connector is unchanged; this is the standard AWS env override.
os.environ["AWS_ENDPOINT_URL"] = _ENDPOINT

pytest.importorskip("boto3")
import boto3  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402

_REGION = "us-east-1"
# Unique per run so a delete-pending stream from an earlier run never interferes.
_STREAM = f"omni-stream-{int(time.time())}"
_AWS = AwsAuth(access_key_id="test", secret_access_key="test", region=_REGION)


async def _take(stream, n, timeout=20):
    iterator = stream.__aiter__()
    items = []
    try:
        for _ in range(n):
            items.append(await asyncio.wait_for(iterator.__anext__(), timeout))
    finally:
        await iterator.aclose()
    return items


def _client():
    return boto3.client(
        "kinesis",
        endpoint_url=_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name=_REGION,
    )


@pytest.fixture
def kinesis_stream():
    """Create a stream, put two records, and point the connector at LocalStack."""
    # Force a fresh default session so the connector's boto3.client() re-reads
    # AWS_ENDPOINT_URL (a session cached by an earlier test would ignore it).
    boto3.DEFAULT_SESSION = None
    try:
        client = _client()
        client.create_stream(StreamName=_STREAM, ShardCount=1)
        client.get_waiter("stream_exists").wait(StreamName=_STREAM)
        # Wait for ACTIVE, not just existence, before putting records.
        for _ in range(30):
            status = client.describe_stream(StreamName=_STREAM)["StreamDescription"]["StreamStatus"]
            if status == "ACTIVE":
                break
            time.sleep(0.5)
    except (BotoCoreError, ClientError, OSError):
        pytest.skip(f"no LocalStack Kinesis reachable at {_ENDPOINT}")

    first = client.put_record(StreamName=_STREAM, Data=b"record-0", PartitionKey="pk")
    client.put_record(StreamName=_STREAM, Data=b"record-1", PartitionKey="pk")
    first_seq = first["SequenceNumber"]

    try:
        yield first_seq
    finally:
        try:
            client.delete_stream(StreamName=_STREAM, EnforceConsumerDeletion=True)
        except (BotoCoreError, ClientError):
            pass


async def test_consume_records(kinesis_stream):
    first_seq = kinesis_stream
    uri = f"kinesis://{_STREAM}?at={first_seq}"

    results = await _take(KinesisConnector(poll_interval=0.3).stream(uri, auth=_AWS), 2)

    assert all(isinstance(r, Success) for r in results), results
    contents = [list(r.tree.iter_atoms())[0].content for r in results]
    assert contents == ["record-0", "record-1"]
    extra = results[0].tree.metadata.source_extra["kinesis"]
    assert extra["stream"] == _STREAM
    assert extra["sequence_number"] is not None
