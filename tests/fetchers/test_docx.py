"""Tests for DOCX fetcher."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from omni_fetcher.fetchers.docx import DOCXFetcher
from omni_fetcher.schemas.documents import DOCXDocument


class TestDOCXFetcher:
    """Basic creation tests."""

    def test_creation(self):
        """Can create DOCXFetcher."""
        fetcher = DOCXFetcher()
        assert fetcher.name == "docx"
        assert fetcher.priority == 20

    def test_can_handle_docx(self):
        """DOCXFetcher identifies .docx files."""
        assert DOCXFetcher.can_handle("document.docx")
        assert DOCXFetcher.can_handle("https://example.com/doc.docx")
        assert DOCXFetcher.can_handle("DOCUMENT.DOCX")

    def test_cannot_handle_non_docx(self):
        """DOCXFetcher rejects non-DOCX files."""
        assert not DOCXFetcher.can_handle("document.doc")
        assert not DOCXFetcher.can_handle("document.pdf")
        assert not DOCXFetcher.can_handle("document.txt")

    def test_priority(self):
        """DOCXFetcher has correct priority."""
        assert DOCXFetcher.priority == 20

    def test_creation_with_timeout(self):
        """Can create DOCXFetcher with custom timeout."""
        fetcher = DOCXFetcher(timeout=120.0)
        assert fetcher.timeout == 120.0


class TestDOCXFetcherFetch:
    """Tests for fetch method."""

    @pytest.mark.asyncio
    async def test_fetch_local_docx(self):
        """Fetches local DOCX file."""
        fetcher = DOCXFetcher()

        mock_doc = MagicMock()
        mock_doc.paragraphs = [MagicMock(text="Hello World")]
        mock_doc.tables = []
        mock_doc.core_properties = MagicMock(
            title="Test Doc",
            author="John Doe",
            created=datetime(2024, 1, 1),
            modified=datetime(2024, 1, 2),
        )
        mock_doc.part.rels = {}

        with patch("omni_fetcher.fetchers.docx.Path.read_bytes", return_value=b"fake docx data"):
            with patch("docx.Document", return_value=mock_doc):
                result = await fetcher.fetch("file:///test.docx")

        assert isinstance(result, DOCXDocument)
        assert result.title == "Test Doc"
        assert result.author == "John Doe"

    @pytest.mark.asyncio
    async def test_fetch_docx_with_text(self):
        """DOCX with text extracts text."""
        fetcher = DOCXFetcher()

        mock_doc = MagicMock()
        mock_doc.paragraphs = [
            MagicMock(text="Paragraph 1"),
            MagicMock(text="Paragraph 2"),
        ]
        mock_doc.tables = []
        mock_doc.core_properties = MagicMock(title=None, author=None, created=None, modified=None)
        mock_doc.part.rels = {}

        with patch("omni_fetcher.fetchers.docx.Path.read_bytes", return_value=b"fake docx data"):
            with patch("docx.Document", return_value=mock_doc):
                result = await fetcher.fetch("file:///test.docx")

        assert "Paragraph 1" in result.text.content
        assert "Paragraph 2" in result.text.content

    @pytest.mark.asyncio
    async def test_fetch_docx_with_tables(self):
        """DOCX with tables extracts tables."""
        fetcher = DOCXFetcher()

        mock_row = MagicMock()
        mock_row.cells = [MagicMock(text="Cell1"), MagicMock(text="Cell2")]

        mock_table = MagicMock()
        mock_table.rows = [mock_row]

        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = [mock_table]
        mock_doc.core_properties = MagicMock(title=None, author=None, created=None, modified=None)
        mock_doc.part.rels = {}

        with patch("omni_fetcher.fetchers.docx.Path.read_bytes", return_value=b"fake docx data"):
            with patch("docx.Document", return_value=mock_doc):
                result = await fetcher.fetch("file:///test.docx")

        assert len(result.tables) == 1

    @pytest.mark.asyncio
    async def test_fetch_docx_with_images(self):
        """DOCX with images extracts images."""
        fetcher = DOCXFetcher()

        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = []
        mock_doc.core_properties = MagicMock(title=None, author=None, created=None, modified=None)

        mock_rel = MagicMock()
        mock_rel.reltype = (
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
        )
        mock_rel.target_part = MagicMock(
            content_type="image/png",
            blob=b"fake image data",
        )
        mock_rel.target_ref = "media/image1.png"

        mock_doc.part.rels.values = MagicMock(return_value=[mock_rel])

        with patch("omni_fetcher.fetchers.docx.Path.read_bytes", return_value=b"fake docx data"):
            with patch("docx.Document", return_value=mock_doc):
                result = await fetcher.fetch("file:///test.docx")

        assert len(result.images) == 1

    @pytest.mark.asyncio
    async def test_fetch_docx_no_images(self):
        """DOCX without images returns empty list."""
        fetcher = DOCXFetcher()

        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = []
        mock_doc.core_properties = MagicMock(title=None, author=None, created=None, modified=None)
        mock_doc.part.rels = {}

        with patch("omni_fetcher.fetchers.docx.Path.read_bytes", return_value=b"fake docx data"):
            with patch("docx.Document", return_value=mock_doc):
                result = await fetcher.fetch("file:///test.docx")

        assert result.images == []

    @pytest.mark.asyncio
    async def test_fetch_docx_no_tables(self):
        """DOCX without tables returns empty list."""
        fetcher = DOCXFetcher()

        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = []
        mock_doc.core_properties = MagicMock(title=None, author=None, created=None, modified=None)
        mock_doc.part.rels = {}

        with patch("omni_fetcher.fetchers.docx.Path.read_bytes", return_value=b"fake docx data"):
            with patch("docx.Document", return_value=mock_doc):
                result = await fetcher.fetch("file:///test.docx")

        assert result.tables == []

    @pytest.mark.asyncio
    async def test_fetch_docx_empty_document(self):
        """Empty DOCX returns empty text."""
        fetcher = DOCXFetcher()

        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_doc.tables = []
        mock_doc.core_properties = MagicMock(title=None, author=None, created=None, modified=None)
        mock_doc.part.rels = {}

        with patch("omni_fetcher.fetchers.docx.Path.read_bytes", return_value=b"fake docx data"):
            with patch("docx.Document", return_value=mock_doc):
                result = await fetcher.fetch("file:///test.docx")

        assert result.text.content == ""
