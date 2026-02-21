"""Tests for the CLI module."""

from unittest.mock import MagicMock, patch, AsyncMock
import json

import pytest
from typer.testing import CliRunner

from omni_fetcher.cli import app


runner = CliRunner()


class TestCLIVersion:
    """Tests for the version command."""

    def test_version_output(self):
        """Test that version command outputs version info."""
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "omni" in result.stdout.lower()
        assert "v" in result.stdout


class TestCLISources:
    """Tests for the sources command."""

    def test_sources_list(self):
        """Test listing all registered sources."""
        result = runner.invoke(app, ["sources", "--list"])
        assert result.exit_code == 0
        assert "Source" in result.stdout or "source" in result.stdout.lower()

    def test_sources_info(self):
        """Test getting info for a specific source."""
        result = runner.invoke(app, ["sources", "--info", "http_url"])
        assert result.exit_code == 0
        assert "http_url" in result.stdout.lower() or "Source" in result.stdout


class TestCLICache:
    """Tests for the cache commands."""

    def test_cache_stats(self):
        """Test cache stats command."""
        result = runner.invoke(app, ["cache", "stats"])
        assert result.exit_code == 0

    def test_cache_clear(self):
        """Test cache clear command with confirmation."""
        with patch("omni_fetcher.cli.get_cache") as mock_get_cache:
            mock_cache = AsyncMock()
            mock_get_cache.return_value = mock_cache

            result = runner.invoke(app, ["cache", "clear", "--yes"])
            assert result.exit_code == 0
            assert "success" in result.stdout.lower() or "clear" in result.stdout.lower()


class TestCLIFetch:
    """Tests for the fetch command."""

    def test_fetch_url_success(self):
        """Test successful URL fetch with mocked response."""
        mock_result = MagicMock()
        mock_result.data = {"key": "value", "test": "data"}

        with patch("omni_fetcher.cli.get_fetcher") as mock_get_fetcher:
            mock_fetcher = AsyncMock()
            mock_fetcher.fetch.return_value = mock_result
            mock_get_fetcher.return_value = mock_fetcher

            with patch("omni_fetcher.cli.get_cache", return_value=None):
                result = runner.invoke(app, ["fetch", "https://example.com/data"])
                assert result.exit_code == 0

    def test_fetch_with_auth(self):
        """Test fetch with authentication options."""
        mock_result = MagicMock()
        mock_result.data = {"authenticated": True}

        with patch("omni_fetcher.cli.get_fetcher") as mock_get_fetcher:
            mock_fetcher = AsyncMock()
            mock_fetcher.fetch.return_value = mock_result
            mock_get_fetcher.return_value = mock_fetcher

            with patch("omni_fetcher.cli.get_cache", return_value=None):
                result = runner.invoke(
                    app,
                    [
                        "fetch",
                        "https://example.com/data",
                        "--auth-type",
                        "bearer",
                        "--auth-token",
                        "test_token",
                    ],
                )
                assert result.exit_code == 0

    def test_fetch_output_json(self):
        """Test fetch with JSON output format."""
        mock_result = MagicMock()
        mock_result.data = {"name": "test", "value": 123}

        with patch("omni_fetcher.cli.get_fetcher") as mock_get_fetcher:
            mock_fetcher = AsyncMock()
            mock_fetcher.fetch.return_value = mock_result
            mock_get_fetcher.return_value = mock_fetcher

            with patch("omni_fetcher.cli.get_cache", return_value=None):
                result = runner.invoke(
                    app,
                    ["fetch", "https://example.com/data", "--format", "json"],
                )
                assert result.exit_code == 0

    def test_fetch_output_text(self):
        """Test fetch with text output format."""
        mock_result = MagicMock()
        mock_result.data = "simple text content"

        with patch("omni_fetcher.cli.get_fetcher") as mock_get_fetcher:
            mock_fetcher = AsyncMock()
            mock_fetcher.fetch.return_value = mock_result
            mock_get_fetcher.return_value = mock_fetcher

            with patch("omni_fetcher.cli.get_cache", return_value=None):
                result = runner.invoke(
                    app,
                    ["fetch", "https://example.com/data", "--format", "text"],
                )
                assert result.exit_code == 0

    def test_fetch_with_basic_auth(self):
        """Test fetch with basic authentication."""
        mock_result = MagicMock()
        mock_result.data = {"status": "ok"}

        with patch("omni_fetcher.cli.get_fetcher") as mock_get_fetcher:
            mock_fetcher = AsyncMock()
            mock_fetcher.fetch.return_value = mock_result
            mock_get_fetcher.return_value = mock_fetcher

            with patch("omni_fetcher.cli.get_cache", return_value=None):
                result = runner.invoke(
                    app,
                    [
                        "fetch",
                        "https://example.com/data",
                        "--auth-type",
                        "basic",
                        "--auth-user",
                        "user",
                        "--auth-pass",
                        "password",
                    ],
                )
                assert result.exit_code == 0

    def test_fetch_with_api_key(self):
        """Test fetch with API key authentication."""
        mock_result = MagicMock()
        mock_result.data = {"api_key_auth": True}

        with patch("omni_fetcher.cli.get_fetcher") as mock_get_fetcher:
            mock_fetcher = AsyncMock()
            mock_fetcher.fetch.return_value = mock_result
            mock_get_fetcher.return_value = mock_fetcher

            with patch("omni_fetcher.cli.get_cache", return_value=None):
                result = runner.invoke(
                    app,
                    [
                        "fetch",
                        "https://example.com/data",
                        "--auth-type",
                        "api_key",
                        "--auth-token",
                        "my_api_key",
                    ],
                )
                assert result.exit_code == 0
