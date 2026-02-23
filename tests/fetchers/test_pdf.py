"""Tests for PDF fetcher."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from pathlib import Path
from io import BytesIO

from omni_fetcher.fetchers.pdf import PDFFetcher
from omni_fetcher.schemas.documents import PDFDocument


class TestPDFFetcher:
    """Basic creation tests."""

    def test_creation(self):
        """Can create PDFFetcher."""
        fetcher = PDFFetcher()
        assert fetcher.name == "pdf"
        assert fetcher.priority == 20

    def test_can_handle_pdf(self):
        """PDFFetcher identifies .pdf files."""
        assert PDFFetcher.can_handle("document.pdf")
        assert PDFFetcher.can_handle("https://example.com/file.pdf")
        assert PDFFetcher.can_handle("DOCUMENT.PDF")

    def test_cannot_handle_non_pdf(self):
        """PDFFetcher rejects non-PDF files."""
        assert not PDFFetcher.can_handle("document.doc")
        assert not PDFFetcher.can_handle("document.docx")
        assert not PDFFetcher.can_handle("document.txt")

    def test_priority(self):
        """PDFFetcher has correct priority."""
        assert PDFFetcher.priority == 20

    def test_creation_with_timeout(self):
        """Can create PDFFetcher with custom timeout."""
        fetcher = PDFFetcher(timeout=120.0)
        assert fetcher.timeout == 120.0


class TestPDFFetcherFetch:
    """Tests for fetch method."""

    def _make_mock_reader(self, page_count=1, metadata=None, page_texts=None):
        """Helper to create mock PDF reader."""
        mock_pages = []
        texts = page_texts or ["Page text content"] * page_count
        for text in texts:
            mock_page = MagicMock()
            mock_page.extract_text = MagicMock(return_value=text)
            mock_pages.append(mock_page)

        mock_reader = MagicMock()
        mock_reader.pages = mock_pages
        mock_reader.metadata = metadata or {}
        return mock_reader

    @pytest.mark.asyncio
    async def test_fetch_local_pdf(self):
        """Fetches local PDF file."""
        fetcher = PDFFetcher()
        mock_reader = self._make_mock_reader(metadata={"/Title": "Test PDF", "/Author": "John Doe"})

        with patch.object(Path, "read_bytes", return_value=b"fake pdf data"):
            with patch("omni_fetcher.fetchers.pdf.PdfReader", return_value=mock_reader):
                result = await fetcher.fetch("file:///test.pdf")

        assert isinstance(result, PDFDocument)
        assert result.author == "John Doe"

    @pytest.mark.asyncio
    async def test_fetch_pdf_extracts_text(self):
        """PDF extracts text from all pages."""
        fetcher = PDFFetcher()
        mock_reader = self._make_mock_reader(
            page_count=2, page_texts=["Page 1 text", "Page 2 text"]
        )

        with patch.object(Path, "read_bytes", return_value=b"fake pdf data"):
            with patch("omni_fetcher.fetchers.pdf.PdfReader", return_value=mock_reader):
                result = await fetcher.fetch("file:///test.pdf")

        assert "Page 1 text" in result.text.content
        assert "Page 2 text" in result.text.content

    @pytest.mark.asyncio
    async def test_fetch_pdf_page_count(self):
        """PDF returns correct page count."""
        fetcher = PDFFetcher()
        mock_reader = self._make_mock_reader(page_count=3)

        with patch.object(Path, "read_bytes", return_value=b"fake pdf data"):
            with patch("omni_fetcher.fetchers.pdf.PdfReader", return_value=mock_reader):
                result = await fetcher.fetch("file:///test.pdf")

        assert result.page_count == 3

    @pytest.mark.asyncio
    async def test_fetch_pdf_with_metadata(self):
        """PDF extracts all metadata fields."""
        fetcher = PDFFetcher()
        metadata = {
            "/Title": "Test Title",
            "/Author": "Test Author",
            "/Subject": "Test Subject",
            "/Creator": "Test Creator",
            "/Producer": "Test Producer",
            "/Lang": "en",
        }
        mock_reader = self._make_mock_reader(metadata=metadata)

        with patch.object(Path, "read_bytes", return_value=b"fake pdf data"):
            with patch("omni_fetcher.fetchers.pdf.PdfReader", return_value=mock_reader):
                result = await fetcher.fetch("file:///test.pdf")

        assert result.author == "Test Author"
        assert result.subject == "Test Subject"
        assert result.creator == "Test Creator"
        assert result.producer == "Test Producer"

    @pytest.mark.asyncio
    async def test_fetch_pdf_scanned_tag(self):
        """PDF with scanned tag is marked as scanned."""
        fetcher = PDFFetcher()
        mock_reader = self._make_mock_reader(page_texts=[""])

        with patch.object(Path, "read_bytes", return_value=b"fake pdf data"):
            with patch("omni_fetcher.fetchers.pdf.PdfReader", return_value=mock_reader):
                result = await fetcher.fetch("file:///test.pdf")

        assert "scanned" in result.tags

    @pytest.mark.asyncio
    async def test_fetch_pdf_no_scanned_tag(self):
        """PDF without scanned tag is marked as not scanned."""
        fetcher = PDFFetcher()
        mock_reader = self._make_mock_reader(page_texts=["Some text content"])

        with patch.object(Path, "read_bytes", return_value=b"fake pdf data"):
            with patch("omni_fetcher.fetchers.pdf.PdfReader", return_value=mock_reader):
                result = await fetcher.fetch("file:///test.pdf")

        assert "scanned" not in result.tags

    @pytest.mark.asyncio
    async def test_fetch_pdf_multipage(self):
        """PDF extracts text from multiple pages."""
        fetcher = PDFFetcher()
        mock_reader = self._make_mock_reader(
            page_count=3, page_texts=["Page 1", "Page 2", "Page 3"]
        )

        with patch.object(Path, "read_bytes", return_value=b"fake pdf data"):
            with patch("omni_fetcher.fetchers.pdf.PdfReader", return_value=mock_reader):
                result = await fetcher.fetch("file:///test.pdf")

        assert result.page_count == 3
        assert "Page 1" in result.text.content
        assert "Page 2" in result.text.content
        assert "Page 3" in result.text.content

    @pytest.mark.asyncio
    async def test_fetch_pdf_empty(self):
        """PDF with no pages returns empty text."""
        fetcher = PDFFetcher()
        mock_reader = self._make_mock_reader(page_count=0, page_texts=[])

        with patch.object(Path, "read_bytes", return_value=b"fake pdf data"):
            with patch("omni_fetcher.fetchers.pdf.PdfReader", return_value=mock_reader):
                result = await fetcher.fetch("file:///test.pdf")

        assert result.text.content == ""
