"""External-behaviour tests for the v1 ``mongodb`` connectors.

Both connectors are exercised through their seams (no live MongoDB): the bounded
``find`` via ``_query`` and the change stream via ``_watch``. Covered: the
``documents`` container of ``json_document`` children; the ``change`` nodes;
credential resolution; error mapping by driver code; truncation; ``fetch()`` on
the change stream is ``UNSUPPORTED``; a missing extra is ``UNSUPPORTED``.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import BasicAuth
from omni_fetcher.v1.connectors import mongodb as mongo_module
from omni_fetcher.v1.connectors.mongodb import (
    MongoChangeStreamConnector,
    MongoQueryConnector,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success


class _MongoError(Exception):
    """A pymongo error stand-in carrying a numeric ``code``."""

    def __init__(self, code: Optional[int] = None) -> None:
        super().__init__(f"mongo error {code}")
        self.code = code


async def _collect(agen):
    return [item async for item in agen]


# --- bounded find ------------------------------------------------------------


def _install_query(monkeypatch, documents=None, raises=None, captured=None):
    async def fake_query(self, spec, user, password, filter_doc, projection, doc_cap):
        if captured is not None:
            captured.update(user=user, password=password, filter=filter_doc, cap=doc_cap)
        if raises is not None:
            raise raises
        return list(documents or [])

    monkeypatch.setattr(MongoQueryConnector, "_query", fake_query)


async def test_find_yields_documents_container(monkeypatch):
    _install_query(monkeypatch, [{"_id": "a", "name": "one"}, {"_id": "b", "name": "two"}])

    results = await _collect(MongoQueryConnector().stream("mongodb://h/mydb.things"))

    assert len(results) == 1
    assert isinstance(results[0], Success)
    node = results[0].tree
    assert node.metadata.kind == "documents"
    assert node.metadata.source_extra["mongodb"]["collection"] == "things"
    assert node.metadata.source_extra["mongodb"]["document_count"] == 2
    assert len(node.children) == 2
    child = node.children[0]
    assert child.metadata.kind == "json_document"
    atoms = list(child.iter_atoms())
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].format == TextFormat.CODE
    assert child.metadata.source_extra["mongodb"]["id"] == "a"


async def test_query_json_and_credentials(monkeypatch):
    captured: dict = {}
    _install_query(monkeypatch, [{"_id": "a"}], captured=captured)

    query = quote('{"name": "one"}')
    result = await _collect(
        MongoQueryConnector().stream(
            f"mongodb://uriuser:uripass@h/db.coll?query={query}",
            auth=BasicAuth(username="au", password="ap"),
        )
    )

    assert isinstance(result[0], Success)
    assert captured["filter"] == {"name": "one"}
    assert (captured["user"], captured["password"]) == ("au", "ap")  # BasicAuth wins


async def test_over_cap_is_partial(monkeypatch):
    _install_query(monkeypatch, [{"_id": "a"}, {"_id": "b"}])

    results = await _collect(MongoQueryConnector().stream("mongodb://h/db.coll?limit=1"))

    assert isinstance(results[0], Partial)
    assert results[0].tree.metadata.source_extra["mongodb"]["document_count"] == 1
    assert any(g.kind == ErrorKind.UNSUPPORTED for g in results[0].gaps)


async def test_unauthorized_is_permission_denied(monkeypatch):
    _install_query(monkeypatch, raises=_MongoError(code=13))

    results = await _collect(MongoQueryConnector().stream("mongodb://h/db.coll"))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.PERMISSION_DENIED


async def test_auth_failed_code(monkeypatch):
    _install_query(monkeypatch, raises=_MongoError(code=18))

    results = await _collect(MongoQueryConnector().stream("mongodb://h/db.coll"))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.AUTH_FAILED


async def test_bad_query_json_is_invalid_input(monkeypatch):
    _install_query(monkeypatch, [{"_id": "a"}])

    results = await _collect(
        MongoQueryConnector().stream("mongodb://h/db.coll?query=" + quote("{not json"))
    )

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.INVALID_INPUT


async def test_invalid_uri_is_invalid_input():
    results = await _collect(MongoQueryConnector().stream("mongodb://h/no-collection"))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.INVALID_INPUT


async def test_query_unsupported_when_extra_missing(monkeypatch):
    monkeypatch.setattr(mongo_module, "MONGODB_AVAILABLE", False)

    results = await _collect(MongoQueryConnector().stream("mongodb://h/db.coll"))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.UNSUPPORTED


# --- change stream -----------------------------------------------------------


def _install_watch(monkeypatch, changes, raise_after=False):
    async def fake_watch(self, spec, user, password):
        for change in changes:
            yield change
        if raise_after:
            raise _MongoError(code=None)

    monkeypatch.setattr(MongoChangeStreamConnector, "_watch", fake_watch)


_CHANGE = {
    "_id": {"_data": "resume-token-1"},
    "operationType": "insert",
    "fullDocument": {"_id": "x", "v": 1},
    "ns": {"db": "db", "coll": "coll"},
}


async def test_change_stream_yields_change_nodes(monkeypatch):
    _install_watch(monkeypatch, [_CHANGE])

    results = await _collect(
        MongoChangeStreamConnector().stream("mongodb+changestream://h/db.coll")
    )

    assert len(results) == 1
    assert isinstance(results[0], Success)
    node = results[0].tree
    assert node.metadata.kind == "change"
    assert node.metadata.source_extra["mongodb"]["operation_type"] == "insert"
    assert node.metadata.source_extra["mongodb"]["resume_token"] == {"_data": "resume-token-1"}


async def test_change_stream_transport_failure_is_transient(monkeypatch):
    _install_watch(monkeypatch, [_CHANGE], raise_after=True)

    results = await _collect(
        MongoChangeStreamConnector().stream("mongodb+changestream://h/db.coll")
    )

    assert isinstance(results[0], Success)
    assert isinstance(results[1], Error)
    assert results[1].kind == ErrorKind.TRANSIENT


async def test_change_stream_fetch_is_unsupported():
    result = await MongoChangeStreamConnector().fetch("mongodb+changestream://h/db.coll")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED


def test_can_handle_disjoint_schemes():
    assert MongoQueryConnector.can_handle("mongodb://h/db.coll")
    assert not MongoQueryConnector.can_handle("mongodb+changestream://h/db.coll")
    assert MongoChangeStreamConnector.can_handle("mongodb+changestream://h/db.coll")
    assert not MongoChangeStreamConnector.can_handle("mongodb://h/db.coll")
