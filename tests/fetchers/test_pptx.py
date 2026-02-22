"""Tests for PPTX fetcher."""

import pytest

from omni_fetcher.fetchers.pptx import PPTXFetcher
from omni_fetcher.schemas.documents import PPTXDocument, SlideDocument
from omni_fetcher.schemas.atomics import TextDocument
from omni_fetcher.schemas.base import DataCategory, MediaType


class TestPPTXFetcher:
    def test_creation(self):
        """Can create PPTXFetcher."""
        fetcher = PPTXFetcher()
        assert fetcher.name == "pptx"
        assert fetcher.priority == 20

    def test_can_handle_pptx(self):
        """PPTXFetcher identifies PPTX URLs."""
        assert PPTXFetcher.can_handle("file:///test.pptx")
        assert PPTXFetcher.can_handle("https://example.com/file.pptx")
        assert PPTXFetcher.can_handle("http://example.com/presentation.PPTX")
        assert PPTXFetcher.can_handle("file:///path/to/presentation.pptx")

    def test_cannot_handle_non_pptx(self):
        """PPTXFetcher rejects non-PPTX URLs."""
        assert not PPTXFetcher.can_handle("file:///test.pdf")
        assert not PPTXFetcher.can_handle("https://example.com/file.docx")
        assert not PPTXFetcher.can_handle("https://example.com/page.html")
        assert not PPTXFetcher.can_handle("https://example.com/image.jpg")

    def test_priority(self):
        """PPTXFetcher has correct priority."""
        assert PPTXFetcher.priority == 20
