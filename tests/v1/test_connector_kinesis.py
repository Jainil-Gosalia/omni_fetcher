"""External-behaviour tests for the v1 ``kinesis`` connector.

The broker is stubbed via the ``_consume`` seam (no live AWS): a fake async
generator yields scripted records. Covered: per-record ``message`` nodes with
the resume ``sequence_number`` in ``source_extra["kinesis"]``; ``fetch()`` is
``UNSUPPORTED``; a missing/wrong auth is ``AUTH_FAILED``; a bad URI is
``INVALID_INPUT``; a transport failure mid-stream is a terminal ``TRANSIENT``;
and the ``stream_with_restart`` resume derivation maps the sequence number onto
``?after=``.
"""

from __future__ import annotations

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import AwsAuth, BearerAuth
from omni_fetcher.v1.connectors.kinesis import KinesisConnector
from omni_fetcher.v1.connectors._messaging import build_message_result
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.mapping import SequenceCounter
from omni_fetcher.v1.result import Error, Success
from omni_fetcher.v1.retry import _resume_uri

_AWS = AwsAuth(access_key_id="AKIA", secret_access_key="secret", region="us-east-1")


def _install_consume(monkeypatch, messages, raise_after=False):
    async def fake_consume(self, spec, auth):
        for message in messages:
            yield message
        if raise_after:
            raise RuntimeError("shard read failed")

    monkeypatch.setattr(KinesisConnector, "_consume", fake_consume)


async def _collect(agen):
    return [item async for item in agen]


_MSG = {
    "content": "event-payload",
    "fields": {
        "sequence_number": "49590338271",
        "partition_key": "pk-1",
        "shard": "shardId-000000000000",
        "stream": "events",
        "timestamp": None,
    },
}


async def test_stream_yields_message_nodes(monkeypatch):
    _install_consume(monkeypatch, [_MSG])

    results = await _collect(KinesisConnector().stream("kinesis://events?shard=s", auth=_AWS))

    assert len(results) == 1
    assert isinstance(results[0], Success)
    node = results[0].tree
    assert node.metadata.kind == "message"
    atoms = list(node.iter_atoms())
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].content == "event-payload"
    extra = node.metadata.source_extra["kinesis"]
    assert extra["sequence_number"] == "49590338271"
    assert extra["stream"] == "events"


async def test_transport_failure_is_terminal_transient(monkeypatch):
    _install_consume(monkeypatch, [_MSG], raise_after=True)

    results = await _collect(KinesisConnector().stream("kinesis://events", auth=_AWS))

    assert len(results) == 2
    assert isinstance(results[0], Success)
    assert isinstance(results[1], Error)
    assert results[1].kind == ErrorKind.TRANSIENT


async def test_missing_auth_is_auth_failed(monkeypatch):
    def _explode(self, spec, auth):
        raise AssertionError("consume must not run without auth")

    monkeypatch.setattr(KinesisConnector, "_consume", _explode)

    results = await _collect(KinesisConnector().stream("kinesis://events", auth=None))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.AUTH_FAILED


async def test_non_aws_auth_is_auth_failed(monkeypatch):
    def _explode(self, spec, auth):
        raise AssertionError("consume must not run for non-AWS auth")

    monkeypatch.setattr(KinesisConnector, "_consume", _explode)

    results = await _collect(
        KinesisConnector().stream("kinesis://events", auth=BearerAuth(token="x"))
    )

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.AUTH_FAILED


async def test_bad_uri_is_invalid_input():
    results = await _collect(KinesisConnector().stream("kinesis://", auth=_AWS))

    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.INVALID_INPUT


async def test_fetch_is_unsupported():
    result = await KinesisConnector().fetch("kinesis://events", auth=_AWS)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED


def test_resume_derivation_maps_sequence_number():
    last = build_message_result(
        uri="kinesis://events",
        namespace="kinesis",
        content="x",
        fields={"sequence_number": "49590338271", "stream": "events"},
        counter=SequenceCounter(),
    )

    resumed = _resume_uri("kinesis://events", last, {}, None)

    assert resumed == "kinesis://events?after=49590338271"


def test_can_handle():
    assert KinesisConnector.can_handle("kinesis://events")
    assert not KinesisConnector.can_handle("kafka://topic")
