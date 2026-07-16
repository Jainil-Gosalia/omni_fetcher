"""The canonical ``websocket`` connector -- unbounded WebSocket source for v1.

Consumes a WebSocket connection through the v1 contract: each message is
one ``Result`` whose tree is a single ``kind="message"`` node carrying one
plain ``Text`` atom (the raw payload, undecoded/unparsed) plus resume
metadata in ``source_extra["websocket"]`` (url, handshake_timestamp,
sequence, close_code).

Configuration travels entirely in the URI query string -- the one channel
that survives orchestrator routing and the CLI: ``?token=<value>`` or
``?auth=Bearer+<token>`` for auth (forwarded verbatim to the server as part
of the connect URL; the server's handshake decides what to do with them),
and ``?sequence=<n>`` naming the sequence number to assign the next
received message (``stream_with_restart`` derives this from the last
delivered message on reconnect).

``websockets`` is optional (the ``websockets`` extra): this module imports
without it, ``builtin_registry()`` skips the source when it is missing, and
direct use yields a typed ``UNSUPPORTED`` naming the extra. All connection
handling flows through a narrow ``_Connection`` protocol built by the
``_make_connection`` seam, so tests script a fake and never open a real
socket.

Stream-only: ``fetch()`` returns a typed ``UNSUPPORTED`` immediately.
Connection loss, timeouts, or handshake failure yield one terminal
``TRANSIENT``. A close under RFC 6455 code 1000 (normal closure) or 1001
(going away) is the server ending the stream on purpose, not a failure --
the stream simply ends with no ``Error``, mirroring SSE's clean end. The
connection is always closed when iteration ends or is abandoned.
"""

from __future__ import annotations

import importlib.util
from typing import Any, AsyncIterator, Optional, Protocol
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

# Source namespace under which all descriptive ``websocket`` fields are stored.
SOURCE_NAMESPACE = "websocket"

# Advisory semantic ``kind`` for every node this connector emits.
MESSAGE_KIND = "message"

# Whether the optional websockets client is importable (the ``websockets`` extra).
WEBSOCKETS_AVAILABLE = importlib.util.find_spec("websockets") is not None

_SCHEMES = ("ws://", "wss://")

# RFC 6455 close codes that mean "the server ended the stream on purpose" --
# not a failure. A clean close under one of these codes ends the stream with
# no Error (mirrors SSE's "server closed the connection" -> no exception);
# any other close (or a recv() failure with no close frame at all) is TRANSIENT.
_CLEAN_CLOSE_CODES = frozenset({1000, 1001})


class _Connection(Protocol):
    """The narrow connection protocol the stream drives.

    Implemented by the production ``websockets`` adapter and by test fakes;
    the stream itself never touches ``websockets`` directly, so unit tests
    need no real socket.
    """

    async def recv(self) -> Any: ...

    async def close(self) -> None: ...

    @property
    def close_code(self) -> Optional[int]: ...


class _WebSocketSpec:
    """Parsed ``ws://`` / ``wss://`` routing decision."""

    def __init__(self, uri: str, token: Optional[str], auth: Optional[str], sequence: int) -> None:
        self.uri = uri
        self.token = token
        self.auth = auth
        self.sequence = sequence


def _parse_uri(uri: str) -> _WebSocketSpec:
    """Parse a ``ws://``/``wss://`` URI into a spec, raising ``ValueError`` when bad."""
    if not uri.startswith(_SCHEMES):
        raise ValueError(f"not a ws:// or wss:// URI: {uri}")
    scheme_len = uri.index("://") + 3
    remainder = uri[scheme_len:]
    location, _, query = remainder.partition("?")
    if not location:
        raise ValueError(f"ws(s):// URI must name a host: {uri}")

    params = parse_qs(query)
    token = params.get("token", [None])[0]
    auth = params.get("auth", [None])[0]
    raw_sequence = params.get("sequence", ["0"])[0]
    try:
        sequence = int(raw_sequence)
    except ValueError:
        raise ValueError(f"sequence= must be an integer: {raw_sequence}")
    if sequence < 0:
        raise ValueError(f"sequence= must be >= 0: {raw_sequence}")

    return _WebSocketSpec(uri=uri, token=token, auth=auth, sequence=sequence)


class WebSocketConnector(BaseFetcher):
    """
    Unbounded WebSocket connector for the v1 contract
    ===============================================
    Streams messages as canonical per-item ``Result``s. Auth and resume
    position travel as URI query parameters, forwarded verbatim to the
    server as part of the connect URL. ``fetch()`` is a typed
    ``UNSUPPORTED``. All socket access goes through the ``_make_connection``
    seam.
    ===============================================
    NOTE:
        1. Without the ``websockets`` extra the stream yields one typed
           ``UNSUPPORTED`` naming the extra; nothing raises.
        2. ``?sequence=<n>`` seeds the per-stream sequence counter so a
           resumed connection continues numbering where the previous one
           left off (see ``retry.py``'s resume-URI derivation).

    Attributes
    ----------
        timeout:
            Handshake timeout in seconds for the production client.

    Methods
    -------
        can_handle:
        stream:
        fetch:
    """

    name = SOURCE_NAMESPACE

    def __init__(self, timeout: float = 30.0) -> None:
        """
        Create a WebSocket connector

        Parameters
        ----------
            timeout:
                Handshake timeout in seconds for the underlying client.
        """
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI names a WebSocket source

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for ``ws://`` / ``wss://`` URIs.
        """
        return uri.startswith(_SCHEMES)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Open a WebSocket and yield one ``Result`` per message, forever

        The stream ends only on a typed error (handshake failure, dropped
        connection) or consumer abandonment -- either way the underlying
        connection is closed.

        NOTE:
            1. ``zoom`` is accepted; a single message is its own natural
               granularity (central pruning still applies per item).

        Parameters
        ----------
            uri:
                The ``ws://host[:port]/path?...`` source URI.
            auth:
                Optional credential forwarded to the connection factory.
            zoom:
                Accepted; natural per-message granularity is emitted.

        Return
        ------
            results:
                An unbounded async iterator of ``Result`` items.
        """
        del zoom
        if not WEBSOCKETS_AVAILABLE:
            yield error(
                kind=ErrorKind.UNSUPPORTED,
                message=(
                    "websockets is not installed; install the 'websockets' extra "
                    "(pip install 'omni_fetcher[websockets]') to use ws:// sources"
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
                message="invalid ws(s):// URI",
                locator=uri,
            )
            return

        try:
            connection = await self._make_connection(spec, auth)
        except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
            yield from_exception(
                exc,
                kind=ErrorKind.TRANSIENT,
                message="could not connect to WebSocket server",
                locator=uri,
            )
            return

        handshake_timestamp = now_utc().isoformat()
        counter = SequenceCounter(start=spec.sequence)
        try:
            try:
                while True:
                    payload = await connection.recv()
                    yield self._message_result(uri, payload, counter, handshake_timestamp)
            except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
                close_code = connection.close_code
                if close_code in _CLEAN_CLOSE_CODES:
                    # The server ended the stream on purpose (RFC 6455 normal
                    # closure / going away) -- not a failure, so no Error.
                    return
                detail = f"websocket connection lost (close_code={close_code})"
                yield from_exception(
                    exc,
                    kind=ErrorKind.TRANSIENT,
                    message=detail,
                    locator=uri,
                )
                return
        finally:
            await connection.close()

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
                The ``ws(s)://`` URI whose collection was requested.
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
                "ws(s):// is an unbounded source and cannot be collected; "
                "iterate stream() instead of calling fetch()"
            ),
            locator=uri,
        )

    async def _make_connection(
        self,
        spec: _WebSocketSpec,
        auth: Optional[AuthCredential],
    ) -> _Connection:
        """Build a live connection for the spec (the socket seam).

        Production wraps ``websockets.connect`` in the narrow
        ``_Connection`` protocol; tests replace this method with a scripted
        fake. Only ever called when ``WEBSOCKETS_AVAILABLE`` (or under a
        test seam).
        """
        return await _WebsocketsAdapter.create(spec, auth, self.timeout)

    def _message_result(
        self,
        uri: str,
        payload: Any,
        counter: SequenceCounter,
        handshake_timestamp: str,
    ) -> Result:
        """Map one received message onto the canonical per-item Result."""
        content = (
            payload.decode("utf-8", errors="replace")
            if isinstance(payload, bytes)
            else str(payload)
        )
        sequence = counter.next()
        node = build_node(
            kind=MESSAGE_KIND,
            atoms=[Text(content=content, format=TextFormat.PLAIN)],
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "url": uri,
                "handshake_timestamp": handshake_timestamp,
                "sequence": sequence,
                "close_code": None,
            },
        )
        stamp_temporal(node, sequence=sequence, timestamp=now_utc())
        return success(node)


class _WebsocketsAdapter:
    """Production ``_Connection`` built on ``websockets`` (integration-tested only).

    Unit suites never construct this; it exists so the stream's protocol
    has exactly one production implementation.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @classmethod
    async def create(
        cls,
        spec: _WebSocketSpec,
        auth: Optional[AuthCredential],
        timeout: float,
    ) -> "_WebsocketsAdapter":
        """Connect to the spec's URI, forwarding auth/sequence as-is."""
        import websockets  # imported only with the extra

        del auth  # auth travels in the URI query string (D2); nothing extra to inject.
        connection = await websockets.connect(spec.uri, open_timeout=timeout)
        return cls(connection)

    async def recv(self) -> Any:
        return await self._connection.recv()

    @property
    def close_code(self) -> Optional[int]:
        return getattr(self._connection, "close_code", None)

    async def close(self) -> None:
        await self._connection.close()
