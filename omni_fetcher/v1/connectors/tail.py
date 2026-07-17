"""The canonical ``tail`` connector -- the first unbounded v1 source.

Streams a local file line by line through the v1 contract: each complete
line is one ``Result`` whose tree is a single ``kind="log_line"`` node
carrying one plain ``Text`` atom (the line, without its trailing newline)
plus resume positions in ``source_extra["tail"]`` (path, byte_offset,
line_number). ``byte_offset`` is the offset AFTER the line -- the exact
``?from=`` value that resumes the stream without loss or duplication.

Configuration travels in the URI (the only channel that survives the
orchestrator's no-argument instantiation): ``tail://<path>`` with
``?from=end`` (default) | ``?from=start`` | ``?from=<byte-offset>`` and
``?poll=<seconds>``.

Stream-only: ``fetch()`` returns a typed ``UNSUPPORTED`` immediately --
an unbounded stream can never be collected. Expected failures are typed
``Result`` values, never raised: a missing path is ``NOT_FOUND``, a read
failure mid-stream is one terminal ``TRANSIENT``. All file handles are
released when the consumer stops iterating (``try/finally`` around the
generator body). Rotation/truncation handling is issue 02's slice.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Union
from urllib.parse import parse_qs, unquote

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

# Source namespace under which all descriptive ``tail`` fields are stored.
SOURCE_NAMESPACE = "tail"

# Advisory semantic ``kind`` for every node this connector emits.
LINE_KIND = "log_line"

# Default idle-poll interval (seconds) when the URI carries no ``poll=``.
DEFAULT_POLL_SECONDS = 0.5

_SCHEME = "tail://"


class _TailSpec:
    """Parsed ``tail://`` routing decision (path + start position + poll)."""

    def __init__(self, path: str, start: Union[str, int], poll: float) -> None:
        self.path = path
        self.start = start
        self.poll = poll


def _parse_uri(uri: str, default_poll: float) -> _TailSpec:
    """Parse a ``tail://`` URI into a spec, raising ``ValueError`` when bad."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a tail:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    path_part, _, query = remainder.partition("?")
    path = unquote(path_part)
    if not path:
        raise ValueError(f"tail:// URI carries no path: {uri}")

    params = parse_qs(query)
    raw_start = params.get("from", ["end"])[0]
    start: Union[str, int]
    if raw_start in ("end", "start"):
        start = raw_start
    else:
        start = int(raw_start)  # ValueError -> INVALID_INPUT at the boundary
        if start < 0:
            raise ValueError(f"from= byte offset must be >= 0: {raw_start}")

    poll = float(params.get("poll", [default_poll])[0])
    if poll <= 0:
        raise ValueError(f"poll= interval must be > 0: {poll}")
    return _TailSpec(path=path, start=start, poll=poll)


class TailConnector(BaseFetcher):
    """
    Unbounded file-tail connector for the v1 contract
    ===============================================
    Follows a local file and yields one canonical ``Result`` per complete
    line, indefinitely -- the tracer for streaming sources on the
    ``stream()`` seam. ``fetch()`` is a typed ``UNSUPPORTED`` (an
    unbounded stream cannot be collected). Read-only, stateless, and
    configured entirely through URI query parameters.
    ===============================================
    NOTE:
        1. Partial lines (no trailing newline yet) are never yielded; the
           connector re-reads from the same offset until the newline
           arrives, so items always carry whole lines.
        2. ``byte_offset`` in ``source_extra["tail"]`` is the position
           AFTER the yielded line: feeding it back as ``?from=`` resumes
           exactly, which the restart helper relies on.

    Attributes
    ----------
        poll:
            Programmatic default idle-poll interval in seconds; a
            ``?poll=`` URI parameter always wins.

    Methods
    -------
        can_handle:
        stream:
        fetch:
    """

    name = SOURCE_NAMESPACE

    def __init__(self, poll: float = DEFAULT_POLL_SECONDS) -> None:
        """
        Create a tail connector

        Parameters
        ----------
            poll:
                Default idle-poll interval in seconds, used when the URI
                carries no ``poll=`` parameter.
        """
        self.poll = poll

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI names a tailable source

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for ``tail://`` URIs.
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
        Follow a file and yield one ``Result`` per complete line, forever

        The unbounded primitive: existing content is streamed according to
        ``?from=`` and every subsequently appended line follows within one
        poll interval. The stream ends only on a typed error (missing
        file, read failure) or when the consumer stops iterating -- which
        releases the file handle.

        NOTE:
            1. ``auth`` is accepted for interface conformance; a local
               file needs no credential. ``zoom`` is accepted; a single
               line is its own natural granularity (the orchestrator's
               central pruning still applies per item).

        Parameters
        ----------
            uri:
                The ``tail://<path>?from=...&poll=...`` source URI.
            auth:
                Ignored.
            zoom:
                Accepted; natural per-line granularity is emitted.

        Return
        ------
            results:
                An unbounded async iterator of ``Result`` items.
        """
        del auth, zoom
        try:
            spec = _parse_uri(uri, self.poll)
        except ValueError as exc:
            yield from_exception(
                exc, kind=ErrorKind.INVALID_INPUT, message="invalid tail:// URI", locator=uri
            )
            return

        path = Path(spec.path)
        if not path.is_file():
            yield error(
                kind=ErrorKind.NOT_FOUND,
                message=f"file not found: {spec.path}",
                locator=uri,
            )
            return

        counter = SequenceCounter()
        line_number = 0
        try:
            handle = path.open("rb")
        except OSError as exc:
            yield from_exception(exc, kind=ErrorKind.TRANSIENT, locator=uri)
            return

        try:
            if spec.start == "end":
                handle.seek(0, os.SEEK_END)
            elif spec.start == "start":
                handle.seek(0)
            else:
                handle.seek(int(spec.start))
            opened_ino = os.fstat(handle.fileno()).st_ino

            while True:
                position_before = handle.tell()
                try:
                    raw = handle.readline()
                except OSError as exc:
                    yield from_exception(
                        exc,
                        kind=ErrorKind.TRANSIENT,
                        message="tail read failed",
                        locator=uri,
                    )
                    return

                if raw.endswith(b"\n"):
                    line_number += 1
                    yield self._line_result(uri, path, raw, handle.tell(), line_number, counter)
                    continue
                if raw:
                    # Partial line: re-read from the same offset once the
                    # writer finishes it -- whole lines only.
                    handle.seek(position_before)
                    await asyncio.sleep(spec.poll)
                    continue

                # At EOF: check for rotation, truncation, or disappearance
                # before sleeping. ``new_handle`` is a fresh handle when the
                # file was rotated/truncated, ``None`` when unchanged;
                # ``reason`` is set for a terminal condition (disappearance /
                # unreadable reopen).
                new_handle, reason = await self._follow_check(path, handle, opened_ino, spec.poll)
                if reason is not None:
                    yield error(kind=ErrorKind.TRANSIENT, message=reason, locator=uri)
                    return
                if new_handle is not None:
                    handle.close()
                    handle = new_handle
                    opened_ino = os.fstat(handle.fileno()).st_ino
                    line_number = 0
                    continue
                await asyncio.sleep(spec.poll)
        finally:
            handle.close()

    async def _follow_check(
        self,
        path: Path,
        handle: Any,
        opened_ino: int,
        poll: float,
    ) -> tuple[Optional[Any], Optional[str]]:
        """Decide, at EOF, whether the followed file rotated/vanished.

        Returns ``(new_handle, None)`` when the file was rotated (new inode)
        or truncated (shrank) and a fresh handle at offset 0 should replace
        the current one; ``(None, reason)`` when the file disappeared (after
        a one-poll grace for a delete+recreate replace) or could not be
        reopened; ``(None, None)`` when nothing changed and the caller should
        idle-poll.
        """

        def _reopen() -> Any:
            return path.open("rb")

        try:
            stat = os.stat(path)
        except OSError:
            # Vanished: grant one grace poll so an atomic delete+recreate
            # replace is seen as rotation rather than deletion.
            await asyncio.sleep(poll)
            try:
                os.stat(path)
            except OSError:
                return None, "tailed file disappeared"
            try:
                return _reopen(), None
            except OSError:
                return None, "could not reopen tailed file after rotation"

        rotated = stat.st_ino != opened_ino
        truncated = stat.st_size < handle.tell()
        if rotated or truncated:
            try:
                return _reopen(), None
            except OSError:
                return None, "could not reopen tailed file"
        return None, None

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
                The ``tail://`` URI whose collection was requested.
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
                "tail:// is an unbounded source and cannot be collected; "
                "iterate stream() instead of calling fetch()"
            ),
            locator=uri,
        )

    def _line_result(
        self,
        uri: str,
        path: Path,
        raw: bytes,
        offset_after: int,
        line_number: int,
        counter: SequenceCounter,
    ) -> Result:
        """Map one complete raw line onto the canonical per-item Result."""
        content = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        node = build_node(
            kind=LINE_KIND,
            # A log line is arbitrary decoded text, not asserted prose.
            atoms=[Text(content=content, format=TextFormat.OPAQUE)],
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "path": str(path),
                "byte_offset": offset_after,
                "line_number": line_number,
            },
        )
        stamp_temporal(node, sequence=counter.next(), timestamp=now_utc())
        return success(node)
