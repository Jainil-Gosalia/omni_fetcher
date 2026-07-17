"""External-behaviour tests for the v1 ``sse`` connector (v1.4).

No real connection anywhere: a scripted fake raw-line session is injected
through the ``_make_session`` seam, exercising the real SSE wire-format
parser (``data:``/``id:``/blank-line dispatch), and the ``aiohttp``
availability flag is monkeypatched so the suite runs without the
``websockets`` extra installed:

- each dispatched event is one ``Success`` with the contract's node shape
  (kind ``message``, raw ``data:`` payload as a plain Text atom, url/
  handshake_timestamp/sequence/close_code in ``source_extra["sse"]``);
- server-assigned ``id:`` becomes the resume sequence; without it, receipt
  order (seeded by ``?sequence=``) is used;
- auth (``?token=``/``?auth=``) travels through the URI into the spec the
  session factory receives; ``sse://``/``sses://`` map onto http(s);
- connection loss ends the stream with one typed TRANSIENT and always
  closes the session; abandonment closes it too;
- ``fetch()`` is a typed UNSUPPORTED; a missing extra is a typed
  UNSUPPORTED naming it; malformed URIs are INVALID_INPUT.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Optional

import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors import sse as sse_module
from omni_fetcher.v1.connectors.sse import SSEConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Result, Success

pytestmark = pytest.mark.asyncio

URI = "sse://events.example.com/live"


class _FakeSession:
    """Scripted ``_Session`` yielding raw SSE wire-format lines."""

    def __init__(self, lines: List[str], *, fail_after: Optional[int] = None) -> None:
        self._lines = list(lines)
        self._fail_after = fail_after
        self.delivered = 0
        self.closed = False

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        for line in self._lines:
            self.delivered += 1
            yield line
        if self._fail_after is not None and self.delivered >= self._fail_after:
            raise ConnectionError("connection dropped")
        # Exhausted without a scripted failure: block, mirroring a quiet stream.
        await asyncio.sleep(3600)

    async def aclose(self) -> None:
        self.closed = True


def _connector_with(
    fake: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[SSEConnector, list]:
    """A connector whose transport seam returns the scripted fake, recording specs."""
    monkeypatch.setattr(sse_module, "AIOHTTP_AVAILABLE", True)
    connector = SSEConnector()
    specs: list = []

    async def make_session(spec, auth):
        specs.append(spec)
        return fake

    connector._make_session = make_session  # type: ignore[method-assign]
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
# Wire-format parsing + message mapping


async def test_events_map_onto_canonical_message_nodes(monkeypatch) -> None:
    """A dispatched (data-only) event is one Success with the contract's shape."""
    fake = _FakeSession(["data: first payload", "", "data: second payload", ""])
    connector, _ = _connector_with(fake, monkeypatch)

    items = await _collect(connector.stream(URI), 2)

    first, second = items
    assert isinstance(first, Success) and isinstance(second, Success)
    node = first.tree
    assert node.metadata.kind == "message"
    atom = node.find_atoms(AtomKind.TEXT)[0]
    assert atom.content == "first payload"
    # Event data is an arbitrary payload; no syntax is asserted for it.
    assert atom.format == TextFormat.OPAQUE

    extra = node.metadata.source_extra["sse"]
    assert extra["url"] == URI
    assert extra["close_code"] is None
    assert extra["handshake_timestamp"]

    assert second.tree.find_atoms(AtomKind.TEXT)[0].content == "second payload"
    seq_one = first.tree.metadata.temporal.sequence
    seq_two = second.tree.metadata.temporal.sequence
    assert seq_one is not None and seq_two is not None and seq_two > seq_one


async def test_multiline_data_is_joined_with_newlines(monkeypatch) -> None:
    """Multiple data: lines in one event join with '\\n' per the SSE spec."""
    fake = _FakeSession(["data: line one", "data: line two", ""])
    connector, _ = _connector_with(fake, monkeypatch)

    items = await _collect(connector.stream(URI), 1)

    assert items[0].tree.find_atoms(AtomKind.TEXT)[0].content == "line one\nline two"


async def test_comment_lines_are_ignored(monkeypatch) -> None:
    """Lines starting with ':' are comments, not dispatched as events."""
    fake = _FakeSession([": keep-alive", "data: real event", ""])
    connector, _ = _connector_with(fake, monkeypatch)

    items = await _collect(connector.stream(URI), 1)

    assert items[0].tree.find_atoms(AtomKind.TEXT)[0].content == "real event"


# ---------------------------------------------------------------------------
# Sequence: server id vs. receipt order


async def test_server_id_becomes_the_resume_sequence(monkeypatch) -> None:
    """A numeric id: field is used directly as the resume sequence."""
    fake = _FakeSession(["id: 100", "data: a", "", "id: 101", "data: b", ""])
    connector, _ = _connector_with(fake, monkeypatch)

    items = await _collect(connector.stream(URI), 2)

    assert items[0].tree.metadata.source_extra["sse"]["sequence"] == 100
    assert items[1].tree.metadata.source_extra["sse"]["sequence"] == 101


async def test_missing_id_falls_back_to_receipt_order_seeded_by_sequence_param(
    monkeypatch,
) -> None:
    """Without id:, sequence falls back to receipt order, seeded by ?sequence=."""
    fake = _FakeSession(["data: a", "", "data: b", ""])
    connector, _ = _connector_with(fake, monkeypatch)

    items = await _collect(connector.stream(URI + "?sequence=7"), 2)

    assert items[0].tree.metadata.source_extra["sse"]["sequence"] == 7
    assert items[1].tree.metadata.source_extra["sse"]["sequence"] == 8


# ---------------------------------------------------------------------------
# Auth + scheme mapping


async def test_auth_travels_through_the_spec_and_scheme_maps_to_http(
    monkeypatch,
) -> None:
    """?token=/?auth= reach the session factory's spec; sse(s):// maps to http(s)."""
    fake = _FakeSession(["data: a", ""])
    connector, specs = _connector_with(fake, monkeypatch)

    await _collect(connector.stream("sses://events.example.com/live?auth=Bearer+tok"), 1)

    assert specs[0].auth == "Bearer tok"
    assert specs[0].token is None
    assert specs[0].http_uri.startswith("https://events.example.com/live")


# ---------------------------------------------------------------------------
# Failure + cleanup contract


async def test_connection_failure_is_one_transient_and_session_closes(
    monkeypatch,
) -> None:
    """A dropped connection yields one typed TRANSIENT, ends, and closes."""
    fake = _FakeSession(["data: a", ""], fail_after=2)
    connector, _ = _connector_with(fake, monkeypatch)

    items = [item async for item in connector.stream(URI)]

    assert len(items) == 2
    assert isinstance(items[0], Success)
    assert isinstance(items[1], Error)
    assert items[1].kind == ErrorKind.TRANSIENT
    assert fake.closed


async def test_abandoned_stream_closes_the_session(monkeypatch) -> None:
    """Breaking iteration mid-stream closes the session."""
    fake = _FakeSession(["data: a", "", "data: b", ""])
    connector, _ = _connector_with(fake, monkeypatch)

    stream = connector.stream(URI)
    first = await stream.__anext__()  # type: ignore[attr-defined]
    assert isinstance(first, Success)
    await stream.aclose()  # type: ignore[attr-defined]

    assert fake.closed


async def test_connect_failure_is_typed_transient(monkeypatch) -> None:
    """A session-factory failure yields one typed TRANSIENT."""
    monkeypatch.setattr(sse_module, "AIOHTTP_AVAILABLE", True)
    connector = SSEConnector()

    async def make_session(spec, auth):
        raise ConnectionRefusedError("no route to host")

    connector._make_session = make_session  # type: ignore[method-assign]

    items = [item async for item in connector.stream(URI)]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.TRANSIENT


# ---------------------------------------------------------------------------
# Stream-only + gating contract


async def test_fetch_is_typed_unsupported() -> None:
    """fetch() fails fast with UNSUPPORTED, naming stream()."""
    result = await SSEConnector().fetch(URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED
    assert "stream" in result.message


async def test_missing_extra_is_typed_unsupported(monkeypatch) -> None:
    """Without aiohttp, streaming yields one UNSUPPORTED naming the extra."""
    monkeypatch.setattr(sse_module, "AIOHTTP_AVAILABLE", False)

    items = [item async for item in SSEConnector().stream(URI)]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.UNSUPPORTED
    assert "websockets" in items[0].message


async def test_malformed_uri_is_invalid_input(monkeypatch) -> None:
    """An sse:// URI without a host is a typed INVALID_INPUT."""
    monkeypatch.setattr(sse_module, "AIOHTTP_AVAILABLE", True)

    items = [item async for item in SSEConnector().stream("sse://")]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.INVALID_INPUT


async def test_can_handle_sse_and_sses() -> None:
    """can_handle() recognises both sse:// and sses:// schemes."""
    assert SSEConnector.can_handle("sse://host/path")
    assert SSEConnector.can_handle("sses://host/path")
    assert not SSEConnector.can_handle("http://host/path")
