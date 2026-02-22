"""Tests for fetchers."""

import pytest
import asyncio
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.fetchers.local_file import LocalFileFetcher
from omni_fetcher.fetchers.http_url import HTTPURLFetcher
from omni_fetcher.fetchers.http_json import HTTPJSONFetcher


class TestBaseFetcher:
    def test_base_fetcher_is_abstract(self):
        """BaseFetcher cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseFetcher()


class TestLocalFileFetcher:
    def test_local_file_fetcher_creation(self):
        """Can create LocalFileFetcher."""
        fetcher = LocalFileFetcher()
        assert fetcher.name == "local_file"

    def test_local_file_fetcher_supports_file_scheme(self):
        """LocalFileFetcher supports file:// URIs."""
        assert LocalFileFetcher.can_handle("file:///path/to/file.txt")

    def test_local_file_fetcher_rejects_non_file(self):
        """LocalFileFetcher rejects non-file URIs."""
        assert not LocalFileFetcher.can_handle("https://example.com/file.txt")
        assert not LocalFileFetcher.can_handle("http://api.example.com/data")

    @pytest.mark.asyncio
    async def test_fetch_nonexistent_file_raises(self):
        """Fetching non-existent file raises error."""
        fetcher = LocalFileFetcher()

        with patch("pathlib.Path.exists", return_value=False):
            with pytest.raises(FileNotFoundError):
                await fetcher.fetch("/nonexistent/file.txt")


class TestHTTPURLFetcher:
    def test_http_url_fetcher_creation(self):
        """Can create HTTPURLFetcher."""
        fetcher = HTTPURLFetcher()
        assert fetcher.name == "http_url"

    def test_http_url_fetcher_supports_http_https(self):
        """HTTPURLFetcher supports http:// and https:// URIs."""
        assert HTTPURLFetcher.can_handle("http://example.com")
        assert HTTPURLFetcher.can_handle("https://example.com")
        assert HTTPURLFetcher.can_handle("https://example.com/path?query=1")

    def test_http_url_fetcher_rejects_non_http(self):
        """HTTPURLFetcher rejects non-HTTP URIs."""
        assert not HTTPURLFetcher.can_handle("file:///path/to/file.txt")
        assert not HTTPURLFetcher.can_handle("ftp://example.com/file.txt")


class TestHTTPJSONFetcher:
    def test_http_json_fetcher_creation(self):
        """Can create HTTPJSONFetcher."""
        fetcher = HTTPJSONFetcher()
        assert fetcher.name == "http_json"

    def test_http_json_fetcher_supports_json_urls(self):
        """HTTPJSONFetcher identifies JSON endpoints."""
        assert HTTPJSONFetcher.can_handle("https://api.example.com/data.json")
        assert HTTPJSONFetcher.can_handle("https://api.example.com/users")
        assert HTTPJSONFetcher.can_handle("https://api.example.com/data?format=json")

    def test_http_json_fetcher_priority(self):
        """HTTPJSONFetcher has higher priority for JSON URLs."""
        assert HTTPJSONFetcher.priority < 100


class TestFetcherRegistration:
    def test_fetchers_can_be_imported(self):
        """Built-in fetchers can be imported."""
        # These should not raise
        LocalFileFetcher()
        HTTPURLFetcher()
        HTTPJSONFetcher()
