"""External-behaviour tests for the v1 ``pubsub`` connector.

The broker is stubbed via the ``_consume`` seam (no live Pub/Sub): a fake async
generator yields scripted messages. Covered: per-message ``message`` nodes with
facts in ``source_extra["pubsub"]``; ``fetch()`` is ``UNSUPPORTED``; a
missing/wrong auth is ``AUTH_FAILED``; a bad URI is ``INVALID_INPUT``; a
transport failure is a terminal ``TRANSIENT``; a missing extra is
``UNSUPPORTED``.
"""

from __future__ import annotations

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import BearerAuth, OAuth2Auth
from omni_fetcher.v1.connectors import pubsub as pubsub_module
from omni_fetcher.v1.connectors.pubsub import PubSubConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

_AUTH = OAuth2Auth(access_token="ya29.pubsub")


def _install_consume(monkeypatch, messages, raise_after=False):
    async def fake_consume(self, spec, auth):
        for message in messages:
            yield message
        if raise_after:
            raise RuntimeError("pull failed")

    monkeypatch.setattr(PubSubConnector, "_consume", fake_consume)


async def _collect(agen):
    return [item async for item in agen]


_MSG = {
    "content": "hello pubsub",
    "fields": {"message_id": "m-1", "subscription": "sub", "publish_time": None},
}


async def test_stream_yields_message_nodes(monkeypatch):
    _install_consume(monkeypatch, [_MSG])

    results = await _collect(PubSubConnector().stream("pubsub://proj/sub", auth=_AUTH))

    assert len(results) == 1
    assert isinstance(results[0], Success)
    node = results[0].tree
    assert node.metadata.kind == "message"
    atoms = list(node.iter_atoms())
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].content == "hello pubsub"
    assert node.metadata.source_extra["pubsub"]["message_id"] == "m-1"


async def test_transport_failure_is_terminal_transient(monkeypatch):
    _install_consume(monkeypatch, [_MSG], raise_after=True)

    results = await _collect(PubSubConnector().stream("pubsub://proj/sub", auth=_AUTH))

    assert isinstance(results[0], Success)
    assert isinstance(results[1], Error)
    assert results[1].kind == ErrorKind.TRANSIENT


async def test_unsupported_when_extra_missing(monkeypatch):
    monkeypatch.setattr(pubsub_module, "PUBSUB_AVAILABLE", False)

    results = await _collect(PubSubConnector().stream("pubsub://proj/sub", auth=_AUTH))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.UNSUPPORTED


async def test_missing_auth_is_auth_failed(monkeypatch):
    def _explode(self, spec, auth):
        raise AssertionError("consume must not run without auth")

    monkeypatch.setattr(PubSubConnector, "_consume", _explode)

    results = await _collect(PubSubConnector().stream("pubsub://proj/sub", auth=None))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.AUTH_FAILED


async def test_non_oauth_auth_is_auth_failed(monkeypatch):
    def _explode(self, spec, auth):
        raise AssertionError("consume must not run for non-OAuth auth")

    monkeypatch.setattr(PubSubConnector, "_consume", _explode)

    results = await _collect(
        PubSubConnector().stream("pubsub://proj/sub", auth=BearerAuth(token="x"))
    )

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.AUTH_FAILED


async def test_bad_uri_is_invalid_input(monkeypatch):
    results = await _collect(PubSubConnector().stream("pubsub://only-project", auth=_AUTH))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.INVALID_INPUT


async def test_fetch_is_unsupported():
    result = await PubSubConnector().fetch("pubsub://proj/sub", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED


def test_can_handle():
    assert PubSubConnector.can_handle("pubsub://proj/sub")
    assert not PubSubConnector.can_handle("kinesis://s")
