"""Tests for PDF fetcher."""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

from omni_fetcher.fetchers.pdf import PDFFetcher


class TestPDFFetcher:
    def test_pdf_fetcher_creation(self):
        """Can create PDFFetcher."""
        fetcher = PDFFetcher()
        assert fetcher.name == "pdf"

    def test_pdf_fetcher_supports_pdf_urls(self):
        """PDFFetcher identifies PDF URLs."""
        assert PDFFetcher.can_handle("https://example.com/file.pdf")
        assert PDFFetcher.can_handle("file:///path/to/document.pdf")

    def test_pdf_fetcher_rejects_non_pdf(self):
        """PDFFetcher rejects non-PDF URLs."""
        assert not PDFFetcher.can_handle("https://example.com/page.html")
        assert not PDFFetcher.can_handle("https://example.com/image.jpg")

    def test_pdf_fetcher_priority(self):
        """PDFFetcher has high priority."""
        assert PDFFetcher.priority < 50
