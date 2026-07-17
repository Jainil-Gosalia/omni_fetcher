"""The canonical ``postgres-cdc`` connector -- PostgreSQL change stream for v1.

Consumes PostgreSQL logical replication through the v1 contract: each row
change (INSERT / UPDATE / DELETE) is one ``Result`` whose tree is a single
``kind="change"`` node carrying one ``Text`` atom of JSON --
``{op, table, new, old, lsn, timestamp, xid}`` -- plus the same facts in
``source_extra["postgres"]`` (table, operation, lsn, timestamp, xid, slot).

Configuration travels in the URI: ``postgres-cdc://<host>[:<port>]/<database>``
(default port 5432) with ``?slot=<name>`` naming the replication slot (a
``omni_fetcher_<uuid>`` name is generated when omitted) and ``?user=`` /
``?password=`` for credentials. The connector manages the slot itself (D2):
it is created (or reused) on ``stream()`` entry and dropped when iteration
ends cleanly or is abandoned. After a transport failure the slot is
deliberately *kept* -- its ``confirmed_flush_lsn`` is the resume pointer, so
``stream_with_restart`` reconnects to the same slot (its ``?slot=`` is
derived from the last item's ``source_extra``) and continues where the WAL
left off (D7). A slot abandoned after restarts are exhausted must be dropped
host-side (``SELECT pg_drop_replication_slot(...)``).

asyncpg is optional (the ``postgres`` extra): this module imports without
it, ``builtin_registry()`` skips the source when it is missing, and direct
use yields a typed ``UNSUPPORTED`` naming the extra. All database access
flows through a narrow replication-stream protocol built by the
``_make_replication_stream`` seam, so tests script a fake and never touch a
live PostgreSQL.

Stream-only: ``fetch()`` returns a typed ``UNSUPPORTED`` immediately. The
stream begins at the current WAL position (no initial snapshot, D11);
transport failures mid-stream yield one terminal ``TRANSIENT``.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from typing import Any, AsyncIterator, Dict, Optional, Protocol
from urllib.parse import parse_qs

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import (
    SequenceCounter,
    build_node,
    now_utc,
    stamp_temporal,
)
from omni_fetcher.v1.result import Result, error, from_exception, success
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which all descriptive ``postgres`` fields are stored.
SOURCE_NAMESPACE = "postgres"

# Advisory semantic ``kind`` for every node this connector emits.
CHANGE_KIND = "change"

# Whether the optional asyncpg client is importable (the ``postgres`` extra).
ASYNCPG_AVAILABLE = importlib.util.find_spec("asyncpg") is not None

_SCHEME = "postgres-cdc://"
_DEFAULT_PORT = 5432

# Prefix for connector-generated replication slot names (D2).
_SLOT_PREFIX = "omni_fetcher_"


class _Change(Protocol):
    """The decoded row change the replication stream yields.

    ``op`` is ``"INSERT"`` / ``"UPDATE"`` / ``"DELETE"``; ``table`` is
    ``schema.table``; ``new`` / ``old`` are column maps (``old`` is present
    for UPDATE/DELETE when the replica identity provides it).
    """

    op: str
    table: str
    new: Optional[Dict[str, Any]]
    old: Optional[Dict[str, Any]]
    lsn: str
    timestamp: str
    xid: Optional[int]


class _ReplicationStream(Protocol):
    """
    The narrow replication protocol the stream drives
    ===============================================
    Implemented by the production asyncpg adapter and by test fakes; the
    stream itself never touches asyncpg directly, so unit tests need no
    live PostgreSQL.
    ===============================================
    NOTE:
        1. ``create_slot`` must *reuse* an existing slot of the same name
           (resume continues from its ``confirmed_flush_lsn``, D7) rather
           than fail on a duplicate.

    Methods
    -------
        create_slot:
        next_change:
        drop_slot:
        close:
    """

    async def create_slot(self, name: str) -> None: ...

    async def next_change(self) -> _Change: ...

    async def drop_slot(self, name: str) -> None: ...

    async def close(self) -> None: ...


class _PostgresCDCSpec:
    """Parsed ``postgres-cdc://`` routing decision."""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        slot: Optional[str],
        user: Optional[str],
        password: Optional[str],
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.slot = slot
        self.user = user
        self.password = password


def _parse_uri(uri: str) -> _PostgresCDCSpec:
    """Parse a ``postgres-cdc://`` URI into a spec, raising ``ValueError`` when bad."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a postgres-cdc:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    location, _, query = remainder.partition("?")
    host_part, _, database = location.partition("/")
    if not host_part or not database or "/" in database:
        raise ValueError(f"postgres-cdc:// URI must be postgres-cdc://host[:port]/database: {uri}")

    if ":" in host_part:
        host, port_text = host_part.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            raise ValueError(f"postgres-cdc:// port must be numeric: {port_text}")
    else:
        host = host_part
        port = _DEFAULT_PORT

    params = parse_qs(query)
    return _PostgresCDCSpec(
        host=host,
        port=port,
        database=database,
        slot=params.get("slot", [None])[0],
        user=params.get("user", [None])[0],
        password=params.get("password", [None])[0],
    )


class PostgresCDCConnector(BaseFetcher):
    """
    PostgreSQL change-data-capture connector for the v1 contract
    ===============================================
    Streams row-level changes from logical replication as canonical
    per-item ``Result``s (one ``kind="change"`` node per INSERT / UPDATE /
    DELETE, D5). The replication slot is connector-managed: created (or
    reused) on ``stream()`` entry, dropped on clean end or abandonment,
    kept after a transport failure so a restart resumes from its
    ``confirmed_flush_lsn`` (D2/D7). ``fetch()`` is a typed
    ``UNSUPPORTED``. All database access goes through the
    ``_make_replication_stream`` seam.
    ===============================================
    NOTE:
        1. Without the ``postgres`` extra the stream yields one typed
           ``UNSUPPORTED`` naming the extra; nothing raises.
        2. The stream starts at the current WAL position -- no initial
           table snapshot (D11); run a bounded SQL query first if history
           is needed.

    Attributes
    ----------
        timeout:
            Per-connection timeout in seconds for the production client.
        poll_interval:
            Seconds the production adapter waits between empty WAL polls.

    Methods
    -------
        can_handle:
        stream:
        fetch:
    """

    name = SOURCE_NAMESPACE

    def __init__(self, timeout: float = 30.0, poll_interval: float = 1.0) -> None:
        """
        Create a PostgreSQL CDC connector

        Parameters
        ----------
            timeout:
                Per-connection timeout in seconds for the underlying
                client.
            poll_interval:
                Seconds the production adapter waits between empty WAL
                polls.
        """
        self.timeout = timeout
        self.poll_interval = poll_interval

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI names a PostgreSQL CDC source

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for ``postgres-cdc://`` URIs.
        """
        return uri.startswith(_SCHEME)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream row changes and yield one ``Result`` per change, forever

        Creates (or reuses) the replication slot named by ``?slot=`` -- a
        ``omni_fetcher_<uuid>`` name is generated when omitted -- then
        yields every decoded INSERT / UPDATE / DELETE as a ``change``
        node. The stream ends only on a typed error or abandonment. On a
        clean end the slot is dropped; after a transport failure it is
        kept as the durable resume pointer for ``stream_with_restart``.

        NOTE:
            1. ``zoom`` is accepted; a single row change is its own
               natural granularity.

        Parameters
        ----------
            uri:
                The ``postgres-cdc://host[:port]/database?...`` source URI.
            auth:
                Optional credential forwarded to the stream factory.
            zoom:
                Accepted; natural per-change granularity is emitted.

        Return
        ------
            results:
                An unbounded async iterator of ``Result`` items.
        """
        del zoom
        if not ASYNCPG_AVAILABLE:
            yield error(
                kind=ErrorKind.UNSUPPORTED,
                message=(
                    "asyncpg is not installed; install the 'postgres' extra "
                    "(pip install 'omni_fetcher[postgres]') to use "
                    "postgres-cdc:// sources"
                ),
                locator=uri,
            )
            return

        try:
            spec = _parse_uri(uri)
        except ValueError as exc:
            yield from_exception(
                exc,
                kind=ErrorKind.INVALID_INPUT,
                message="invalid postgres-cdc:// URI",
                locator=uri,
            )
            return

        try:
            replication = await self._make_replication_stream(spec, auth)
        except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
            yield from_exception(
                exc,
                kind=ErrorKind.TRANSIENT,
                message="could not connect to PostgreSQL",
                locator=uri,
            )
            return

        slot = spec.slot or f"{_SLOT_PREFIX}{uuid.uuid4().hex[:12]}"
        counter = SequenceCounter()
        # After a transport failure the slot must survive: its
        # confirmed_flush_lsn is the only resume pointer (D7).
        transport_failed = False
        try:
            try:
                await replication.create_slot(slot)
                while True:
                    change = await replication.next_change()
                    yield self._change_result(uri, slot, change, counter)
            except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
                transport_failed = True
                yield from_exception(
                    exc,
                    kind=ErrorKind.TRANSIENT,
                    message="postgres replication failed",
                    locator=uri,
                )
                return
        finally:
            if not transport_failed:
                try:
                    await replication.drop_slot(slot)
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
            try:
                await replication.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

    async def fetch(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> Result:
        """
        Refuse collection of an unbounded source (typed, immediate)

        Parameters
        ----------
            uri:
                The ``postgres-cdc://`` URI whose collection was requested.
            auth:
                Ignored.
            zoom:
                Ignored.

        Return
        ------
            result:
                ``error(UNSUPPORTED)`` directing callers to ``stream()``.
        """
        del auth, zoom
        return error(
            kind=ErrorKind.UNSUPPORTED,
            message=(
                "postgres-cdc:// is an unbounded source and cannot be collected; "
                "iterate stream() instead of calling fetch()"
            ),
            locator=uri,
        )

    async def _make_replication_stream(
        self,
        spec: _PostgresCDCSpec,
        auth: Optional[AuthCredential],
    ) -> _ReplicationStream:
        """Build a connected replication stream for the spec (the database seam).

        Production wraps asyncpg in the narrow ``_ReplicationStream``
        protocol; tests replace this method with a scripted fake. Only
        ever called when ``ASYNCPG_AVAILABLE`` (or under a test seam).
        """
        return await _AsyncpgAdapter.create(spec, auth, self.timeout, self.poll_interval)

    def _change_result(
        self,
        uri: str,
        slot: str,
        change: _Change,
        counter: SequenceCounter,
    ) -> Result:
        """Map one decoded row change onto the canonical per-item Result."""
        record = {
            "op": change.op,
            "table": change.table,
            "new": change.new,
            "old": change.old,
            "lsn": change.lsn,
            "timestamp": change.timestamp,
            "xid": change.xid,
        }
        node = build_node(
            kind=CHANGE_KIND,
            atoms=[
                Text(
                    content=json.dumps(record, ensure_ascii=False, default=str),
                    format=TextFormat.CODE,
                )
            ],
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "table": change.table,
                "operation": change.op,
                "lsn": change.lsn,
                "timestamp": change.timestamp,
                "xid": change.xid,
                "slot": slot,
            },
        )
        stamp_temporal(node, sequence=counter.next(), timestamp=now_utc())
        return success(node)


class _DecodedChange:
    """A concrete ``_Change`` produced by the production pgoutput decoder."""

    def __init__(
        self,
        op: str,
        table: str,
        new: Optional[Dict[str, Any]],
        old: Optional[Dict[str, Any]],
        lsn: str,
        timestamp: str,
        xid: Optional[int],
    ) -> None:
        self.op = op
        self.table = table
        self.new = new
        self.old = old
        self.lsn = lsn
        self.timestamp = timestamp
        self.xid = xid


class _AsyncpgAdapter:
    """Production ``_ReplicationStream`` built on asyncpg (integration-tested only).

    Unit suites never construct this; it exists so the stream's protocol
    has exactly one production implementation. Changes are consumed by
    polling ``pg_logical_slot_get_binary_changes`` with the built-in
    ``pgoutput`` plugin (D6) and decoding its binary messages; pgoutput
    requires a publication, so a ``FOR ALL TABLES`` publication named
    ``omni_fetcher_pub`` is created (or reused) alongside the slot --
    v1.6 streams all tables (no publication filtering).
    """

    _PUBLICATION = "omni_fetcher_pub"

    def __init__(self, connection: Any, poll_interval: float) -> None:
        self._connection = connection
        self._poll_interval = poll_interval
        self._slot: Optional[str] = None
        self._decoder = _PgOutputDecoder()
        self._pending: list[_DecodedChange] = []

    @classmethod
    async def create(
        cls,
        spec: _PostgresCDCSpec,
        auth: Optional[AuthCredential],
        timeout: float,
        poll_interval: float,
    ) -> "_AsyncpgAdapter":
        """Construct a connected adapter for the spec."""
        import asyncpg  # imported only with the extra

        del auth  # credentials travel in the URI (?user=&password=)
        connection = await asyncpg.connect(
            host=spec.host,
            port=spec.port,
            database=spec.database,
            user=spec.user,
            password=spec.password,
            timeout=timeout,
        )
        return cls(connection, poll_interval)

    async def create_slot(self, name: str) -> None:
        try:
            await self._connection.execute(f"CREATE PUBLICATION {self._PUBLICATION} FOR ALL TABLES")
        except Exception:  # noqa: BLE001 - already exists (or insufficient rights)
            pass
        try:
            await self._connection.fetchrow(
                "SELECT pg_create_logical_replication_slot($1, 'pgoutput')", name
            )
        except Exception as exc:  # noqa: BLE001 - reuse an existing slot (D7)
            if "already exists" not in str(exc):
                raise
        self._slot = name

    async def next_change(self) -> _Change:
        import asyncio

        while True:
            if self._pending:
                return self._pending.pop(0)
            rows = await self._connection.fetch(
                "SELECT lsn, xid, data FROM pg_logical_slot_get_binary_changes"
                "($1, NULL, NULL, 'proto_version', '1', 'publication_names', $2)",
                self._slot,
                self._PUBLICATION,
            )
            for row in rows:
                decoded = self._decoder.decode(bytes(row["data"]), _format_lsn(row["lsn"]))
                if decoded is not None:
                    self._pending.append(decoded)
            if not self._pending:
                await asyncio.sleep(self._poll_interval)

    async def drop_slot(self, name: str) -> None:
        await self._connection.execute("SELECT pg_drop_replication_slot($1)", name)

    async def close(self) -> None:
        await self._connection.close()


def _format_lsn(lsn: Any) -> str:
    """Render an LSN in PostgreSQL's canonical ``X/XXXXXXX`` hex form.

    asyncpg decodes ``pg_lsn`` values as 64-bit integers; a value already
    in text form passes through unchanged.
    """
    if isinstance(lsn, int):
        return f"{lsn >> 32:X}/{lsn & 0xFFFFFFFF:X}"
    return str(lsn)


class _PgOutputDecoder:
    """Minimal decoder for pgoutput protocol-version-1 binary messages.

    Tracks Relation ('R') messages to resolve relation ids into
    ``schema.table`` + column names, and Begin ('B') messages for the
    transaction id and commit timestamp; Insert/Update/Delete become
    ``_DecodedChange`` records. All other message types are ignored.
    """

    # Microseconds between the Unix and PostgreSQL (2000-01-01) epochs.
    _PG_EPOCH_OFFSET_US = 946_684_800_000_000

    def __init__(self) -> None:
        self._relations: Dict[int, tuple[str, list[str]]] = {}
        self._xid: Optional[int] = None
        self._timestamp: str = ""

    def decode(self, data: bytes, lsn: str) -> Optional[_DecodedChange]:
        """Decode one pgoutput message; return a change or ``None``."""
        import struct

        tag = data[:1]
        if tag == b"B":  # Begin: final_lsn(8) commit_ts(8) xid(4)
            _, commit_ts, xid = struct.unpack(">QqI", data[1:21])
            self._xid = xid
            self._timestamp = self._to_iso(commit_ts)
            return None
        if tag == b"R":  # Relation: id(4) ns(cstr) name(cstr) ident(1) ncols(2) cols
            relation_id = struct.unpack(">I", data[1:5])[0]
            offset = 5
            namespace, offset = self._cstring(data, offset)
            relname, offset = self._cstring(data, offset)
            offset += 1  # replica identity byte
            ncols = struct.unpack(">H", data[offset : offset + 2])[0]
            offset += 2
            columns: list[str] = []
            for _ in range(ncols):
                offset += 1  # per-column flags
                name, offset = self._cstring(data, offset)
                columns.append(name)
                offset += 8  # type oid (4) + type modifier (4)
            table = f"{namespace}.{relname}" if namespace else relname
            self._relations[relation_id] = (table, columns)
            return None
        if tag == b"I":  # Insert: relid(4) 'N' tuple
            relation_id = struct.unpack(">I", data[1:5])[0]
            new, _ = self._tuple(data, 6)
            return self._change("INSERT", relation_id, new=new, old=None, lsn=lsn)
        if tag == b"U":  # Update: relid(4) ['K'|'O' old-tuple] 'N' new-tuple
            relation_id = struct.unpack(">I", data[1:5])[0]
            offset = 5
            old_values: Optional[list] = None
            if data[offset : offset + 1] in (b"K", b"O"):
                old_values, offset = self._tuple(data, offset + 1)
            new, _ = self._tuple(data, offset + 1)
            return self._change("UPDATE", relation_id, new=new, old=old_values, lsn=lsn)
        if tag == b"D":  # Delete: relid(4) 'K'|'O' old-tuple
            relation_id = struct.unpack(">I", data[1:5])[0]
            old_values, _ = self._tuple(data, 6)
            return self._change("DELETE", relation_id, new=None, old=old_values, lsn=lsn)
        return None  # Commit / Origin / Type / Truncate: not emitted (D9)

    def _change(
        self,
        op: str,
        relation_id: int,
        *,
        new: Optional[list],
        old: Optional[list],
        lsn: str,
    ) -> _DecodedChange:
        table, columns = self._relations.get(relation_id, (f"relation:{relation_id}", []))

        def named(values: Optional[list]) -> Optional[Dict[str, Any]]:
            if values is None:
                return None
            if columns:
                return dict(zip(columns, values))
            return {str(index): value for index, value in enumerate(values)}

        return _DecodedChange(
            op=op,
            table=table,
            new=named(new),
            old=named(old),
            lsn=lsn,
            timestamp=self._timestamp,
            xid=self._xid,
        )

    def _tuple(self, data: bytes, offset: int) -> tuple[list, int]:
        """Decode one pgoutput TupleData at ``offset``; return (values, next offset)."""
        import struct

        ncols = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2
        values: list = []
        for _ in range(ncols):
            kind = data[offset : offset + 1]
            offset += 1
            if kind in (b"n", b"u"):  # null / unchanged TOAST
                values.append(None)
            else:  # b"t": length-prefixed text representation
                length = struct.unpack(">I", data[offset : offset + 4])[0]
                offset += 4
                values.append(data[offset : offset + length].decode("utf-8", errors="replace"))
                offset += length
        return values, offset

    def _to_iso(self, pg_timestamp_us: int) -> str:
        """Convert a PG epoch (2000-01-01) microsecond timestamp to ISO-8601 UTC."""
        from datetime import datetime, timezone

        unix_us = pg_timestamp_us + self._PG_EPOCH_OFFSET_US
        return datetime.fromtimestamp(unix_us / 1_000_000.0, tz=timezone.utc).isoformat()

    @staticmethod
    def _cstring(data: bytes, offset: int) -> tuple[str, int]:
        """Read a NUL-terminated string at ``offset``; return (text, next offset)."""
        end = data.index(b"\x00", offset)
        return data[offset:end].decode("utf-8", errors="replace"), end + 1
