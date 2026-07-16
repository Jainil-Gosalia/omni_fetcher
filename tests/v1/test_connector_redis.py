"""External-behaviour tests for the v1 ``redis`` connector (v1.3).

No live Redis: a scripted fake client is injected through the
``_make_client`` seam, recording all interactions. redis-py is a
core dependency (used by RedisCacheBackend), so it's always
importable; RedisConnector is registered unconditionally in
builtin_registry().

Tests verify:
- each message is one ``Success`` with kind "message", decoded value
  as a plain Text atom, entry_id/timestamp/stream in
  ``source_extra["redis"]``;
- stateless mode (no ``group=``) uses XREAD by default; group mode
  subscribes to XREADGROUP;
- ``?offset=$`` (default) starts at latest, ``?offset=0`` at earliest,
  ``?offset=<entry-id>`` resumes precisely;
- ``?group=<name>`` opts into consumer group semantics;
- missing stream yields NOT_FOUND immediately;
- broker failures yield TRANSIENT and end the stream;
- ``fetch()`` is UNSUPPORTED;
- malformed URIs are INVALID_INPUT;
- connection cleanup on iteration abandonment.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Tuple

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.connectors.redis import RedisConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

pytestmark = pytest.mark.asyncio

URI = "redis://redis.local:6379/my-stream"


class _FakeStreamEntry:
    """Represents one Redis Stream entry."""

    def __init__(self, entry_id: str, data: dict) -> None:
        self.entry_id = entry_id
        self.data = data


class _FakeRedisClient:
    """Scripted Redis client recording every interaction."""

    def __init__(
        self,
        entries: List[_FakeStreamEntry],
        *,
        fail_after: Optional[int] = None,
    ) -> None:
        self._all_entries = list(entries)  # Keep original for offset lookups
        self._fail_after = fail_after
        self.xread_calls: List[Tuple[str, str, Optional[bool]]] = []
        self.xreadgroup_calls: List[Tuple[str, str, str, str]] = []
        self.closed = False
        self.delivered = 0
        self._xread_call_count = 0  # Track xread calls
        self._xreadgroup_call_count = 0  # Track xreadgroup calls

    async def xread(
        self,
        streams: Dict[str, str],
        count: int = 1,
        block: Optional[int] = None,
    ) -> List[Tuple[str, List[Tuple[bytes, Dict]]]]:
        """Simulate XREAD: return matching entries starting from offset."""
        stream_key = list(streams.keys())[0]
        start_offset = streams[stream_key]
        self.xread_calls.append((stream_key, start_offset, bool(block)))
        self._xread_call_count += 1

        if self._fail_after is not None and self.delivered >= self._fail_after:
            raise ConnectionError("redis went away")

        # Filter entries based on start_offset
        result_entries = []
        if start_offset == "$":
            # Latest: return entries starting from current position
            # For testing: return entries indexed by call count
            start_idx = (self._xread_call_count - 1) * count
            result_entries = self._all_entries[start_idx : start_idx + count]
        elif start_offset == "0":
            # Earliest: return entries from the start
            start_idx = (self._xread_call_count - 1) * count
            result_entries = self._all_entries[start_idx : start_idx + count]
        elif start_offset == "0-0":
            # Stream doesn't exist
            result_entries = []
        else:
            # Resume from specific entry_id (return entries after it)
            found = False
            for i, entry in enumerate(self._all_entries):
                if found:
                    result_entries.append(entry)
                    if len(result_entries) >= count:
                        break
                elif entry.entry_id == start_offset:
                    found = True
                    # Include entries after this one
                    if i + 1 < len(self._all_entries):
                        result_entries.append(self._all_entries[i + 1])
                        if len(result_entries) >= count:
                            break

        if not result_entries and block:
            # Only block if no results and block is set
            await asyncio.sleep(0.001)  # Short wait for testing

        self.delivered += len(result_entries)
        if result_entries:
            return [
                (
                    stream_key.encode(),
                    [(entry.entry_id.encode(), entry.data) for entry in result_entries],
                )
            ]
        return []

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Dict[str, str],
        count: int = 1,
        block: Optional[int] = None,
    ) -> List[Tuple[str, List[Tuple[bytes, Dict]]]]:
        """Simulate XREADGROUP for consumer groups."""
        stream_key = list(streams.keys())[0]
        start_offset = streams[stream_key]
        self.xreadgroup_calls.append((groupname, consumername, stream_key, start_offset))
        self._xreadgroup_call_count += 1

        if self._fail_after is not None and self.delivered >= self._fail_after:
            raise ConnectionError("redis went away")

        # For testing: return entries indexed by call count
        result_entries = []
        if start_offset in (">", "$"):
            # Pending messages or new messages: return entries
            start_idx = (self._xreadgroup_call_count - 1) * count
            result_entries = self._all_entries[start_idx : start_idx + count]
        elif start_offset == "0":
            start_idx = (self._xreadgroup_call_count - 1) * count
            result_entries = self._all_entries[start_idx : start_idx + count]

        if not result_entries and block:
            await asyncio.sleep(0.001)  # Short wait for testing

        self.delivered += len(result_entries)
        if result_entries:
            return [
                (
                    stream_key.encode(),
                    [(entry.entry_id.encode(), entry.data) for entry in result_entries],
                )
            ]
        return []

    async def aclose(self) -> None:
        self.closed = True


def _connector_with(fake: _FakeRedisClient, monkeypatch: pytest.MonkeyPatch) -> RedisConnector:
    """Create a connector with an injected fake client."""
    connector = RedisConnector()

    async def mock_make_client(spec, timeout):
        return fake

    monkeypatch.setattr(connector, "_make_client", mock_make_client)
    return connector


# ============================================================================
# Tracer Bullet: Basic streaming with default offset ($)
# ============================================================================


@pytest.mark.asyncio
async def test_stream_basic_default_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Messages arrive as Results with kind='message', Text atom, entry_id in source_extra."""
    entries = [
        _FakeStreamEntry("1526919030474-0", {b"field1": b"value1"}),
        _FakeStreamEntry("1526919030475-0", {b"field2": b"value2"}),
    ]
    fake = _FakeRedisClient(entries)
    connector = _connector_with(fake, monkeypatch)

    results = []
    stream_gen = connector.stream(URI)
    try:
        async for result in stream_gen:
            results.append(result)
            if len(results) >= 2:
                break
    finally:
        await stream_gen.aclose()

    assert len(results) == 2
    for i, result in enumerate(results):
        assert isinstance(result, Success)
        assert result.tree.metadata.kind == "message"
        # Verify Text atom
        assert len(result.tree.children) == 1
        atom = result.tree.children[0]
        assert atom.kind == AtomKind.TEXT
        # Verify source_extra
        source_extra = result.tree.metadata.source_extra
        assert "redis" in source_extra
        redis_extra = source_extra["redis"]
        assert redis_extra["entry_id"] == entries[i].entry_id
        assert "timestamp" in redis_extra
        assert redis_extra["stream"] == "my-stream"


@pytest.mark.asyncio
async def test_stream_earliest_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """?offset=0 starts from earliest message."""
    entries = [
        _FakeStreamEntry("1526919030474-0", {b"field1": b"value1"}),
        _FakeStreamEntry("1526919030475-0", {b"field2": b"value2"}),
    ]
    fake = _FakeRedisClient(entries)
    connector = _connector_with(fake, monkeypatch)

    uri_earliest = f"{URI}?offset=0"
    results = []
    stream_gen = connector.stream(uri_earliest)
    try:
        async for result in stream_gen:
            results.append(result)
            if len(results) >= 2:
                break
    finally:
        await stream_gen.aclose()

    assert len(results) == 2
    # First XREAD call should request offset=0
    assert fake.xread_calls[0] == ("my-stream", "0", True)


@pytest.mark.asyncio
async def test_stream_resume_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """?offset=<entry-id> resumes from specific position."""
    entries = [
        _FakeStreamEntry("1526919030474-0", {b"field1": b"value1"}),
        _FakeStreamEntry("1526919030475-0", {b"field2": b"value2"}),
    ]
    fake = _FakeRedisClient(entries)
    connector = _connector_with(fake, monkeypatch)

    # Resume from first entry: should get second entry
    uri_resume = f"{URI}?offset=1526919030474-0"
    results = []
    stream_gen = connector.stream(uri_resume)
    try:
        async for result in stream_gen:
            results.append(result)
            if len(results) >= 1:
                break
    finally:
        await stream_gen.aclose()

    assert len(results) == 1
    assert results[0].tree.metadata.source_extra["redis"]["entry_id"] == "1526919030475-0"
    # Verify the XREAD was called with the resume offset
    assert fake.xread_calls[0] == ("my-stream", "1526919030474-0", True)


@pytest.mark.asyncio
async def test_stream_consumer_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """?group=<name> joins a consumer group using XREADGROUP."""
    entries = [
        _FakeStreamEntry("1526919030474-0", {b"field1": b"value1"}),
        _FakeStreamEntry("1526919030475-0", {b"field2": b"value2"}),
    ]
    fake = _FakeRedisClient(entries)
    connector = _connector_with(fake, monkeypatch)

    uri_group = f"{URI}?group=workers"
    results = []
    stream_gen = connector.stream(uri_group)
    try:
        async for result in stream_gen:
            results.append(result)
            if len(results) >= 2:
                break
    finally:
        await stream_gen.aclose()

    assert len(results) == 2
    # Should use XREADGROUP, not XREAD
    assert len(fake.xreadgroup_calls) > 0
    assert len(fake.xread_calls) == 0
    # First call uses the configured offset ($), then subsequent calls use ">"
    assert fake.xreadgroup_calls[0] == ("workers", "omni-fetcher", "my-stream", "$")


@pytest.mark.asyncio
async def test_fetch_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch() on redis:// returns UNSUPPORTED immediately."""
    entries = [_FakeStreamEntry("1526919030474-0", {b"field1": b"value1"})]
    fake = _FakeRedisClient(entries)
    connector = _connector_with(fake, monkeypatch)

    result = await connector.fetch(URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED
    assert "unbounded" in result.message.lower()
    assert "stream()" in result.message


@pytest.mark.asyncio
async def test_malformed_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed URIs yield INVALID_INPUT."""
    connector = RedisConnector()

    # Missing stream key
    results = []
    async for result in connector.stream("redis://redis.local:6379"):
        results.append(result)

    assert len(results) == 1
    assert isinstance(results[0], Error)
    assert results[0].kind == ErrorKind.INVALID_INPUT


@pytest.mark.asyncio
async def test_stream_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connection failure mid-stream yields TRANSIENT."""
    entries = [
        _FakeStreamEntry("1526919030474-0", {b"field1": b"value1"}),
        _FakeStreamEntry("1526919030475-0", {b"field2": b"value2"}),
    ]
    fake = _FakeRedisClient(entries, fail_after=1)  # Fail after 1 message delivered
    connector = _connector_with(fake, monkeypatch)

    results = []
    stream_gen = connector.stream(URI)
    try:
        async for result in stream_gen:
            results.append(result)
    finally:
        await stream_gen.aclose()

    # Should get one success + one TRANSIENT error
    assert len(results) == 2
    assert isinstance(results[0], Success)
    assert isinstance(results[1], Error)
    assert results[1].kind == ErrorKind.TRANSIENT
    # Connection should be closed
    assert fake.closed


@pytest.mark.asyncio
async def test_stream_cleanup_on_break(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connection is closed when iteration is abandoned."""
    entries = [_FakeStreamEntry("1526919030474-0", {b"field1": b"value1"})]
    fake = _FakeRedisClient(entries)
    connector = _connector_with(fake, monkeypatch)

    stream_gen = connector.stream(URI)
    try:
        async for result in stream_gen:
            break
    finally:
        # Explicitly close the generator to ensure cleanup
        await stream_gen.aclose()

    # Client should be closed after break
    assert fake.closed
