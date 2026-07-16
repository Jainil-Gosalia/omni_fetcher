"""External-behaviour tests for the v1 ``websocket`` connector (v1.4).

No real socket anywhere: a scripted fake connection is injected through the
``_make_connection`` seam (recording the spec it was built from and every
close), and the ``websockets`` availability flag is monkeypatched so the
suite runs without the ``websockets`` extra installed:

- each message is one ``Success`` with the contract's node shape (kind
  ``message``, raw payload as a plain Text atom, url/handshake_timestamp/
  sequence/close_code in ``source_extra["websocket"]``);
- auth (``?token=``/``?auth=``) and resume (``?sequence=``) travel through
  the URI into the spec the connection factory receives;
- connection loss ends the stream with one typed TRANSIENT and always
  closes the connection; abandonment closes it too;
- ``fetch()`` is a typed UNSUPPORTED; a missing extra is a typed
  UNSUPPORTED naming it; malformed URIs are INVALID_INPUT.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, List, Optional

import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors import websocket as websocket_module
from omni_fetcher.v1.connectors.websocket import WebSocketConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Result, Success

pytestmark = pytest.mark.asyncio

URI = "ws://live.example.com/events"


class _FakeConnection:
    """Scripted ``_Connection`` recording every interaction."""

    def __init__(
        self,
        payloads: List[Any],
        *,
        fail_after: Optional[int] = None,
        fail_close_code: int = 1006,
    ) -> None:
        self._payloads = list(payloads)
        self._fail_after = fail_after
        self._fail_close_code = fail_close_code
        self.delivered = 0
        self.closed = False
        self.close_code: Optional[int] = None

    async def recv(self) -> Any:
        if self._fail_after is not None and self.delivered >= self._fail_after:
            self.close_code = self._fail_close_code
            raise ConnectionError("connection dropped")
        if not self._payloads:
            await asyncio.sleep(3600)  # a quiet socket blocks forever
        self.delivered += 1
        return self._payloads.pop(0)

    async def close(self) -> None:
        self.closed = True


def _connector_with(
    fake: _FakeConnection, monkeypatch: pytest.MonkeyPatch
) -> tuple[WebSocketConnector, list]:
    """A connector whose socket seam returns the scripted fake, recording specs."""
    monkeypatch.setattr(websocket_module, "WEBSOCKETS_AVAILABLE", True)
    connector = WebSocketConnector()
    specs: list = []

    async def make_connection(spec, auth):
        specs.append(spec)
        return fake

    connector._make_connection = make_connection  # type: ignore[method-assign]
    return connector, specs


async def _collect(stream: AsyncIterator[Result], count: int) -> list[Result]:
    items: list[Result] = []

    async def _run() -> None:
        async for item in stream:
            items.append(item)
            if len(items) >= count:
                break

    try:
        await asyncio.wait_for(_run(), timeout=8.0)
    finally:
        await stream.aclose()  # type: ignore[attr-defined]
    return items


# ---------------------------------------------------------------------------
# Message mapping


async def test_messages_map_onto_canonical_message_nodes(monkeypatch) -> None:
    """Each payload is one Success with the contract's node shape."""
    fake = _FakeConnection(["first payload", b"second payload"])
    connector, _ = _connector_with(fake, monkeypatch)

    items = await _collect(connector.stream(URI), 2)

    first, second = items
    assert isinstance(first, Success) and isinstance(second, Success)
    node = first.tree
    assert node.metadata.kind == "message"
    atom = node.find_atoms(AtomKind.TEXT)[0]
    assert atom.content == "first payload"
    assert atom.format == TextFormat.PLAIN

    extra = node.metadata.source_extra["websocket"]
    assert extra["url"] == URI
    assert extra["sequence"] == 0
    assert extra["close_code"] is None
    assert extra["handshake_timestamp"]

    assert second.tree.find_atoms(AtomKind.TEXT)[0].content == "second payload"
    assert second.tree.metadata.source_extra["websocket"]["sequence"] == 1
    seq_one = first.tree.metadata.temporal.sequence
    seq_two = second.tree.metadata.temporal.sequence
    assert seq_one is not None and seq_two is not None and seq_two > seq_one


# ---------------------------------------------------------------------------
# Auth + resume via URI


async def test_auth_and_sequence_travel_through_the_spec(monkeypatch) -> None:
    """?token=/?auth=/?sequence= reach the connection factory's spec."""
    fake = _FakeConnection(["x"])
    connector, specs = _connector_with(fake, monkeypatch)

    await _collect(connector.stream(URI + "?token=abc123&sequence=5"), 1)

    assert specs[0].token == "abc123"
    assert specs[0].auth is None
    assert specs[0].sequence == 5


async def test_sequence_param_seeds_the_counter(monkeypatch) -> None:
    """?sequence=<n> seeds the first message's sequence number."""
    fake = _FakeConnection(["x", "y"])
    connector, _ = _connector_with(fake, monkeypatch)

    items = await _collect(connector.stream(URI + "?sequence=42"), 2)

    assert items[0].tree.metadata.source_extra["websocket"]["sequence"] == 42
    assert items[1].tree.metadata.source_extra["websocket"]["sequence"] == 43


# ---------------------------------------------------------------------------
# Failure + cleanup contract


async def test_connection_failure_is_one_transient_and_connection_closes(
    monkeypatch,
) -> None:
    """A recv error yields one typed TRANSIENT, ends, and closes the socket."""
    fake = _FakeConnection(["a"], fail_after=1)
    connector, _ = _connector_with(fake, monkeypatch)

    items = [item async for item in connector.stream(URI)]

    assert len(items) == 2
    assert isinstance(items[0], Success)
    assert isinstance(items[1], Error)
    assert items[1].kind == ErrorKind.TRANSIENT
    assert "1006" in (items[1].message or "")
    assert fake.closed


async def test_clean_close_ends_the_stream_without_an_error(monkeypatch) -> None:
    """A close under RFC 6455 code 1000/1001 ends the stream with no Error."""
    fake = _FakeConnection(["a", "b"], fail_after=2, fail_close_code=1000)
    connector, _ = _connector_with(fake, monkeypatch)

    items = [item async for item in connector.stream(URI)]

    assert len(items) == 2
    assert all(isinstance(item, Success) for item in items)
    assert fake.closed


@pytest.mark.parametrize("close_code", [1000, 1001])
async def test_going_away_close_also_ends_cleanly(monkeypatch, close_code) -> None:
    """Both normal-closure (1000) and going-away (1001) are clean ends."""
    fake = _FakeConnection(["a"], fail_after=1, fail_close_code=close_code)
    connector, _ = _connector_with(fake, monkeypatch)

    items = [item async for item in connector.stream(URI)]

    assert len(items) == 1
    assert isinstance(items[0], Success)


async def test_abandoned_stream_closes_the_connection(monkeypatch) -> None:
    """Breaking iteration mid-stream closes the connection."""
    fake = _FakeConnection(["a", "b"])
    connector, _ = _connector_with(fake, monkeypatch)

    stream = connector.stream(URI)
    first = await stream.__anext__()  # type: ignore[attr-defined]
    assert isinstance(first, Success)
    await stream.aclose()  # type: ignore[attr-defined]

    assert fake.closed


async def test_connect_failure_is_typed_transient(monkeypatch) -> None:
    """A connection-factory failure yields one typed TRANSIENT."""
    monkeypatch.setattr(websocket_module, "WEBSOCKETS_AVAILABLE", True)
    connector = WebSocketConnector()

    async def make_connection(spec, auth):
        raise ConnectionRefusedError("no route to host")

    connector._make_connection = make_connection  # type: ignore[method-assign]

    items = [item async for item in connector.stream(URI)]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.TRANSIENT


# ---------------------------------------------------------------------------
# Stream-only + gating contract


async def test_fetch_is_typed_unsupported() -> None:
    """fetch() fails fast with UNSUPPORTED, naming stream()."""
    result = await WebSocketConnector().fetch(URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED
    assert "stream" in result.message


async def test_missing_extra_is_typed_unsupported(monkeypatch) -> None:
    """Without websockets, streaming yields one UNSUPPORTED naming the extra."""
    monkeypatch.setattr(websocket_module, "WEBSOCKETS_AVAILABLE", False)

    items = [item async for item in WebSocketConnector().stream(URI)]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.UNSUPPORTED
    assert "websockets" in items[0].message


async def test_malformed_uri_is_invalid_input(monkeypatch) -> None:
    """A ws:// URI without a host is a typed INVALID_INPUT."""
    monkeypatch.setattr(websocket_module, "WEBSOCKETS_AVAILABLE", True)

    items = [item async for item in WebSocketConnector().stream("ws://")]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.INVALID_INPUT


async def test_non_integer_sequence_is_invalid_input(monkeypatch) -> None:
    """?sequence= must be an integer."""
    monkeypatch.setattr(websocket_module, "WEBSOCKETS_AVAILABLE", True)

    items = [item async for item in WebSocketConnector().stream(URI + "?sequence=nope")]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.INVALID_INPUT


async def test_can_handle_ws_and_wss() -> None:
    """can_handle() recognises both ws:// and wss:// schemes."""
    assert WebSocketConnector.can_handle("ws://host/path")
    assert WebSocketConnector.can_handle("wss://host/path")
    assert not WebSocketConnector.can_handle("http://host/path")
