"""Tests for PPTX fetcher."""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime
from pathlib import Path

from omni_fetcher.fetchers.pptx import PPTXFetcher
from omni_fetcher.schemas.documents import PPTXDocument, SlideDocument


class TestPPTXFetcher:
    """Basic creation tests."""

    def test_creation(self):
        """Can create PPTXFetcher."""
        fetcher = PPTXFetcher()
        assert fetcher.name == "pptx"
        assert fetcher.priority == 20

    def test_can_handle_pptx(self):
        """PPTXFetcher identifies .pptx files."""
        assert PPTXFetcher.can_handle("presentation.pptx")
        assert PPTXFetcher.can_handle("https://example.com/deck.pptx")
        assert PPTXFetcher.can_handle("PRESENTATION.PPTX")

    def test_cannot_handle_non_pptx(self):
        """PPTXFetcher rejects non-PPTX files."""
        assert not PPTXFetcher.can_handle("presentation.ppt")
        assert not PPTXFetcher.can_handle("presentation.pdf")
        assert not PPTXFetcher.can_handle("presentation.docx")

    def test_priority(self):
        """PPTXFetcher has correct priority."""
        assert PPTXFetcher.priority == 20

    def test_creation_with_timeout(self):
        """Can create PPTXFetcher with custom timeout."""
        fetcher = PPTXFetcher(timeout=120.0)
        assert fetcher.timeout == 120.0


class TestPPTXFetcherFetch:
    """Tests for fetch method."""

    def _make_mock_slide(self, title=None, shapes_texts=None, notes_text=None):
        """Helper to create mock slides."""
        mock_title = MagicMock()
        mock_title.text = title if title else ""

        mock_shapes = MagicMock()
        mock_shapes.title = mock_title
        if shapes_texts:
            mock_shape = MagicMock()
            mock_shape.text = shapes_texts
            mock_shapes.__iter__ = MagicMock(return_value=iter([mock_shape]))
        else:
            mock_shapes.__iter__ = MagicMock(return_value=iter([]))

        mock_slide = MagicMock()
        mock_slide.shapes = mock_shapes

        if notes_text:
            mock_notes_frame = MagicMock()
            mock_notes_frame.text = notes_text
            mock_notes_slide = MagicMock()
            mock_notes_slide.notes_text_frame = mock_notes_frame
            mock_slide.notes_slide = mock_notes_slide
        else:
            mock_slide.notes_slide = None

        return mock_slide

    @pytest.mark.asyncio
    async def test_fetch_local_pptx(self):
        """Fetches local PPTX file."""
        fetcher = PPTXFetcher()

        mock_slide = self._make_mock_slide(title="Slide Title")

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        mock_prs.core_properties = MagicMock(
            title="Test Presentation",
            author="John Doe",
        )

        with patch.object(Path, "read_bytes", return_value=b"fake pptx data"):
            with patch("pptx.Presentation", return_value=mock_prs):
                result = await fetcher.fetch("file:///test.pptx")

        assert isinstance(result, PPTXDocument)
        assert result.title == "Test Presentation"
        assert result.author == "John Doe"

    @pytest.mark.asyncio
    async def test_fetch_pptx_with_slides(self):
        """PPTX with slides extracts slide content."""
        fetcher = PPTXFetcher()

        mock_slide = self._make_mock_slide(shapes_texts="Slide text content")

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        mock_prs.core_properties = MagicMock(title=None, author=None)

        with patch.object(Path, "read_bytes", return_value=b"fake pptx data"):
            with patch("pptx.Presentation", return_value=mock_prs):
                result = await fetcher.fetch("file:///test.pptx")

        assert result.slide_count == 1
        assert len(result.slides) == 1
        assert "Slide text content" in result.slides[0].text.content

    @pytest.mark.asyncio
    async def test_fetch_pptx_slide_with_title(self):
        """PPTX slide with title extracts title."""
        fetcher = PPTXFetcher()

        mock_slide = self._make_mock_slide(title="My Slide Title")

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        mock_prs.core_properties = MagicMock(title=None, author=None)

        with patch.object(Path, "read_bytes", return_value=b"fake pptx data"):
            with patch("pptx.Presentation", return_value=mock_prs):
                result = await fetcher.fetch("file:///test.pptx")

        assert result.slides[0].title == "My Slide Title"

    @pytest.mark.asyncio
    async def test_fetch_pptx_slide_with_images(self):
        """PPTX slide with images extracts images."""
        fetcher = PPTXFetcher()

        mock_image = MagicMock()
        mock_image.blob = b"fake image data"
        mock_image.content_type = "image/png"

        mock_shape = MagicMock()
        mock_shape.image = mock_image
        mock_shape.text = ""

        mock_shapes = MagicMock()
        mock_shapes.title = None
        mock_shapes.__iter__ = MagicMock(return_value=iter([mock_shape]))

        mock_slide = MagicMock()
        mock_slide.shapes = mock_shapes
        mock_slide.notes_slide = None

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        mock_prs.core_properties = MagicMock(title=None, author=None)

        with patch.object(Path, "read_bytes", return_value=b"fake pptx data"):
            with patch("pptx.Presentation", return_value=mock_prs):
                result = await fetcher.fetch("file:///test.pptx")

        assert len(result.slides[0].images) == 1

    @pytest.mark.asyncio
    async def test_fetch_pptx_slide_with_speaker_notes(self):
        """PPTX slide with speaker notes appends to text."""
        fetcher = PPTXFetcher()

        mock_slide = self._make_mock_slide(notes_text="Speaker notes text")

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        mock_prs.core_properties = MagicMock(title=None, author=None)

        with patch.object(Path, "read_bytes", return_value=b"fake pptx data"):
            with patch("pptx.Presentation", return_value=mock_prs):
                result = await fetcher.fetch("file:///test.pptx")

        assert "Speaker notes text" in result.slides[0].text.content

    @pytest.mark.asyncio
    async def test_fetch_pptx_empty_presentation(self):
        """Empty PPTX returns zero slides."""
        fetcher = PPTXFetcher()

        mock_prs = MagicMock()
        mock_prs.slides = []
        mock_prs.core_properties = MagicMock(title=None, author=None)

        with patch.object(Path, "read_bytes", return_value=b"fake pptx data"):
            with patch("pptx.Presentation", return_value=mock_prs):
                result = await fetcher.fetch("file:///test.pptx")

        assert result.slide_count == 0
        assert result.slides == []

    @pytest.mark.asyncio
    async def test_fetch_pptx_slide_no_images(self):
        """PPTX slide without images returns empty list."""
        fetcher = PPTXFetcher()

        mock_slide = self._make_mock_slide()

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        mock_prs.core_properties = MagicMock(title=None, author=None)

        with patch.object(Path, "read_bytes", return_value=b"fake pptx data"):
            with patch("pptx.Presentation", return_value=mock_prs):
                result = await fetcher.fetch("file:///test.pptx")

        assert result.slides[0].images == []
