"""External-behaviour tests for the v1 ``amqp`` connector.

The broker is stubbed via the ``_consume`` seam (no live RabbitMQ): a fake async
generator yields scripted messages. Covered: per-message ``message`` nodes with
facts in ``source_extra["amqp"]``; credential resolution (BasicAuth wins over
URI userinfo, else guest); ``fetch()`` is ``UNSUPPORTED``; a bad URI is
``INVALID_INPUT``; a transport failure is a terminal ``TRANSIENT``; a missing
extra is ``UNSUPPORTED``.
"""

from __future__ import annotations

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import BasicAuth
from omni_fetcher.v1.connectors import amqp as amqp_module
from omni_fetcher.v1.connectors.amqp import AMQPConnector, _parse_uri, _resolve_credentials
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success


def _install_consume(monkeypatch, messages, raise_after=False):
    async def fake_consume(self, spec, auth):
        for message in messages:
            yield message
        if raise_after:
            raise RuntimeError("channel closed")

    monkeypatch.setattr(AMQPConnector, "_consume", fake_consume)


async def _collect(agen):
    return [item async for item in agen]


_MSG = {
    "content": "queued body",
    "fields": {
        "message_id": "id-1",
        "routing_key": "rk",
        "exchange": "ex",
        "delivery_tag": 7,
        "queue": "jobs",
        "timestamp": None,
    },
}


async def test_stream_yields_message_nodes(monkeypatch):
    _install_consume(monkeypatch, [_MSG])

    results = await _collect(AMQPConnector().stream("amqp://host/jobs", auth=None))

    assert len(results) == 1
    assert isinstance(results[0], Success)
    node = results[0].tree
    assert node.metadata.kind == "message"
    atoms = list(node.iter_atoms())
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].content == "queued body"
    assert node.metadata.source_extra["amqp"]["queue"] == "jobs"


async def test_transport_failure_is_terminal_transient(monkeypatch):
    _install_consume(monkeypatch, [_MSG], raise_after=True)

    results = await _collect(AMQPConnector().stream("amqp://host/jobs", auth=None))

    assert isinstance(results[0], Success)
    assert isinstance(results[1], Error)
    assert results[1].kind == ErrorKind.TRANSIENT


async def test_unsupported_when_extra_missing(monkeypatch):
    monkeypatch.setattr(amqp_module, "AMQP_AVAILABLE", False)

    results = await _collect(AMQPConnector().stream("amqp://host/jobs", auth=None))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.UNSUPPORTED


async def test_bad_uri_is_invalid_input(monkeypatch):
    results = await _collect(AMQPConnector().stream("amqp://host", auth=None))  # no queue

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.INVALID_INPUT


async def test_fetch_is_unsupported():
    result = await AMQPConnector().fetch("amqp://host/jobs", auth=None)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED


def test_credentials_basic_auth_wins_over_uri():
    spec = _parse_uri("amqp://uriuser:uripass@host/jobs")
    user, password = _resolve_credentials(spec, BasicAuth(username="au", password="ap"))
    assert (user, password) == ("au", "ap")


def test_credentials_uri_userinfo_then_guest():
    spec = _parse_uri("amqp://uriuser:uripass@host/jobs")
    assert _resolve_credentials(spec, None) == ("uriuser", "uripass")

    bare = _parse_uri("amqp://host/jobs")
    assert _resolve_credentials(bare, None) == ("guest", "guest")


def test_parse_uri_tls_and_vhost():
    spec = _parse_uri("amqps://host:5671/jobs?vhost=/prod")
    assert spec.tls is True
    assert spec.port == 5671
    assert spec.vhost == "/prod"
    assert spec.queue == "jobs"


def test_can_handle():
    assert AMQPConnector.can_handle("amqp://host/q")
    assert AMQPConnector.can_handle("amqps://host/q")
    assert not AMQPConnector.can_handle("kafka://topic")
