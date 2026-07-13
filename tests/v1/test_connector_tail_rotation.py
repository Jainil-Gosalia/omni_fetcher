"""Tail rotation / truncation / transient-end tests (issue 02).

Real-filesystem behavior for a followed log that rotates, truncates, or
vanishes. Truncation is in-place and works on every OS; rotation-by-replace
and deletion require POSIX open-file semantics (Windows blocks deleting or
renaming a file with an open reader), so those are skipped on Windows and
exercised on the Linux CI matrix.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import AsyncIterator

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.connectors.tail import TailConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Result, Success

pytestmark = pytest.mark.asyncio

_POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX open-file semantics (delete/rename a file with an open "
    "reader); exercised on the Linux CI matrix.",
)


def _uri(path: Path, **params: object) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    base = "tail://" + str(path).replace("\\", "/")
    return f"{base}?{query}" if query else base


async def _next(stream: AsyncIterator[Result]) -> Result:
    return await asyncio.wait_for(stream.__anext__(), timeout=8.0)  # type: ignore[attr-defined]


def _line(item: Result) -> str:
    assert isinstance(item, Success), item
    return item.tree.find_atoms(AtomKind.TEXT)[0].content


async def test_truncation_resumes_from_new_content(tmp_path: Path) -> None:
    """Truncating in place restarts reading from offset 0 (no re-yield)."""
    log = tmp_path / "app.log"
    log.write_text("first\nsecond\n", encoding="utf-8", newline="")

    stream = TailConnector().stream(_uri(log, **{"from": "start", "poll": 0.01}))
    try:
        assert _line(await _next(stream)) == "first"
        assert _line(await _next(stream)) == "second"

        # Shrink the file: "wb"/"w" truncates. The reader's byte position
        # (13) now exceeds the new size (6) -> reopen from the start.
        log.write_text("fresh\n", encoding="utf-8", newline="")

        assert _line(await _next(stream)) == "fresh"
    finally:
        await stream.aclose()  # type: ignore[attr-defined]


@_POSIX_ONLY
async def test_rotation_via_replace_resumes_on_the_new_file(tmp_path: Path) -> None:
    """An atomic replace (new inode at the path) is followed onto the new file."""
    log = tmp_path / "app.log"
    log.write_text("a\nb\n", encoding="utf-8", newline="")
    replacement = tmp_path / "rotated.log"
    replacement.write_text("c\n", encoding="utf-8", newline="")

    stream = TailConnector().stream(_uri(log, **{"from": "start", "poll": 0.01}))
    try:
        assert _line(await _next(stream)) == "a"
        assert _line(await _next(stream)) == "b"

        os.replace(replacement, log)  # new inode occupies the path

        assert _line(await _next(stream)) == "c"
    finally:
        await stream.aclose()  # type: ignore[attr-defined]


@_POSIX_ONLY
async def test_deletion_without_replacement_ends_in_transient(tmp_path: Path) -> None:
    """A vanished file (after the grace poll) ends the stream with TRANSIENT."""
    log = tmp_path / "app.log"
    log.write_text("only\n", encoding="utf-8", newline="")

    stream = TailConnector().stream(_uri(log, **{"from": "start", "poll": 0.01}))
    try:
        assert _line(await _next(stream)) == "only"

        log.unlink()

        terminal = await _next(stream)
        assert isinstance(terminal, Error)
        assert terminal.kind == ErrorKind.TRANSIENT
    finally:
        await stream.aclose()  # type: ignore[attr-defined]


@_POSIX_ONLY
async def test_unreadable_reopen_ends_in_transient(tmp_path: Path) -> None:
    """A reopen that fails on permissions maps onto a terminal TRANSIENT."""
    log = tmp_path / "app.log"
    log.write_text("a\nb\n", encoding="utf-8", newline="")

    stream = TailConnector().stream(_uri(log, **{"from": "start", "poll": 0.01}))
    try:
        assert _line(await _next(stream)) == "a"
        assert _line(await _next(stream)) == "b"

        # Truncate to force a reopen, then strip read permission so the
        # reopen raises -- the connector surfaces it as TRANSIENT.
        log.write_text("c\n", encoding="utf-8", newline="")
        os.chmod(log, 0)

        terminal = await _next(stream)
        assert isinstance(terminal, Error)
        assert terminal.kind == ErrorKind.TRANSIENT
    finally:
        await stream.aclose()  # type: ignore[attr-defined]
        os.chmod(log, 0o644)
