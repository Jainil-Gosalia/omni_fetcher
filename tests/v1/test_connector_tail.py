"""External-behaviour tests for the v1 ``tail`` connector (issue 01).

Everything runs against real temp files (no fakes needed for a local
source) with tight poll intervals and ``wait_for`` guards so nothing can
hang the suite:

- ``from=start`` yields existing lines, then follows appends indefinitely;
- each item is one ``Success`` with the contract's node shape (kind
  ``log_line``, one Text atom without the trailing newline, positions in
  ``source_extra["tail"]``, temporal stamps);
- ``from=end`` (the default) skips existing content; ``from=<byte>``
  resumes mid-file exactly;
- ``fetch()`` is a typed UNSUPPORTED, never a hang;
- a missing file is a typed NOT_FOUND that ends the stream;
- abandoning iteration releases the file handle (assertable natively on
  Windows: an open handle blocks deletion).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors.tail import TailConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Result, Success

pytestmark = pytest.mark.asyncio


def _uri(path: Path, **params: object) -> str:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    base = "tail://" + str(path).replace("\\", "/")
    return f"{base}?{query}" if query else base


async def _collect(stream: AsyncIterator[Result], count: int) -> list[Result]:
    """Pull ``count`` items with a hang guard, then close the stream."""
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


def _line(item: Result) -> str:
    assert isinstance(item, Success)
    atoms = item.tree.find_atoms(AtomKind.TEXT)
    assert len(atoms) == 1
    return atoms[0].content


async def _append_later(path: Path, text: str, delay: float = 0.05) -> None:
    await asyncio.sleep(delay)
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(text)


# ---------------------------------------------------------------------------
# Following


async def test_from_start_yields_existing_then_appended_lines(tmp_path: Path) -> None:
    """Existing content streams first; appended lines keep arriving."""
    log = tmp_path / "app.log"
    log.write_text("one\ntwo\n", encoding="utf-8", newline="")
    appender = asyncio.ensure_future(_append_later(log, "three\n"))

    stream = TailConnector().stream(_uri(log, **{"from": "start", "poll": 0.01}))
    items = await _collect(stream, 3)
    await appender

    assert [_line(item) for item in items] == ["one", "two", "three"]


async def test_node_shape_positions_and_temporal_order(tmp_path: Path) -> None:
    """Items carry the contract's node shape and resume positions."""
    log = tmp_path / "app.log"
    log.write_text("alpha\nbeta\n", encoding="utf-8", newline="")

    stream = TailConnector().stream(_uri(log, **{"from": "start", "poll": 0.01}))
    items = await _collect(stream, 2)

    first, second = items
    assert isinstance(first, Success) and isinstance(second, Success)
    for item in (first, second):
        node = item.tree
        assert node.metadata.kind == "log_line"
        assert node.children == [] or all(not hasattr(child, "children") for child in node.children)
        atom = node.find_atoms(AtomKind.TEXT)[0]
        assert atom.format == TextFormat.PLAIN
        assert not atom.content.endswith("\n")

    extra_one = first.tree.metadata.source_extra["tail"]
    extra_two = second.tree.metadata.source_extra["tail"]
    assert extra_one["path"] == str(log)
    assert extra_one["line_number"] == 1 and extra_two["line_number"] == 2
    # byte_offset is the resume point AFTER each line.
    assert extra_one["byte_offset"] == len("alpha\n")
    assert extra_two["byte_offset"] == len("alpha\nbeta\n")

    seq_one = first.tree.metadata.temporal.sequence
    seq_two = second.tree.metadata.temporal.sequence
    assert seq_one is not None and seq_two is not None and seq_two > seq_one


async def test_from_end_default_skips_existing_content(tmp_path: Path) -> None:
    """The default start position is EOF: only new lines arrive."""
    log = tmp_path / "app.log"
    log.write_text("old-line\n", encoding="utf-8", newline="")
    appender = asyncio.ensure_future(_append_later(log, "new-line\n"))

    stream = TailConnector().stream(_uri(log, poll=0.01))
    items = await _collect(stream, 1)
    await appender

    assert [_line(item) for item in items] == ["new-line"]


async def test_from_byte_offset_resumes_exactly(tmp_path: Path) -> None:
    """from=<byte> starts mid-file at the given offset."""
    log = tmp_path / "app.log"
    log.write_text("alpha\nbeta\n", encoding="utf-8", newline="")

    stream = TailConnector().stream(_uri(log, **{"from": len("alpha\n"), "poll": 0.01}))
    items = await _collect(stream, 1)

    assert _line(items[0]) == "beta"


async def test_existing_lines_arrive_without_waiting_a_poll(tmp_path: Path) -> None:
    """A long poll interval delays only the idle path, not available data."""
    log = tmp_path / "app.log"
    log.write_text("a\nb\nc\n", encoding="utf-8", newline="")

    stream = TailConnector().stream(_uri(log, **{"from": "start", "poll": 30}))
    items = await asyncio.wait_for(_collect(stream, 3), timeout=2.0)

    assert len(items) == 3


# ---------------------------------------------------------------------------
# Stream-only contract


async def test_fetch_is_typed_unsupported(tmp_path: Path) -> None:
    """fetch() fails fast with UNSUPPORTED, naming stream()."""
    log = tmp_path / "app.log"
    log.write_text("data\n", encoding="utf-8", newline="")

    result = await TailConnector().fetch(_uri(log))

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED
    assert "stream" in result.message


async def test_missing_file_is_typed_not_found(tmp_path: Path) -> None:
    """A nonexistent path yields one NOT_FOUND and the stream ends."""
    stream = TailConnector().stream(_uri(tmp_path / "absent.log", poll=0.01))

    items = [item async for item in stream]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.NOT_FOUND


async def test_abandoned_stream_releases_the_file_handle(tmp_path: Path) -> None:
    """Closing the iterator mid-stream frees the handle (Windows-proof)."""
    log = tmp_path / "app.log"
    log.write_text("one\ntwo\n", encoding="utf-8", newline="")

    stream = TailConnector().stream(_uri(log, **{"from": "start", "poll": 0.01}))
    first = await stream.__anext__()  # type: ignore[attr-defined]
    assert isinstance(first, Success)
    await stream.aclose()  # type: ignore[attr-defined]

    # On Windows an open handle makes unlink fail; success proves cleanup.
    log.unlink()
    assert not log.exists()


async def test_orchestrator_stream_close_releases_the_handle_too(
    tmp_path: Path,
) -> None:
    """Abandoning the ORCHESTRATOR's stream closes the inner generator.

    Regression: the orchestrator's pass-through generator must aclose()
    the connector's stream deterministically -- waiting for garbage
    collection leaks the file handle past the consumer's exit.
    """
    from omni_fetcher.v1 import OmniFetcher, builtin_registry

    log = tmp_path / "app.log"
    log.write_text("one\ntwo\n", encoding="utf-8", newline="")

    omni = OmniFetcher(builtin_registry())
    stream = omni.stream(_uri(log, **{"from": "start", "poll": 0.01}))
    first = await stream.__anext__()  # type: ignore[attr-defined]
    assert isinstance(first, Success)
    await stream.aclose()  # type: ignore[attr-defined]

    log.unlink()
    assert not log.exists()
