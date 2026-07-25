"""External-behaviour tests for the v1 ``dynamodb`` connector.

The table is stubbed via the ``_read`` seam (no live AWS). Covered: the
``documents`` container of ``json_document`` children; a GetItem miss is
``NOT_FOUND`` while a Scan miss is an empty container; a missing/wrong auth is
``AUTH_FAILED``; a bad URI/key is ``INVALID_INPUT``; AWS error codes map onto
the taxonomy; truncation degrades to a ``Partial``.
"""

from __future__ import annotations

from urllib.parse import quote

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import AwsAuth, BearerAuth
from omni_fetcher.v1.connectors.dynamodb import DynamoDBConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success

_AWS = AwsAuth(access_key_id="AKIA", secret_access_key="secret", region="us-east-1")


class _ClientError(Exception):
    """A botocore ClientError stand-in carrying an AWS error ``Code``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


async def _collect(agen):
    return [item async for item in agen]


def _install_read(monkeypatch, items=None, raises=None, captured=None):
    async def fake_read(self, spec, auth, key, doc_cap):
        if captured is not None:
            captured.update(key=key, cap=doc_cap, table=spec.table)
        if raises is not None:
            raise raises
        return list(items or [])

    monkeypatch.setattr(DynamoDBConnector, "_read", fake_read)


async def test_scan_yields_documents_container(monkeypatch):
    _install_read(monkeypatch, [{"id": "1", "v": 10}, {"id": "2", "v": 20}])

    results = await _collect(DynamoDBConnector().stream("dynamodb://orders", auth=_AWS))

    assert len(results) == 1
    assert isinstance(results[0], Success)
    node = results[0].tree
    assert node.metadata.kind == "documents"
    assert node.metadata.source_extra["dynamodb"]["mode"] == "scan"
    assert node.metadata.source_extra["dynamodb"]["document_count"] == 2
    child = node.children[0]
    assert child.metadata.kind == "json_document"
    atoms = list(child.iter_atoms())
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].format == TextFormat.CODE


async def test_get_item_by_key(monkeypatch):
    captured: dict = {}
    _install_read(monkeypatch, [{"id": "1", "v": 10}], captured=captured)

    key = quote('{"id": "1"}')
    results = await _collect(DynamoDBConnector().stream(f"dynamodb://orders?key={key}", auth=_AWS))

    assert isinstance(results[0], Success)
    assert results[0].tree.metadata.source_extra["dynamodb"]["mode"] == "get"
    assert captured["key"] == {"id": "1"}


async def test_get_item_miss_is_not_found(monkeypatch):
    _install_read(monkeypatch, [])  # key given, nothing returned

    key = quote('{"id": "nope"}')
    results = await _collect(DynamoDBConnector().stream(f"dynamodb://orders?key={key}", auth=_AWS))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.NOT_FOUND


async def test_scan_empty_is_empty_container(monkeypatch):
    _install_read(monkeypatch, [])  # scan, nothing matched

    results = await _collect(DynamoDBConnector().stream("dynamodb://orders", auth=_AWS))

    assert isinstance(results[0], Success)
    assert results[0].tree.metadata.source_extra["dynamodb"]["document_count"] == 0


async def test_over_cap_is_partial(monkeypatch):
    _install_read(monkeypatch, [{"id": "1"}, {"id": "2"}])

    results = await _collect(DynamoDBConnector().stream("dynamodb://orders?limit=1", auth=_AWS))

    assert isinstance(results[0], Partial)
    assert results[0].tree.metadata.source_extra["dynamodb"]["document_count"] == 1


async def test_table_not_found_maps(monkeypatch):
    _install_read(monkeypatch, raises=_ClientError("ResourceNotFoundException"))

    results = await _collect(DynamoDBConnector().stream("dynamodb://gone", auth=_AWS))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.NOT_FOUND


async def test_validation_is_invalid_input(monkeypatch):
    _install_read(monkeypatch, raises=_ClientError("ValidationException"))

    results = await _collect(DynamoDBConnector().stream("dynamodb://orders", auth=_AWS))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.INVALID_INPUT


async def test_missing_auth_is_auth_failed():
    results = await _collect(DynamoDBConnector().stream("dynamodb://orders", auth=None))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.AUTH_FAILED


async def test_non_aws_auth_is_auth_failed():
    results = await _collect(
        DynamoDBConnector().stream("dynamodb://orders", auth=BearerAuth(token="x"))
    )

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.AUTH_FAILED


async def test_bad_key_json_is_invalid_input():
    results = await _collect(
        DynamoDBConnector().stream("dynamodb://orders?key=" + quote("{bad"), auth=_AWS)
    )

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.INVALID_INPUT


def test_can_handle():
    assert DynamoDBConnector.can_handle("dynamodb://orders")
    assert not DynamoDBConnector.can_handle("mongodb://h/db.coll")
