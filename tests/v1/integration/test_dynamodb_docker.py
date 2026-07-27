"""Real integration tests for the v1 ``dynamodb`` connector against DynamoDB Local.

Drives ``DynamoDBConnector`` through its real ``boto3`` resource path against the
official ``amazon/dynamodb-local`` container. The endpoint is supplied to boto3
via the standard ``AWS_ENDPOINT_URL_DYNAMODB`` environment variable, so the connector is
unchanged. Skipped unless DynamoDB Local is reachable at
``$OMNI_TEST_DYNAMO_ENDPOINT`` (default ``http://localhost:8000``).

Spin one up with Docker:

    docker run -d --name omni-dynamo -p 8000:8000 amazon/dynamodb-local
"""

from __future__ import annotations

import os
import time
from urllib.parse import quote

import pytest

from omni_fetcher.v1.auth import AwsAuth
from omni_fetcher.v1.connectors.dynamodb import DynamoDBConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

_ENDPOINT = os.environ.get("OMNI_TEST_DYNAMO_ENDPOINT", "http://localhost:8000")
# Set the global endpoint override before boto3's default session is created.
# Service-specific override so this can run in the same process as the Kinesis
# integration test (which points at a different endpoint) without colliding.
os.environ["AWS_ENDPOINT_URL_DYNAMODB"] = _ENDPOINT

pytest.importorskip("boto3")
import boto3  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402

_REGION = "us-east-1"
_TABLE = f"omni-orders-{int(time.time())}"
_AWS = AwsAuth(access_key_id="test", secret_access_key="test", region=_REGION)


async def _collect(agen):
    return [item async for item in agen]


@pytest.fixture
def table():
    """Create a table, put two items, point the connector at DynamoDB Local."""
    boto3.DEFAULT_SESSION = None
    resource = boto3.resource(
        "dynamodb",
        endpoint_url=_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name=_REGION,
    )
    try:
        tbl = resource.create_table(
            TableName=_TABLE,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        tbl.wait_until_exists()
    except (BotoCoreError, ClientError, OSError):
        pytest.skip(f"no DynamoDB Local reachable at {_ENDPOINT}")

    tbl.put_item(Item={"id": "1", "name": "one", "qty": 10})
    tbl.put_item(Item={"id": "2", "name": "two", "qty": 20})
    try:
        yield
    finally:
        try:
            tbl.delete()
        except (BotoCoreError, ClientError):
            pass


async def test_scan_all_items(table):
    result = (await _collect(DynamoDBConnector().stream(f"dynamodb://{_TABLE}", auth=_AWS)))[0]

    assert isinstance(result, Success), result
    node = result.tree
    assert node.metadata.kind == "documents"
    assert node.metadata.source_extra["dynamodb"]["mode"] == "scan"
    assert node.metadata.source_extra["dynamodb"]["document_count"] == 2
    bodies = "".join(list(c.iter_atoms())[0].content for c in node.children)
    assert "one" in bodies and "two" in bodies


async def test_get_item_by_key(table):
    key = quote('{"id": "1"}')
    result = (
        await _collect(DynamoDBConnector().stream(f"dynamodb://{_TABLE}?key={key}", auth=_AWS))
    )[0]

    assert isinstance(result, Success), result
    assert result.tree.metadata.source_extra["dynamodb"]["mode"] == "get"
    assert result.tree.metadata.source_extra["dynamodb"]["document_count"] == 1
    assert "one" in list(result.tree.children[0].iter_atoms())[0].content


async def test_get_item_miss_is_not_found(table):
    key = quote('{"id": "does-not-exist"}')
    result = (
        await _collect(DynamoDBConnector().stream(f"dynamodb://{_TABLE}?key={key}", auth=_AWS))
    )[0]

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND
