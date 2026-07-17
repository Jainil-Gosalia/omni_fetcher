"""External-behaviour tests for the v1 ``postgres-cdc`` connector (v1.6).

No live PostgreSQL anywhere: a scripted fake replication stream is injected
through the ``_make_replication_stream`` seam (recording slot creation,
drops, and closes), and the asyncpg availability flag is monkeypatched so
the suite runs without the ``postgres`` extra installed:

- each row change is one ``Success`` with the contract's node shape (kind
  ``change``, one JSON Text atom ``{op, table, new, old, lsn, timestamp,
  xid}``, table/operation/lsn/timestamp/xid/slot in
  ``source_extra["postgres"]``);
- INSERT carries ``new``, DELETE carries ``old``, UPDATE carries both;
- the replication slot is created on stream entry (generated
  ``omni_fetcher_*`` name, or ``?slot=`` verbatim), dropped on clean
  abandonment, and *kept* after a transport failure -- it is the resume
  pointer for ``stream_with_restart``, whose derived ``?slot=`` continues
  from the slot's confirmed flush position;
- connection failures end the stream with one typed TRANSIENT and close
  the connection; ``fetch()`` is a typed UNSUPPORTED; a missing extra is a
  typed UNSUPPORTED naming it; malformed URIs are INVALID_INPUT.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors import postgres_cdc as postgres_module
from omni_fetcher.v1.connectors.postgres_cdc import PostgresCDCConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Result, Success
from omni_fetcher.v1.retry import RetryPolicy, stream_with_restart

pytestmark = pytest.mark.asyncio

URI = "postgres-cdc://db.example.com:5432/mydb"


class _FakeChange:
    def __init__(
        self,
        op: str,
        table: str = "public.users",
        *,
        new: Optional[Dict[str, Any]] = None,
        old: Optional[Dict[str, Any]] = None,
        lsn: str = "0/16B2D80",
        timestamp: str = "2026-07-16T10:00:00+00:00",
        xid: int = 771,
    ) -> None:
        self.op = op
        self.table = table
        self.new = new
        self.old = old
        self.lsn = lsn
        self.timestamp = timestamp
        self.xid = xid


class _FakeReplicationStream:
    """Scripted ``_ReplicationStream`` recording every interaction."""

    def __init__(
        self,
        changes: List[_FakeChange],
        *,
        fail_after: Optional[int] = None,
        fail_on_create: bool = False,
    ) -> None:
        self._changes = list(changes)
        self._fail_after = fail_after
        self._fail_on_create = fail_on_create
        self.created_slots: List[str] = []
        self.dropped_slots: List[str] = []
        self.closed = False
        self.delivered = 0

    async def create_slot(self, name: str) -> None:
        if self._fail_on_create:
            raise ConnectionError("could not create replication slot")
        self.created_slots.append(name)

    async def next_change(self) -> _FakeChange:
        if self._fail_after is not None and self.delivered >= self._fail_after:
            raise ConnectionError("server closed the connection unexpectedly")
        if not self._changes:
            await asyncio.sleep(3600)  # a quiet database blocks forever
        self.delivered += 1
        return self._changes.pop(0)

    async def drop_slot(self, name: str) -> None:
        self.dropped_slots.append(name)

    async def close(self) -> None:
        self.closed = True


def _connector_with(
    fake: _FakeReplicationStream, monkeypatch: pytest.MonkeyPatch
) -> PostgresCDCConnector:
    """A connector whose database seam returns the scripted fake."""
    monkeypatch.setattr(postgres_module, "ASYNCPG_AVAILABLE", True)
    connector = PostgresCDCConnector()

    async def make_stream(spec, auth):
        return fake

    connector._make_replication_stream = make_stream  # type: ignore[method-assign]
    return connector


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
# Change mapping


async def test_changes_map_onto_canonical_change_nodes(monkeypatch) -> None:
    """Each row change is one Success with the contract's node shape."""
    fake = _FakeReplicationStream(
        [
            _FakeChange("INSERT", new={"id": "1", "name": "ada"}),
            _FakeChange("INSERT", new={"id": "2", "name": "bob"}, lsn="0/16B2E10"),
        ]
    )
    connector = _connector_with(fake, monkeypatch)

    items = await _collect(connector.stream(URI + "?slot=my_slot"), 2)

    first, second = items
    assert isinstance(first, Success) and isinstance(second, Success)
    node = first.tree
    assert node.metadata.kind == "change"
    atom = node.find_atoms(AtomKind.TEXT)[0]
    assert atom.format == TextFormat.CODE
    record = json.loads(atom.content)
    assert record == {
        "op": "INSERT",
        "table": "public.users",
        "new": {"id": "1", "name": "ada"},
        "old": None,
        "lsn": "0/16B2D80",
        "timestamp": "2026-07-16T10:00:00+00:00",
        "xid": 771,
    }

    extra = node.metadata.source_extra["postgres"]
    assert extra["table"] == "public.users"
    assert extra["operation"] == "INSERT"
    assert extra["lsn"] == "0/16B2D80"
    assert extra["timestamp"] == "2026-07-16T10:00:00+00:00"
    assert extra["xid"] == 771
    assert extra["slot"] == "my_slot"

    seq_one = first.tree.metadata.temporal.sequence
    seq_two = second.tree.metadata.temporal.sequence
    assert seq_one is not None and seq_two is not None and seq_two > seq_one


async def test_all_change_types_preserve_old_and_new(monkeypatch) -> None:
    """INSERT carries new, UPDATE carries old+new, DELETE carries old."""
    fake = _FakeReplicationStream(
        [
            _FakeChange("INSERT", new={"id": "1"}),
            _FakeChange("UPDATE", new={"id": "1", "name": "ada"}, old={"id": "1", "name": "ad"}),
            _FakeChange("DELETE", old={"id": "1"}),
        ]
    )
    connector = _connector_with(fake, monkeypatch)

    items = await _collect(connector.stream(URI), 3)

    records = [json.loads(item.tree.find_atoms(AtomKind.TEXT)[0].content) for item in items]
    assert [record["op"] for record in records] == ["INSERT", "UPDATE", "DELETE"]
    assert records[0]["new"] == {"id": "1"} and records[0]["old"] is None
    assert records[1]["old"] == {"id": "1", "name": "ad"}
    assert records[1]["new"] == {"id": "1", "name": "ada"}
    assert records[2]["new"] is None and records[2]["old"] == {"id": "1"}


# ---------------------------------------------------------------------------
# Slot lifecycle (D2)


async def test_slot_is_created_on_entry_with_generated_name(monkeypatch) -> None:
    """Without ?slot=, a fresh omni_fetcher_* slot is created on entry."""
    fake = _FakeReplicationStream([_FakeChange("INSERT", new={"id": "1"})])
    connector = _connector_with(fake, monkeypatch)

    await _collect(connector.stream(URI), 1)

    assert len(fake.created_slots) == 1
    assert fake.created_slots[0].startswith("omni_fetcher_")


async def test_custom_slot_name_is_used_verbatim(monkeypatch) -> None:
    """?slot= names the replication slot exactly."""
    fake = _FakeReplicationStream([_FakeChange("INSERT", new={"id": "1"})])
    connector = _connector_with(fake, monkeypatch)

    await _collect(connector.stream(URI + "?slot=audit_feed"), 1)

    assert fake.created_slots == ["audit_feed"]


async def test_abandoned_stream_drops_the_slot_and_closes(monkeypatch) -> None:
    """Breaking iteration mid-stream drops the slot (try/finally contract)."""
    fake = _FakeReplicationStream(
        [_FakeChange("INSERT", new={"id": "1"}), _FakeChange("INSERT", new={"id": "2"})]
    )
    connector = _connector_with(fake, monkeypatch)

    stream = connector.stream(URI + "?slot=my_slot")
    first = await stream.__anext__()  # type: ignore[attr-defined]
    assert isinstance(first, Success)
    await stream.aclose()  # type: ignore[attr-defined]

    assert fake.dropped_slots == ["my_slot"]
    assert fake.closed


# ---------------------------------------------------------------------------
# Failure contract (D10) + slot-based resume (D7)


async def test_connection_failure_is_one_transient_and_keeps_the_slot(
    monkeypatch,
) -> None:
    """A replication error yields one typed TRANSIENT, closes, keeps the slot.

    The surviving slot's confirmed_flush_lsn is the resume pointer -- a
    dropped connection must not destroy it (D7).
    """
    fake = _FakeReplicationStream([_FakeChange("INSERT", new={"id": "1"})], fail_after=1)
    connector = _connector_with(fake, monkeypatch)

    items = [item async for item in connector.stream(URI)]

    assert len(items) == 2
    assert isinstance(items[0], Success)
    assert isinstance(items[1], Error)
    assert items[1].kind == ErrorKind.TRANSIENT
    assert fake.dropped_slots == []
    assert fake.closed


async def test_failed_connect_is_one_transient(monkeypatch) -> None:
    """A connection that cannot be established is one typed TRANSIENT."""
    monkeypatch.setattr(postgres_module, "ASYNCPG_AVAILABLE", True)
    connector = PostgresCDCConnector()

    async def make_stream(spec, auth):
        raise ConnectionError("connection refused")

    connector._make_replication_stream = make_stream  # type: ignore[method-assign]

    items = [item async for item in connector.stream(URI)]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.TRANSIENT


async def test_slot_creation_failure_is_one_transient(monkeypatch) -> None:
    """A slot that cannot be created ends the stream with one TRANSIENT."""
    fake = _FakeReplicationStream([], fail_on_create=True)
    connector = _connector_with(fake, monkeypatch)

    items = [item async for item in connector.stream(URI)]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.TRANSIENT
    assert fake.closed


async def test_stream_with_restart_resumes_the_same_slot(monkeypatch) -> None:
    """After a TRANSIENT end, the restart reuses the slot via derived ?slot=.

    A shared server-side script hands each successive connection the
    remaining changes -- exactly what a real slot's confirmed_flush_lsn
    guarantees -- so the restarted stream sees no duplicates and no gaps.
    """
    monkeypatch.setattr(postgres_module, "ASYNCPG_AVAILABLE", True)
    connector = PostgresCDCConnector()

    server_changes = [
        _FakeChange("INSERT", new={"id": "1"}),
        _FakeChange("INSERT", new={"id": "2"}),
        _FakeChange("INSERT", new={"id": "3"}),
    ]
    fakes: List[_FakeReplicationStream] = []
    uris: List[str] = []

    original_stream = connector.stream

    def recording_stream(uri: str, **kwargs):
        uris.append(uri)
        return original_stream(uri, **kwargs)

    connector.stream = recording_stream  # type: ignore[method-assign]

    async def make_stream(spec, auth):
        # First connection dies after two changes; the second serves only
        # what the slot has not yet confirmed (the remaining changes).
        if not fakes:
            fake = _FakeReplicationStream(server_changes, fail_after=2)
        else:
            fake = _FakeReplicationStream(server_changes[fakes[0].delivered :])
        fakes.append(fake)
        return fake

    connector._make_replication_stream = make_stream  # type: ignore[method-assign]

    async def _sleep(_: float) -> None:
        return None

    policy = RetryPolicy(max_attempts=2, initial_delay=0.0)
    stream = stream_with_restart(connector, URI, policy=policy, sleep=_sleep)
    items = await _collect(stream, 3)

    assert all(isinstance(item, Success) for item in items)
    ids = [
        json.loads(item.tree.find_atoms(AtomKind.TEXT)[0].content)["new"]["id"] for item in items
    ]
    assert ids == ["1", "2", "3"]  # no duplicates, no gaps

    slot = fakes[0].created_slots[0]
    assert len(fakes) == 2
    assert f"slot={slot}" in uris[1]  # restart derived the same slot
    assert fakes[1].created_slots == [slot]  # ...and reattached to it


# ---------------------------------------------------------------------------
# Stream-only + gating contract (D12)


async def test_fetch_is_typed_unsupported() -> None:
    """fetch() fails fast with UNSUPPORTED, naming stream()."""
    result = await PostgresCDCConnector().fetch(URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED
    assert "stream" in result.message


async def test_missing_extra_is_typed_unsupported(monkeypatch) -> None:
    """Without asyncpg, streaming yields one UNSUPPORTED naming the extra."""
    monkeypatch.setattr(postgres_module, "ASYNCPG_AVAILABLE", False)

    items = [item async for item in PostgresCDCConnector().stream(URI)]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.UNSUPPORTED
    assert "postgres" in items[0].message


@pytest.mark.parametrize(
    "bad_uri",
    [
        "postgres-cdc://host-only",
        "postgres-cdc:///mydb",
        "postgres-cdc://host:abc/mydb",
        "postgres-cdc://host/db/extra",
    ],
)
async def test_malformed_uri_is_invalid_input(monkeypatch, bad_uri: str) -> None:
    """A postgres-cdc:// URI without host/database is a typed INVALID_INPUT."""
    monkeypatch.setattr(postgres_module, "ASYNCPG_AVAILABLE", True)

    items = [item async for item in PostgresCDCConnector().stream(bad_uri)]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.INVALID_INPUT


async def test_can_handle_only_claims_the_scheme() -> None:
    """can_handle is scheme-scoped; plain postgres:// is not claimed."""
    assert PostgresCDCConnector.can_handle("postgres-cdc://db.example.com/mydb")
    assert not PostgresCDCConnector.can_handle("postgres://db.example.com/mydb")
    assert not PostgresCDCConnector.can_handle("https://example.com")
