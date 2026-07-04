"""External-behaviour tests for the v1 DOCX connector.

These tests exercise only the connector's public surface -- ``fetch()`` (the
inherited collector over the single-item ``stream()``) -- against real
``.docx`` bytes built in-process with python-docx. No network and no fixed
binary fixture file is required: the document is constructed deterministically
in a temp directory per test.

The assertions cover the canonical contract: a ``Success`` carrying a
``"document"`` node, body paragraphs and tables surfaced as ``Text`` / ``Table``
atoms, docx descriptive fields filed under ``source_extra["docx"]`` (not inline
on atoms), a missing file mapped to a typed ``NOT_FOUND`` error, and a corrupt
file mapped to a typed ``PARSE_ERROR``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omni_fetcher.v1.atoms import AtomKind, Table, Text
from omni_fetcher.v1.connectors.docx import (
    DOCUMENT_KIND,
    SOURCE_NAMESPACE,
    DocxConnector,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

docx = pytest.importorskip("docx")


def _write_sample_docx(path: Path) -> None:
    """Build a deterministic sample .docx with paragraphs and a table."""
    document = docx.Document()
    document.core_properties.title = "Sample Title"
    document.core_properties.author = "Ada Lovelace"
    document.add_paragraph("First paragraph.")
    document.add_paragraph("Second paragraph.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "h1"
    table.cell(0, 1).text = "h2"
    table.cell(1, 0).text = "a"
    table.cell(1, 1).text = "b"
    document.add_paragraph("Closing paragraph.")
    document.save(str(path))


@pytest.fixture
def sample_docx(tmp_path: Path) -> str:
    """A freshly built sample .docx on disk; returns its path string."""
    path = tmp_path / "sample.docx"
    _write_sample_docx(path)
    return str(path)


async def test_fetch_returns_document_success(sample_docx: str) -> None:
    """A readable .docx yields a Success carrying a 'document' node."""
    result = await DocxConnector().fetch(sample_docx)

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == DOCUMENT_KIND
    assert result.tree.metadata.source_url == sample_docx


async def test_body_text_surfaces_as_text_atoms(sample_docx: str) -> None:
    """Body paragraphs are emitted as Text atoms in document order."""
    result = await DocxConnector().fetch(sample_docx)

    assert isinstance(result, Success)
    texts = [
        atom.content
        for atom in result.tree.iter_atoms()
        if isinstance(atom, Text)
    ]
    assert "First paragraph." in texts
    assert "Second paragraph." in texts
    # Document order is preserved across the interleaved table.
    assert texts.index("First paragraph.") < texts.index("Closing paragraph.")


async def test_table_surfaces_as_table_atom(sample_docx: str) -> None:
    """A body table is emitted as a canonical Table atom."""
    result = await DocxConnector().fetch(sample_docx)

    assert isinstance(result, Success)
    tables = result.tree.find_atoms(AtomKind.TABLE)
    assert len(tables) == 1
    table = tables[0]
    assert isinstance(table, Table)
    assert table.headers == ["h1", "h2"]
    assert table.rows == [["a", "b"]]


async def test_descriptive_fields_in_source_extra(sample_docx: str) -> None:
    """Title/author/flags live in metadata, never inline on atoms."""
    result = await DocxConnector().fetch(sample_docx)

    assert isinstance(result, Success)
    md = result.tree.metadata
    # Common core populated from the document.
    assert md.author == "Ada Lovelace"
    # Source-specific descriptive data is namespaced.
    extra = md.source_extra[SOURCE_NAMESPACE]
    assert extra["title"] == "Sample Title"
    assert extra["has_tables"] is True
    assert extra["has_images"] is False
    # No descriptive field leaked onto a content atom (atoms are content-only).
    text_atom = next(
        atom for atom in result.tree.iter_atoms() if isinstance(atom, Text)
    )
    assert set(text_atom.model_dump().keys()) == {
        "kind",
        "content",
        "format",
        "language",
        "encoding",
    }


async def test_missing_file_is_not_found(tmp_path: Path) -> None:
    """A missing .docx path yields a typed NOT_FOUND error."""
    missing = str(tmp_path / "does-not-exist.docx")

    result = await DocxConnector().fetch(missing)

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND
    assert result.locator == missing


async def test_corrupt_file_is_parse_error(tmp_path: Path) -> None:
    """A corrupt .docx (not a real OOXML zip) yields a typed PARSE_ERROR."""
    corrupt = tmp_path / "corrupt.docx"
    corrupt.write_bytes(b"this is not a docx file")

    result = await DocxConnector().fetch(str(corrupt))

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.PARSE_ERROR
    assert result.locator == str(corrupt)


def test_can_handle_recognises_docx() -> None:
    """can_handle claims .docx URIs and rejects others."""
    assert DocxConnector.can_handle("/some/path/report.docx") is True
    assert DocxConnector.can_handle("file:///C:/docs/report.docx") is True
    assert DocxConnector.can_handle("/some/path/report.pdf") is False
