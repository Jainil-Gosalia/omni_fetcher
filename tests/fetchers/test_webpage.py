"""Tests for WebPage fetcher (HTTP URL fetcher for HTML content)."""

import pytest

from omni_fetcher.fetchers.http_url import HTTPURLFetcher
from omni_fetcher.schemas.documents import WebPageDocument
from omni_fetcher.schemas.atomics import TextDocument, ImageDocument
from omni_fetcher.schemas.base import DataCategory


class TestHTTPURLFetcher:
    def test_creation(self):
        """Can create HTTPURLFetcher."""
        fetcher = HTTPURLFetcher()
        assert fetcher.name == "http_url"
        assert fetcher.priority == 50

    def test_can_handle_http(self):
        """HTTPURLFetcher identifies HTTP/HTTPS URLs."""
        assert HTTPURLFetcher.can_handle("https://example.com/page.html")
        assert HTTPURLFetcher.can_handle("http://example.com/page.html")
        assert HTTPURLFetcher.can_handle("https://example.com/api/data")

    def test_cannot_handle_non_http(self):
        """HTTPURLFetcher rejects non-HTTP URLs."""
        assert not HTTPURLFetcher.can_handle("file:///test.pdf")
        assert not HTTPURLFetcher.can_handle("s3://bucket/file.txt")

    def test_priority(self):
        """HTTPURLFetcher has correct priority."""
        assert HTTPURLFetcher.priority == 50
