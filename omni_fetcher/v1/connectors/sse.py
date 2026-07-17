"""The canonical ``sse`` connector -- unbounded Server-Sent Events source for v1.

Consumes an SSE stream through the v1 contract: each dispatched event is
one ``Result`` whose tree is a single ``kind="message"`` node carrying one
plain ``Text`` atom (the event's raw ``data:`` payload, undecoded/unparsed)
plus resume metadata in ``source_extra["sse"]`` (url, handshake_timestamp,
sequence, close_code). Parsing the SSE wire format (``data:``/``id:``/blank
line dispatch) is protocol framing, not content transformation -- it is the
only way to recover one message per dispatched event from the underlying
byte stream, and mirrors how a browser's ``EventSource`` frames the same
wire format.

Configuration travels entirely in the URI query string: ``?token=<value>``
or ``?auth=Bearer+<token>`` for auth (forwarded verbatim as part of the
request URL), and ``?sequence=<n>`` naming the sequence number to assign
the next event when the server does not send its own ``id:`` field
(``stream_with_restart`` derives this from the last delivered message on
reconnect). When the server *does* send ``id:``, an integer id is used
directly as the resume sequence (falls back to message-receipt order
otherwise).

The ``sse://`` / ``sses://`` scheme maps onto a plain ``http://`` /
``https://`` GET request under the hood. ``aiohttp`` is optional (the
``websockets`` extra, shared with the WebSocket connector per D10): this
module imports without it, ``builtin_registry()`` skips the source when it
is missing, and direct use yields a typed ``UNSUPPORTED`` naming the extra.
All transport access goes through a narrow ``_Session`` protocol built by
the ``_make_session`` seam, so tests script a fake raw line stream and
never open a real connection.

Stream-only: ``fetch()`` returns a typed ``UNSUPPORTED`` immediately.
Connection loss, timeouts, or handshake failure yield one terminal
``TRANSIENT``; the session is always closed when iteration ends or is
abandoned.
"""

from __future__ import annotations

import importlib.util
from typing import Any, AsyncIterable, AsyncIterator, Optional, Protocol
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

# Source namespace under which all descriptive ``sse`` fields are stored.
SOURCE_NAMESPACE = "sse"

# Advisory semantic ``kind`` for every node this connector emits.
MESSAGE_KIND = "message"

# Whether the optional aiohttp client is importable (the ``websockets`` extra).
AIOHTTP_AVAILABLE = importlib.util.find_spec("aiohttp") is not None

_SCHEME_TO_HTTP = {"sse://": "http://", "sses://": "https://"}


class _Session(Protocol):
    """The narrow raw-line-stream protocol the stream drives.

    Implemented by the production ``aiohttp`` adapter and by test fakes;
    the stream itself never touches ``aiohttp`` directly, so unit tests
    need no real connection. Yields raw lines (without trailing newline)
    from the response body; ending iteration (``StopAsyncIteration``) means
    the server closed the connection cleanly.
    """

    def __aiter__(self) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


class _SSESpec:
    """Parsed ``sse://`` / ``sses://`` routing decision."""

    def __init__(
        self, uri: str, http_uri: str, token: Optional[str], auth: Optional[str], sequence: int
    ) -> None:
        self.uri = uri
        self.http_uri = http_uri
        self.token = token
        self.auth = auth
        self.sequence = sequence


def _parse_uri(uri: str) -> _SSESpec:
    """Parse an ``sse://``/``sses://`` URI into a spec, raising ``ValueError`` when bad."""
    http_uri: Optional[str] = None
    for scheme, http_scheme in _SCHEME_TO_HTTP.items():
        if uri.startswith(scheme):
            location, _, query = uri[len(scheme) :].partition("?")
            if not location:
                raise ValueError(f"sse(s):// URI must name a host: {uri}")
            http_uri = f"{http_scheme}{location}" + (f"?{query}" if query else "")
            break
    if http_uri is None:
        raise ValueError(f"not an sse:// or sses:// URI: {uri}")

    _, _, query = uri.partition("?")
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

    return _SSESpec(uri=uri, http_uri=http_uri, token=token, auth=auth, sequence=sequence)


class _SSEEvent:
    """One dispatched SSE event (framing-level, not content-level)."""

    def __init__(self, data: str, id: Optional[str]) -> None:
        self.data = data
        self.id = id


async def _iter_events(lines: AsyncIterable[str]) -> AsyncIterator[_SSEEvent]:
    """Dispatch raw SSE wire-format lines into events (``data:``/``id:``/blank-line)."""
    data_lines: list[str] = []
    event_id: Optional[str] = None
    async for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if line == "":
            if data_lines:
                yield _SSEEvent(data="\n".join(data_lines), id=event_id)
                data_lines = []
            continue
        if line.startswith(":"):
            continue  # comment line
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
        elif field == "id":
            event_id = value or None
        # "event" and "retry" fields are protocol framing this connector
        # does not surface (no filtering, no client-side retry timer; D9).
    if data_lines:
        yield _SSEEvent(data="\n".join(data_lines), id=event_id)


class SSEConnector(BaseFetcher):
    """
    Unbounded Server-Sent Events connector for the v1 contract
    ===============================================
    Streams dispatched events as canonical per-item ``Result``s. Auth and
    resume position travel as URI query parameters, forwarded verbatim as
    part of the request URL. ``fetch()`` is a typed ``UNSUPPORTED``. All
    transport access goes through the ``_make_session`` seam.
    ===============================================
    NOTE:
        1. Without the ``aiohttp`` extra the stream yields one typed
           ``UNSUPPORTED`` naming the extra; nothing raises.
        2. ``?sequence=<n>`` seeds the fallback per-stream sequence counter
           used when the server does not send its own ``id:`` field.

    Attributes
    ----------
        timeout:
            Connection timeout in seconds for the production client.

    Methods
    -------
        can_handle:
        stream:
        fetch:
    """

    name = SOURCE_NAMESPACE

    def __init__(self, timeout: float = 30.0) -> None:
        """
        Create an SSE connector

        Parameters
        ----------
            timeout:
                Connection timeout in seconds for the underlying client.
        """
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI names an SSE source

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for ``sse://`` / ``sses://`` URIs.
        """
        return uri.startswith(("sse://", "sses://"))

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Open an SSE stream and yield one ``Result`` per event, forever

        The stream ends only on a typed error (connection failure, dropped
        connection) or consumer abandonment -- either way the underlying
        session is closed.

        NOTE:
            1. ``zoom`` is accepted; a single event is its own natural
               granularity (central pruning still applies per item).

        Parameters
        ----------
            uri:
                The ``sse://host[:port]/path?...`` source URI.
            auth:
                Optional credential forwarded to the session factory.
            zoom:
                Accepted; natural per-event granularity is emitted.

        Return
        ------
            results:
                An unbounded async iterator of ``Result`` items.
        """
        del zoom
        if not AIOHTTP_AVAILABLE:
            yield error(
                kind=ErrorKind.UNSUPPORTED,
                message=(
                    "aiohttp is not installed; install the 'websockets' extra "
                    "(pip install 'omni_fetcher[websockets]') to use sse:// sources"
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
                message="invalid sse(s):// URI",
                locator=uri,
            )
            return

        try:
            session = await self._make_session(spec, auth)
        except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
            yield from_exception(
                exc,
                kind=ErrorKind.TRANSIENT,
                message="could not connect to SSE server",
                locator=uri,
            )
            return

        handshake_timestamp = now_utc().isoformat()
        counter = SequenceCounter(start=spec.sequence)
        try:
            try:
                async for sse_event in _iter_events(session):
                    yield self._message_result(uri, sse_event, counter, handshake_timestamp)
            except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
                yield from_exception(
                    exc,
                    kind=ErrorKind.TRANSIENT,
                    message="sse connection lost",
                    locator=uri,
                )
                return
        finally:
            await session.aclose()

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
                The ``sse(s)://`` URI whose collection was requested.
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
                "sse(s):// is an unbounded source and cannot be collected; "
                "iterate stream() instead of calling fetch()"
            ),
            locator=uri,
        )

    async def _make_session(
        self,
        spec: _SSESpec,
        auth: Optional[AuthCredential],
    ) -> _Session:
        """Build a live raw-line session for the spec (the transport seam).

        Production wraps ``aiohttp`` in the narrow ``_Session`` protocol;
        tests replace this method with a scripted fake. Only ever called
        when ``AIOHTTP_AVAILABLE`` (or under a test seam).
        """
        return await _AiohttpSSEAdapter.create(spec, auth, self.timeout)

    def _message_result(
        self,
        uri: str,
        sse_event: _SSEEvent,
        counter: SequenceCounter,
        handshake_timestamp: str,
    ) -> Result:
        """Map one dispatched SSE event onto the canonical per-item Result."""
        temporal_sequence = counter.next()
        resume_sequence = temporal_sequence
        if sse_event.id is not None:
            try:
                resume_sequence = int(sse_event.id)
            except ValueError:
                resume_sequence = temporal_sequence

        node = build_node(
            kind=MESSAGE_KIND,
            # Event data is an arbitrary payload, routinely JSON.
            atoms=[Text(content=sse_event.data, format=TextFormat.OPAQUE)],
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "url": uri,
                "handshake_timestamp": handshake_timestamp,
                "sequence": resume_sequence,
                "close_code": None,
            },
        )
        stamp_temporal(node, sequence=temporal_sequence, timestamp=now_utc())
        return success(node)


class _AiohttpSSEAdapter:
    """Production ``_Session`` built on ``aiohttp`` (integration-tested only).

    Unit suites never construct this; it exists so the stream's protocol
    has exactly one production implementation.
    """

    def __init__(self, session: Any, response: Any) -> None:
        self._session = session
        self._response = response

    @classmethod
    async def create(
        cls,
        spec: _SSESpec,
        auth: Optional[AuthCredential],
        timeout: float,
    ) -> "_AiohttpSSEAdapter":
        """Open a streaming GET to the spec's URL, forwarding auth as-is."""
        import aiohttp  # imported only with the extra

        del auth  # auth travels in the URI query string (D2); nothing extra to inject.
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=timeout)
        )
        try:
            response = await session.get(spec.http_uri, headers={"Accept": "text/event-stream"})
            response.raise_for_status()
        except Exception:
            await session.close()
            raise
        return cls(session, response)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._lines()

    async def _lines(self) -> AsyncIterator[str]:
        async for raw_line in self._response.content:
            yield raw_line.decode("utf-8", errors="replace")

    async def aclose(self) -> None:
        self._response.close()
        await self._session.close()
