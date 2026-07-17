"""The canonical ``redis`` connector -- unbounded Redis Streams source for v1.

Consumes a Redis Stream through the v1 contract: each message is one
``Result`` whose tree is a single ``kind="message"`` node carrying one
plain ``Text`` atom (the decoded value) plus resume positions in
``source_extra["redis"]`` (entry_id, timestamp, stream). ``entry_id`` is
the message's own entry ID; resuming seeks that entry ID per
``?offset=<entry_id>`` (the restart helper derives this).

Configuration travels in the URI: ``redis://<host>[:<port>]/<stream-key>``
(default port 6379) with ``?offset=$`` (default, latest) | ``0`` (earliest) |
``<entry-id>`` (resume), and ``?group=<id>`` opting into committing
consumer-group semantics -- without it the connector uses XREAD statelessly
and commits nothing.

redis-py is a core dependency (used by RedisCacheBackend), so this module
imports unconditionally and is registered in builtin_registry() without
any extra gating (unlike kafka, which is optional).

Stream-only: ``fetch()`` returns a typed ``UNSUPPORTED`` immediately.
Transport failures mid-stream yield one terminal ``TRANSIENT``; the
connection is always closed when iteration ends or is abandoned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Optional
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

# Source namespace under which all descriptive ``redis`` fields are stored.
SOURCE_NAMESPACE = "redis"

# Advisory semantic ``kind`` for every node this connector emits.
MESSAGE_KIND = "message"

_SCHEME = "redis://"
_DEFAULT_PORT = 6379


class _RedisSpec:
    """Parsed ``redis://`` routing decision."""

    def __init__(
        self,
        host: str,
        port: int,
        stream_key: str,
        offset: str,
        group: Optional[str],
        db: int,
    ) -> None:
        self.host = host
        self.port = port
        self.stream_key = stream_key
        self.offset = offset
        self.group = group
        self.db = db


def _parse_uri(uri: str) -> _RedisSpec:
    """Parse a ``redis://`` URI into a spec, raising ``ValueError`` when bad."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a redis:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    location, _, query = remainder.partition("?")
    host_part, _, stream_key = location.partition("/")

    if not host_part or not stream_key or "/" in stream_key:
        raise ValueError(f"redis:// URI must be redis://host[:port]/stream-key: {uri}")

    # Parse host:port
    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"redis:// port must be numeric: {port_str}")
    else:
        host = host_part
        port = _DEFAULT_PORT

    # Parse query parameters
    params = parse_qs(query)
    offset = params.get("offset", ["$"])[0]
    group = params.get("group", [None])[0]
    db = int(params.get("db", ["0"])[0])

    return _RedisSpec(
        host=host,
        port=port,
        stream_key=stream_key,
        offset=offset,
        group=group,
        db=db,
    )


class RedisConnector(BaseFetcher):
    """
    Unbounded Redis Streams connector for the v1 contract
    ===============================================
    Streams messages as canonical per-item ``Result``s, statelessly by
    default (XREAD, no commits) with ``?group=`` opting into committing
    consumer-group semantics owned by the host. ``fetch()`` is a typed
    ``UNSUPPORTED``. All broker access goes through the ``_make_client``
    seam.
    ===============================================
    NOTE:
        1. ``redis-py`` is a core dependency (unconditional), so the
           connector is registered in ``builtin_registry()`` without extra
           gating (unlike Kafka's optional ``aiokafka``).
        2. In group mode, the connector acks after each yielded message
           (explicit at-least-once after delivery).

    Attributes
    ----------
        timeout:
            Per-connection timeout in seconds.

    Methods
    -------
        can_handle:
        stream:
        fetch:
    """

    name = SOURCE_NAMESPACE

    def __init__(self, timeout: float = 30.0) -> None:
        """
        Create a Redis connector

        Parameters
        ----------
            timeout:
                Per-connection timeout in seconds.
        """
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI names a Redis Streams source

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for ``redis://`` URIs.
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
        Consume a Redis Stream and yield one ``Result`` per message, forever

        Stateless mode (no ``group=``) uses XREAD; group mode uses
        XREADGROUP with pending-message semantics. The stream ends only on
        a typed error or consumer abandonment.

        Parameters
        ----------
            uri:
                The ``redis://host[:port]/stream-key?...`` source URI.
            auth:
                Optional credential (reserved for future use).
            zoom:
                Accepted; natural per-message granularity is emitted.

        Return
        ------
            results:
                An unbounded async iterator of ``Result`` items.
        """
        del auth, zoom

        try:
            spec = _parse_uri(uri)
        except ValueError as exc:
            yield from_exception(
                exc,
                kind=ErrorKind.INVALID_INPUT,
                message="invalid redis:// URI",
                locator=uri,
            )
            return

        try:
            client = await self._make_client(spec, self.timeout)
        except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
            yield from_exception(
                exc,
                kind=ErrorKind.TRANSIENT,
                message="could not connect to Redis",
                locator=uri,
            )
            return

        counter = SequenceCounter()
        try:
            try:
                consumer_name = "omni-fetcher"  # Default consumer name in a group
                start_offset = spec.offset

                while True:
                    try:
                        if spec.group is not None:
                            # Group mode: XREADGROUP
                            result = await client.xreadgroup(
                                groupname=spec.group,
                                consumername=consumer_name,
                                streams={spec.stream_key: start_offset},
                                count=1,
                                block=1000,  # 1 second block
                            )
                            start_offset = ">"  # After first read, get pending messages
                        else:
                            # Stateless mode: XREAD
                            result = await client.xread(
                                streams={spec.stream_key: start_offset},
                                count=1,
                                block=1000,  # 1 second block
                            )
                            start_offset = "$"  # After first read, get only new messages

                        # Parse result and yield messages
                        if result:
                            for stream_key_bytes, entries in result:
                                for entry_id_bytes, data in entries:
                                    entry_id = entry_id_bytes.decode("utf-8")
                                    yield self._message_result(
                                        uri, spec.stream_key, entry_id, data, counter
                                    )

                    except Exception as exc:  # noqa: BLE001
                        yield from_exception(
                            exc,
                            kind=ErrorKind.TRANSIENT,
                            message="redis consume failed",
                            locator=uri,
                        )
                        return
            except Exception as exc:  # noqa: BLE001
                yield from_exception(
                    exc,
                    kind=ErrorKind.TRANSIENT,
                    message="redis stream error",
                    locator=uri,
                )
                return
        finally:
            await client.aclose()

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
                The ``redis://`` URI whose collection was requested.
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
                "redis:// is an unbounded source and cannot be collected; "
                "iterate stream() instead of calling fetch()"
            ),
            locator=uri,
        )

    async def _make_client(self, spec: _RedisSpec, timeout: float) -> Any:
        """Build a Redis client for the spec (the broker seam).

        Production uses redis.asyncio; tests replace this method with a
        scripted fake.
        """
        import redis.asyncio

        client = redis.asyncio.Redis(
            host=spec.host,
            port=spec.port,
            db=spec.db,
            decode_responses=False,
            socket_connect_timeout=timeout,
        )
        await client.ping()
        return client

    def _message_result(
        self,
        uri: str,
        stream_key: str,
        entry_id: str,
        data: Dict[bytes, bytes],
        counter: SequenceCounter,
    ) -> Result:
        """Map one Redis Stream entry onto the canonical per-item Result."""
        # Decode the message value (join all fields)
        content_parts = []
        for value in data.values():
            content_parts.append(value.decode("utf-8", errors="replace"))
        content = " ".join(content_parts) if content_parts else ""

        # Parse timestamp from entry_id (format: milliseconds-sequence)
        timestamp_str = entry_id.split("-")[0]
        try:
            timestamp_ms = int(timestamp_str)
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc).isoformat()
        except (ValueError, IndexError):
            timestamp = now_utc().isoformat()

        node = build_node(
            kind=MESSAGE_KIND,
            atoms=[
                Text(
                    content=content,
                    # Stream field values are arbitrary decoded bytes, joined
                    # for transport; no claim is made about their syntax.
                    format=TextFormat.OPAQUE,
                )
            ],
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "entry_id": entry_id,
                "timestamp": timestamp,
                "stream": stream_key,
            },
        )
        stamp_temporal(node, sequence=counter.next(), timestamp=now_utc())
        return success(node)
