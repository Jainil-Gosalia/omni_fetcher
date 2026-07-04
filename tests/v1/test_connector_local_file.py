"""External-behaviour tests for the v1 ``local_file`` connector.

These tests exercise only the public surface of the connector: ``stream()``
and the inherited ``fetch()``. They create real temp files, drive the
connector, and assert that the output is a canonical ``CompositionNode`` tree
(advisory ``kind`` ``"file"``, content in ``Text``/``Table`` atoms,
descriptive data namespaced in ``source_extra["local_file"]``) wrapped in the
right ``Result`` arm. A missing file must return a *typed error*, never raise.
No connector internals are touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omni_fetcher.v1.atoms import AtomKind, Table, Text, TextFormat
from omni_fetcher.v1.connectors.local_file import (
    FILE_KIND,
    SOURCE_NAMESPACE,
    LocalFileFetcher,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Error, Partial, Success


@pytest.fixture
def fetcher() -> LocalFileFetcher:
    """A fresh connector instance under test."""
    return LocalFileFetcher()


async def _collect(fetcher: LocalFileFetcher, uri: str):
    """Drain ``stream()`` into a list of results (bounded source)."""
    items = []
    async for item in fetcher.stream(uri):
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Text files -> Text atom


async def test_text_file_yields_canonical_file_node(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """A plain text file becomes a kind='file' node with one Text atom."""
    target = tmp_path / "note.txt"
    target.write_text("hello world", encoding="utf-8")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    tree = result.tree
    assert isinstance(tree, CompositionNode)
    assert tree.metadata.kind == FILE_KIND

    atoms = list(tree.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind is AtomKind.TEXT
    assert atoms[0].content == "hello world"
    assert atoms[0].format is TextFormat.PLAIN


async def test_markdown_file_uses_markdown_format(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """A .md file is mapped to a Text atom with MARKDOWN format."""
    target = tmp_path / "readme.md"
    target.write_text("# Title\n\nbody", encoding="utf-8")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    atom = next(result.tree.iter_atoms())
    assert isinstance(atom, Text)
    assert atom.format is TextFormat.MARKDOWN


async def test_descriptive_fields_live_in_source_extra(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """Path/size/mime/mtime are namespaced in source_extra, not on the atom."""
    target = tmp_path / "data.txt"
    target.write_text("abc", encoding="utf-8")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    md = result.tree.metadata
    extra = md.source_extra[SOURCE_NAMESPACE]
    assert extra["name"] == "data.txt"
    assert extra["size"] == 3
    assert "mtime" in extra
    assert extra["path"].endswith("data.txt")

    # Descriptive data is NOT inlined onto the content atom (content-only).
    atom = next(result.tree.iter_atoms())
    assert set(atom.model_dump().keys()) == {
        "kind",
        "content",
        "format",
        "language",
        "encoding",
    }


async def test_source_url_set_on_metadata(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """The requested URI is recorded as the node's source_url."""
    target = tmp_path / "x.txt"
    target.write_text("x", encoding="utf-8")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    assert result.tree.metadata.source_url == str(target)


async def test_stream_stamps_temporal_position(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """A streamed node carries a monotonic sequence + wall-clock timestamp."""
    target = tmp_path / "x.txt"
    target.write_text("x", encoding="utf-8")

    items = await _collect(fetcher, str(target))

    assert len(items) == 1
    assert isinstance(items[0], Success)
    temporal = items[0].tree.metadata.temporal
    assert temporal.sequence == 0
    assert temporal.timestamp is not None
    assert temporal.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# CSV/TSV files -> Table atom


async def test_csv_file_yields_table_atom(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """A CSV file becomes a kind='file' node carrying one Table atom."""
    target = tmp_path / "rows.csv"
    target.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert len(atoms) == 1
    table = atoms[0]
    assert isinstance(table, Table)
    assert table.kind is AtomKind.TABLE
    assert table.headers == ["a", "b"]
    assert table.rows == [["1", "2"], ["3", "4"]]


async def test_tsv_file_yields_table_atom(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """A TSV file is parsed with a tab delimiter into a Table atom."""
    target = tmp_path / "rows.tsv"
    target.write_text("x\ty\n1\t2\n", encoding="utf-8")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    table = next(result.tree.iter_atoms())
    assert isinstance(table, Table)
    assert table.headers == ["x", "y"]
    assert table.rows == [["1", "2"]]


# ---------------------------------------------------------------------------
# Error handling -- returned, never raised


async def test_missing_file_returns_typed_error(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """A missing file returns a NOT_FOUND error (no exception raised)."""
    missing = tmp_path / "does_not_exist.txt"

    result = await fetcher.fetch(str(missing))

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND
    assert result.locator == str(missing)


async def test_missing_file_stream_does_not_raise(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """stream() yields a typed Error for a missing file rather than raising."""
    missing = tmp_path / "nope.txt"

    items = await _collect(fetcher, str(missing))

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind is ErrorKind.NOT_FOUND


async def test_directory_returns_invalid_input(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """Pointing at a directory returns INVALID_INPUT, not a success."""
    result = await fetcher.fetch(str(tmp_path))

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.INVALID_INPUT


async def test_binary_file_returns_partial_with_gap(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """A binary (non-text/table) file is partial with an UNSUPPORTED gap."""
    target = tmp_path / "blob.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Partial)
    # The node is still canonical and descriptive data is recorded.
    assert result.tree.metadata.kind == FILE_KIND
    assert SOURCE_NAMESPACE in result.tree.metadata.source_extra
    # The undecoded content is surfaced explicitly as a gap, never silently.
    assert len(result.gaps) == 1
    assert result.gaps[0].kind is ErrorKind.UNSUPPORTED


# ---------------------------------------------------------------------------
# URI handling + can_handle


async def test_file_uri_is_accepted(
    fetcher: LocalFileFetcher, tmp_path: Path
) -> None:
    """A file:// URI resolves to the same content as a bare path."""
    target = tmp_path / "u.txt"
    target.write_text("content", encoding="utf-8")
    uri = target.as_uri()

    result = await fetcher.fetch(uri)

    assert isinstance(result, Success)
    atom = next(result.tree.iter_atoms())
    assert atom.content == "content"


def test_can_handle_classifies_uris() -> None:
    """can_handle claims file:// URIs and absolute paths only."""
    assert LocalFileFetcher.can_handle("file:///tmp/x.txt") is True
    assert LocalFileFetcher.can_handle("relative/path.txt") is False
