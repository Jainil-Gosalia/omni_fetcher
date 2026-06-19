"""External-behaviour tests for the v1 ``pdf`` connector.

These tests exercise only the public surface of
:class:`omni_fetcher.v1.connectors.pdf.PDFConnector`:

- a PDF with a text layer yields a ``Success`` document node carrying a
  ``Text`` atom per page and PDF descriptive fields under
  ``source_extra["pdf"]`` (never inline on the atom);
- a page with no text layer is reported as an ``UNSUPPORTED`` gap inside a
  ``Partial`` -- never OCR'd, never a silently blank ``Success``;
- a fully scanned PDF (zero extractable text) is a ``Partial`` with one gap
  per page, not a blank ``Success``;
- a missing local file and an unreadable PDF are returned as typed errors.

No network is used: every test drives a real local ``file://`` path
(against a temp file) so retrieval never touches HTTP. The ``pypdf`` reader
is monkeypatched with a tiny fake so extraction is fully deterministic.
"""

from __future__ import annotations

from datetime import datetime

import pytest

import omni_fetcher.v1.connectors.pdf as pdf_module
from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors.pdf import PDFConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success


@pytest.fixture
def pdf_uri(tmp_path):
    """A local ``file://`` URI for a placeholder PDF on disk (no network)."""
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 placeholder bytes")
    return pdf_path.as_uri()


class _FakePage:
    """A fake ``pypdf`` page returning a fixed extracted-text string."""

    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakeReader:
    """A fake ``pypdf.PdfReader`` over fixed pages and metadata."""

    def __init__(self, pages, metadata) -> None:
        self.pages = [_FakePage(text) for text in pages]
        self.metadata = metadata


def _install_fake_reader(monkeypatch, pages, metadata=None) -> None:
    """Patch the connector's ``PdfReader`` to return a fixed fake reader."""

    def _factory(_stream):
        return _FakeReader(pages, metadata)

    monkeypatch.setattr(pdf_module, "PdfReader", _factory)


# ---------------------------------------------------------------------------
# Text-layer PDF -> Success with Text atoms + pdf metadata


async def test_text_pdf_yields_success_with_text_atom(
    monkeypatch, pdf_uri
) -> None:
    """A PDF with a text layer is a Success carrying a Text atom."""
    _install_fake_reader(
        monkeypatch,
        pages=["Hello from page one."],
        metadata={"/Title": "Sample", "/Author": "Ada"},
    )
    connector = PDFConnector()

    result = await connector.fetch(pdf_uri)

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind is AtomKind.TEXT
    assert atoms[0].content == "Hello from page one."
    assert atoms[0].format is TextFormat.PLAIN


async def test_text_pdf_node_kind_is_document(monkeypatch, pdf_uri) -> None:
    """The PDF maps onto a node with the advisory ``document`` kind."""
    _install_fake_reader(monkeypatch, pages=["body"])
    connector = PDFConnector()

    result = await connector.fetch(pdf_uri)

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == "document"


async def test_pdf_metadata_lives_in_source_extra(
    monkeypatch, pdf_uri
) -> None:
    """Descriptive PDF fields are namespaced under source_extra['pdf']."""
    _install_fake_reader(
        monkeypatch,
        pages=["a", "b"],
        metadata={
            "/Title": "Sample",
            "/Author": "Ada",
            "/CreationDate": "D:20240115000000",
        },
    )
    connector = PDFConnector()

    result = await connector.fetch(pdf_uri)

    assert isinstance(result, Success)
    extra = result.tree.metadata.source_extra["pdf"]
    assert extra["title"] == "Sample"
    assert extra["author"] == "Ada"
    assert extra["page_count"] == 2

    # Common core is populated from the descriptive metadata.
    md = result.tree.metadata
    assert md.author == "Ada"
    assert md.created == datetime(2024, 1, 15)
    assert md.source_url == pdf_uri


async def test_descriptive_fields_not_inlined_on_atom(
    monkeypatch, pdf_uri
) -> None:
    """Descriptive metadata never leaks onto the content atom."""
    _install_fake_reader(
        monkeypatch,
        pages=["content"],
        metadata={"/Title": "Sample", "/Author": "Ada"},
    )
    connector = PDFConnector()

    result = await connector.fetch(pdf_uri)

    assert isinstance(result, Success)
    atom = next(result.tree.iter_atoms())
    # The atom carries content only -- no author/title/page_count fields.
    assert set(atom.model_dump().keys()) == {
        "kind",
        "content",
        "format",
        "language",
        "encoding",
    }


async def test_multi_page_pdf_yields_one_atom_per_page(
    monkeypatch, pdf_uri
) -> None:
    """Each text-bearing page contributes its own Text atom, in order."""
    _install_fake_reader(monkeypatch, pages=["one", "two", "three"])
    connector = PDFConnector()

    result = await connector.fetch(pdf_uri)

    assert isinstance(result, Success)
    contents = [a.content for a in result.tree.iter_atoms()]
    assert contents == ["one", "two", "three"]


# ---------------------------------------------------------------------------
# No text layer -> Partial / UNSUPPORTED (no OCR, no blank success)


async def test_scanned_page_reported_as_unsupported_gap(
    monkeypatch, pdf_uri
) -> None:
    """A page with no text layer is an UNSUPPORTED gap in a Partial."""
    _install_fake_reader(monkeypatch, pages=["readable text", "   "])
    connector = PDFConnector()

    result = await connector.fetch(pdf_uri)

    assert isinstance(result, Partial)
    # The readable page is preserved as content.
    assert [a.content for a in result.tree.iter_atoms()] == ["readable text"]
    # The empty page is reported, not OCR'd or silently dropped.
    assert len(result.gaps) == 1
    assert result.gaps[0].kind is ErrorKind.UNSUPPORTED
    assert "OCR is Phase 2" in (result.gaps[0].detail or "")
    assert result.gaps[0].locator == f"{pdf_uri}#page=2"


async def test_fully_scanned_pdf_is_partial_not_blank_success(
    monkeypatch, pdf_uri
) -> None:
    """A PDF with zero extractable text is a Partial, never a Success."""
    _install_fake_reader(monkeypatch, pages=["", "   ", ""])
    connector = PDFConnector()

    result = await connector.fetch(pdf_uri)

    # Critical: not a blank Success, and no OCR was performed.
    assert not isinstance(result, Success)
    assert isinstance(result, Partial)
    # One gap per page; no text atoms were fabricated.
    assert list(result.tree.iter_atoms()) == []
    assert len(result.gaps) == 3
    assert all(g.kind is ErrorKind.UNSUPPORTED for g in result.gaps)


# ---------------------------------------------------------------------------
# Error paths


async def test_missing_local_file_is_error(monkeypatch, tmp_path) -> None:
    """A missing local PDF is returned as a typed Error, not raised."""
    connector = PDFConnector()
    missing = tmp_path / "nope.pdf"

    result = await connector.fetch(missing.as_uri())

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND


async def test_unreadable_pdf_is_parse_error(monkeypatch, tmp_path) -> None:
    """An unreadable PDF surfaces a PARSE_ERROR (real file:// path)."""

    def _boom(_stream):
        raise ValueError("not a PDF")

    monkeypatch.setattr(pdf_module, "PdfReader", _boom)

    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"not really a pdf")
    connector = PDFConnector()

    result = await connector.fetch(pdf_path.as_uri())

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.PARSE_ERROR


async def test_can_handle_recognises_pdf_suffix() -> None:
    """``can_handle`` claims only ``.pdf`` URIs."""
    assert PDFConnector.can_handle("https://example.com/a.PDF")
    assert PDFConnector.can_handle("file:///tmp/x.pdf")
    assert not PDFConnector.can_handle("https://example.com/a.txt")


async def test_local_file_uri_round_trips(monkeypatch, tmp_path) -> None:
    """The real file:// retrieval path reads bytes and extracts text."""
    _install_fake_reader(monkeypatch, pages=["from disk"])

    pdf_path = tmp_path / "local.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake bytes")
    connector = PDFConnector()

    result = await connector.fetch(pdf_path.as_uri())

    assert isinstance(result, Success)
    assert [a.content for a in result.tree.iter_atoms()] == ["from disk"]
