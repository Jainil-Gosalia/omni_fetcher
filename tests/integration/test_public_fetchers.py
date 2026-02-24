"""Integration tests for public (non-auth) fetchers.

These tests fetch data from real public URLs to verify that each fetcher
works correctly with actual data sources.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

from omni_fetcher.fetchers.local_file import LocalFileFetcher
from omni_fetcher.fetchers.http_url import HTTPURLFetcher
from omni_fetcher.fetchers.http_json import HTTPJSONFetcher
from omni_fetcher.fetchers.rss import RSSFetcher
from omni_fetcher.fetchers.csv import CSVFetcher
from omni_fetcher.fetchers.pdf import PDFFetcher
from omni_fetcher.fetchers.docx import DOCXFetcher
from omni_fetcher.fetchers.pptx import PPTXFetcher
from omni_fetcher.fetchers.youtube import YouTubeFetcher
from omni_fetcher.fetchers.audio import AudioFetcher
from omni_fetcher.fetchers.graphql import GraphQLFetcher

from omni_fetcher.schemas.documents import WebPageDocument, PDFDocument, DOCXDocument
from omni_fetcher.schemas.structured import JSONData, GraphQLResponse
from omni_fetcher.schemas.atomics import SpreadsheetDocument
from omni_fetcher.schemas.media import YouTubeVideo


PUBLIC_FETCHER_TESTS = [
    (
        "http_url",
        "https://example.com",
        HTTPURLFetcher,
        WebPageDocument,
    ),
    (
        "http_json",
        "https://jsonplaceholder.typicode.com/posts/1",
        HTTPJSONFetcher,
        JSONData,
    ),
    (
        "rss",
        "https://hnrss.org/frontpage",
        RSSFetcher,
        JSONData,
    ),
    (
        "csv",
        "https://people.sc.fsu.edu/~jburkardt/data/csv/addresses.csv",
        CSVFetcher,
        SpreadsheetDocument,
    ),
    (
        "pdf",
        "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
        PDFFetcher,
        PDFDocument,
    ),
    (
        "youtube",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        YouTubeFetcher,
        YouTubeVideo,
    ),
]


GRAPHQL_FETCHER_TEST = (
    "graphql",
    "https://countries.trevorblades.com/graphql",
    GraphQLFetcher,
    GraphQLResponse,
)


OFFICE_FETCHER_TESTS = [
    (
        "docx",
        "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf".replace(
            "pdf", "docx"
        ),
        DOCXFetcher,
        DOCXDocument,
    ),
]


SKIP_REASON_NO_OFFICE = "Office dependencies not installed (pip install omni_fetcher[office])"


def check_office_available():
    """Check if office dependencies are available."""
    try:
        import docx  # noqa: F401
        import pptx  # noqa: F401

        return True
    except ImportError:
        return False


def check_ssl_working():
    """Check if SSL connections work."""
    try:
        import httpx
        import asyncio

        async def _test():
            async with httpx.AsyncClient() as client:
                await client.get("https://example.com")

        asyncio.get_event_loop().run_until_complete(_test())
        return True
    except Exception:
        return False


SKIP_REASON_SSL = "SSL certificate verification not working in this environment"


def check_ssl_available():
    """Check if SSL connections work."""
    try:
        return True
    except Exception:
        return False


class TestPublicFetcherCanHandle:
    """Test that can_handle correctly identifies URIs for each fetcher."""

    @pytest.mark.parametrize(
        "fetcher_name,uri,fetcher_class,_",
        PUBLIC_FETCHER_TESTS,
    )
    def test_can_handle_returns_true(self, fetcher_name: str, uri: str, fetcher_class: Any, _: Any):
        """Each public fetcher can handle its expected URI pattern."""
        assert fetcher_class.can_handle(uri) is True, f"{fetcher_name} should handle {uri}"

    def test_graphql_can_handle_graphql_uri(self):
        """GraphQL fetcher can handle GraphQL URIs."""
        assert GraphQLFetcher.can_handle("https://api.example.com/graphql")
        assert GraphQLFetcher.can_handle("https://api.example.com/gql")


class TestPublicFetcherIntegration:
    """Integration tests that fetch from real public URLs."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fetcher_name,uri,fetcher_class,expected_schema",
        PUBLIC_FETCHER_TESTS,
    )
    async def test_fetcher_returns_expected_schema(
        self,
        fetcher_name: str,
        uri: str,
        fetcher_class: Any,
        expected_schema: Any,
    ):
        """Each fetcher returns the expected schema type."""
        if fetcher_name == "http_url":
            try:
                import httpx

                async with httpx.AsyncClient() as client:
                    await client.get("https://example.com")
            except Exception:
                pytest.skip("SSL not working in this environment")

        fetcher = fetcher_class()
        result = await fetcher.fetch(uri)
        assert result is not None, f"{fetcher_name} returned None for {uri}"
        assert isinstance(result, expected_schema), (
            f"{fetcher_name} returned {type(result).__name__}, expected {expected_schema.__name__}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fetcher_name,uri,fetcher_class,expected_schema",
        PUBLIC_FETCHER_TESTS,
    )
    async def test_fetcher_returns_valid_metadata(
        self,
        fetcher_name: str,
        uri: str,
        fetcher_class: Any,
        expected_schema: Any,
    ):
        """Each fetcher returns valid metadata in the result."""
        if fetcher_name == "http_url":
            try:
                import httpx

                async with httpx.AsyncClient() as client:
                    await client.get("https://example.com")
            except Exception:
                pytest.skip("SSL not working in this environment")

        fetcher = fetcher_class()
        result = await fetcher.fetch(uri)

        if hasattr(result, "source_uri"):
            assert result.source_uri is not None
            assert len(result.source_uri) > 0


class TestHTTPURLFetcherIntegration:
    """Specific integration tests for HTTP URL fetcher."""

    @pytest.mark.skipif(not check_ssl_working(), reason=SKIP_REASON_SSL)
    @pytest.mark.asyncio
    async def test_fetch_simple_html_page(self):
        """Can fetch a simple HTML page."""
        fetcher = HTTPURLFetcher()
        result = await fetcher.fetch("https://example.com")

        assert isinstance(result, WebPageDocument)
        assert result.url is not None
        assert "example" in result.url.lower()

    @pytest.mark.asyncio
    async def test_fetch_json_api(self):
        """Can detect and fetch JSON from HTTP endpoint."""
        fetcher = HTTPURLFetcher()
        result = await fetcher.fetch("https://jsonplaceholder.typicode.com/posts/1")

        assert isinstance(result, JSONData)
        assert result.data is not None


class TestHTTPJSONFetcherIntegration:
    """Specific integration tests for HTTP JSON fetcher."""

    @pytest.mark.asyncio
    async def test_fetch_jsonplaceholder(self):
        """Can fetch JSON from jsonplaceholder API."""
        fetcher = HTTPJSONFetcher()
        result = await fetcher.fetch("https://jsonplaceholder.typicode.com/users/1")

        assert isinstance(result, JSONData)
        assert result.data is not None
        assert "id" in result.data

    @pytest.mark.asyncio
    async def test_fetch_nested_json(self):
        """Can handle nested JSON structures."""
        fetcher = HTTPJSONFetcher()
        result = await fetcher.fetch("https://jsonplaceholder.typicode.com/posts/1/comments")

        assert isinstance(result, JSONData)
        assert isinstance(result.data, list)


class TestRSSFetcherIntegration:
    """Specific integration tests for RSS fetcher."""

    @pytest.mark.asyncio
    async def test_fetch_hackernews_rss(self):
        """Can fetch and parse Hacker News RSS feed."""
        fetcher = RSSFetcher()
        result = await fetcher.fetch("https://hnrss.org/frontpage")

        assert isinstance(result, JSONData)
        assert result.data is not None
        assert "entries" in result.data
        assert len(result.data["entries"]) > 0

    @pytest.mark.asyncio
    async def test_rss_entry_structure(self):
        """RSS feed entries have expected structure."""
        fetcher = RSSFetcher()
        result = await fetcher.fetch("https://hnrss.org/frontpage")

        entries = result.data.get("entries", [])
        if entries:
            entry = entries[0]
            assert "title" in entry
            assert "link" in entry


class TestCSVFetcherIntegration:
    """Specific integration tests for CSV fetcher."""

    @pytest.mark.asyncio
    async def test_fetch_public_csv(self):
        """Can fetch and parse a public CSV file."""
        fetcher = CSVFetcher()
        result = await fetcher.fetch("https://people.sc.fsu.edu/~jburkardt/data/csv/addresses.csv")

        assert isinstance(result, SpreadsheetDocument)
        assert result.sheets is not None
        assert len(result.sheets) > 0
        sheet = result.sheets[0]
        assert sheet.headers is not None
        assert len(sheet.headers) > 0


class TestPDFFetcherIntegration:
    """Specific integration tests for PDF fetcher."""

    @pytest.mark.asyncio
    async def test_fetch_w3c_pdf(self):
        """Can fetch and parse a PDF document."""
        fetcher = PDFFetcher()
        result = await fetcher.fetch(
            "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
        )

        assert isinstance(result, PDFDocument)
        assert result.page_count is not None

    @pytest.mark.asyncio
    async def test_pdf_has_text_content(self):
        """PDF document has extracted text."""
        fetcher = PDFFetcher()
        result = await fetcher.fetch(
            "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
        )

        assert result.text is not None
        assert hasattr(result.text, "content")


class TestYouTubeFetcherIntegration:
    """Specific integration tests for YouTube fetcher."""

    @pytest.mark.asyncio
    async def test_fetch_youtube_video_metadata(self):
        """Can fetch YouTube video metadata."""
        fetcher = YouTubeFetcher()
        result = await fetcher.fetch("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        assert isinstance(result, YouTubeVideo)
        assert result.video_id is not None
        assert result.title is not None

    @pytest.mark.asyncio
    async def test_youtube_video_has_uploader(self):
        """YouTube video has uploader information."""
        fetcher = YouTubeFetcher()
        result = await fetcher.fetch("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        assert result.uploader is not None


class TestGraphQLFetcherIntegration:
    """Specific integration tests for GraphQL fetcher."""

    @pytest.mark.asyncio
    async def test_fetch_countries_graphql(self):
        """Can fetch data from a public GraphQL endpoint."""
        fetcher = GraphQLFetcher(endpoint="https://countries.trevorblades.com/graphql")

        query = """
        {
            countries {
                code
                name
            }
        }
        """
        result = await fetcher.query(query)

        assert isinstance(result, GraphQLResponse)
        assert result.data is not None

    @pytest.mark.asyncio
    async def test_graphql_handles_variables(self):
        """GraphQL fetcher handles variables correctly."""
        fetcher = GraphQLFetcher(endpoint="https://countries.trevorblades.com/graphql")

        query = """
        query GetCountry($code: ID!) {
            country(code: $code) {
                code
                name
            }
        }
        """
        result = await fetcher.query(query, variables={"code": "US"})

        assert isinstance(result, GraphQLResponse)
        assert result.data is not None


class TestAudioFetcherIntegration:
    """Specific integration tests for audio fetcher."""

    def test_can_handle_audio_urls(self):
        """Audio fetcher identifies audio file URLs."""
        assert AudioFetcher.can_handle("https://example.com/audio.mp3")
        assert AudioFetcher.can_handle("https://example.com/sound.wav")
        assert AudioFetcher.can_handle("https://example.com/music.flac")

    def test_cannot_handle_non_audio(self):
        """Audio fetcher rejects non-audio URLs."""
        assert not AudioFetcher.can_handle("https://example.com/page.html")
        assert not AudioFetcher.can_handle("https://example.com/image.jpg")
        assert not AudioFetcher.can_handle("https://example.com/video.mp4")


class TestLocalFileFetcherIntegration:
    """Integration tests for local file fetcher with various file types."""

    @pytest.mark.asyncio
    async def test_fetch_text_file(self):
        """Can fetch a plain text file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello, World!")
            temp_path = f.name

        try:
            fetcher = LocalFileFetcher()
            result = await fetcher.fetch(temp_path)
            assert result.content == "Hello, World!"
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_fetch_json_file(self):
        """Can fetch a JSON file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"key": "value", "number": 42}')
            temp_path = f.name

        try:
            fetcher = LocalFileFetcher()
            result = await fetcher.fetch(temp_path)
            assert result.data["key"] == "value"
            assert result.data["number"] == 42
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_fetch_markdown_file(self):
        """Can fetch a markdown file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Hello\n\nThis is **bold** text.")
            temp_path = f.name

        try:
            fetcher = LocalFileFetcher()
            result = await fetcher.fetch(temp_path)
            assert "# Hello" in result.content
        finally:
            os.unlink(temp_path)


class TestFetcherRegistryIntegration:
    """Test that all public fetchers are available and can be instantiated."""

    def test_all_public_fetchers_available(self):
        """All public fetchers should be importable and instantiable."""
        fetchers_to_test = [
            ("local_file", LocalFileFetcher, {}),
            ("audio", AudioFetcher, {}),
            ("http_url", HTTPURLFetcher, {}),
            ("http_json", HTTPJSONFetcher, {}),
            ("youtube", YouTubeFetcher, {}),
            ("rss", RSSFetcher, {}),
            ("pdf", PDFFetcher, {}),
            ("csv", CSVFetcher, {}),
            ("graphql", GraphQLFetcher, {"endpoint": "https://api.example.com/graphql"}),
        ]

        for fetcher_name, fetcher_class, kwargs in fetchers_to_test:
            fetcher = fetcher_class(**kwargs)
            assert fetcher.name == fetcher_name, f"{fetcher_name} mismatch"


class TestOfficeFetchersAvailability:
    """Test office fetchers with conditional skipping."""

    @pytest.mark.skipif(not check_office_available(), reason=SKIP_REASON_NO_OFFICE)
    @pytest.mark.asyncio
    async def test_docx_fetcher_available(self):
        """DOCX fetcher can be imported and instantiated."""
        fetcher = DOCXFetcher()
        assert fetcher.name == "docx"

    @pytest.mark.skipif(not check_office_available(), reason=SKIP_REASON_NO_OFFICE)
    @pytest.mark.asyncio
    async def test_pptx_fetcher_available(self):
        """PPTX fetcher can be imported and instantiated."""
        fetcher = PPTXFetcher()
        assert fetcher.name == "pptx"

    def test_docx_can_handle_docx_urls(self):
        """DOCX fetcher identifies DOCX file URLs."""
        assert DOCXFetcher.can_handle("https://example.com/document.docx")
        assert DOCXFetcher.can_handle("https://example.com/file.DOCX")

    def test_pptx_can_handle_pptx_urls(self):
        """PPTX fetcher identifies PPTX file URLs."""
        assert PPTXFetcher.can_handle("https://example.com/presentation.pptx")
        assert PPTXFetcher.can_handle("https://example.com/file.PPTX")
