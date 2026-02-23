"""Tests for WebPage fetcher (HTTP URL fetcher for HTML content)."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from omni_fetcher.fetchers.http_url import HTTPURLFetcher
from omni_fetcher.schemas.documents import WebPageDocument
from omni_fetcher.schemas.atomics import TextDocument, ImageDocument, TextFormat
from omni_fetcher.schemas.structured import JSONData


class TestHTTPURLFetcher:
    """Basic creation tests."""

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

    def test_creation_with_timeout(self):
        """Can create HTTPURLFetcher with custom timeout."""
        fetcher = HTTPURLFetcher(timeout=60.0)
        assert fetcher.timeout == 60.0


class TestHTTPURLFetcherExtractTitle:
    """Tests for title extraction."""

    def test_extract_title_from_og_title(self):
        """Extracts title from og:title meta tag."""
        html = '<html><head><meta property="og:title" content="OG Title"></head></html>'
        fetcher = HTTPURLFetcher()
        title = fetcher._extract_title(html)
        assert title == "OG Title"

    def test_extract_title_from_title_tag(self):
        """Extracts title from <title> tag."""
        html = "<html><head><title>Page Title</title></head></html>"
        fetcher = HTTPURLFetcher()
        title = fetcher._extract_title(html)
        assert title == "Page Title"

    def test_extract_title_from_h1(self):
        """Extracts title from <h1> tag."""
        html = "<html><head></head><body><h1>Heading Title</h1></body></html>"
        fetcher = HTTPURLFetcher()
        title = fetcher._extract_title(html)
        assert title == "Heading Title"

    def test_extract_title_priority(self):
        """og:title takes priority over <title>."""
        html = '<html><head><title>Title Tag</title><meta property="og:title" content="OG Title"></head></html>'
        fetcher = HTTPURLFetcher()
        title = fetcher._extract_title(html)
        assert title == "OG Title"

    def test_extract_title_none(self):
        """Returns None when no title found."""
        html = "<html><head></head><body><p>No title here</p></body></html>"
        fetcher = HTTPURLFetcher()
        title = fetcher._extract_title(html)
        assert title is None


class TestHTTPURLFetcherExtractAuthor:
    """Tests for author extraction."""

    def test_extract_author_from_meta_name(self):
        """Extracts author from meta name=author."""
        html = '<html><head><meta name="author" content="John Doe"></head></html>'
        fetcher = HTTPURLFetcher()
        author = fetcher._extract_author(html)
        assert author == "John Doe"

    def test_extract_author_from_og(self):
        """Extracts author from og:article:author."""
        html = '<html><head><meta property="article:author" content="Jane Smith"></head></html>'
        fetcher = HTTPURLFetcher()
        author = fetcher._extract_author(html)
        assert author == "Jane Smith"

    def test_extract_author_from_twitter(self):
        """Extracts author from twitter:creator."""
        html = '<html><head><meta name="twitter:creator" content="@twitterhandle"></head></html>'
        fetcher = HTTPURLFetcher()
        author = fetcher._extract_author(html)
        assert author == "@twitterhandle"

    def test_extract_author_priority(self):
        """meta name=author takes priority."""
        html = '<html><head><meta name="author" content="Author1"><meta property="article:author" content="Author2"></head></html>'
        fetcher = HTTPURLFetcher()
        author = fetcher._extract_author(html)
        assert author == "Author1"

    def test_extract_author_none(self):
        """Returns None when no author found."""
        html = "<html><head></head><body><p>No author</p></body></html>"
        fetcher = HTTPURLFetcher()
        author = fetcher._extract_author(html)
        assert author is None


class TestHTTPURLFetcherExtractLanguage:
    """Tests for language extraction."""

    def test_extract_language_from_html_tag(self):
        """Extracts language from html lang attribute."""
        html = '<html lang="en"><head></head></html>'
        fetcher = HTTPURLFetcher()
        lang = fetcher._extract_language(html)
        assert lang == "en"

    def test_extract_language_from_meta(self):
        """Extracts language from content-language meta."""
        html = '<html><head><meta http-equiv="content-language" content="fr"></head></html>'
        fetcher = HTTPURLFetcher()
        lang = fetcher._extract_language(html)
        assert lang == "fr"

    def test_extract_language_priority(self):
        """html lang takes priority."""
        html = (
            '<html lang="de"><head><meta http-equiv="content-language" content="fr"></head></html>'
        )
        fetcher = HTTPURLFetcher()
        lang = fetcher._extract_language(html)
        assert lang == "de"

    def test_extract_language_none(self):
        """Returns None when no language found."""
        html = "<html><head></head><body><p>No language</p></body></html>"
        fetcher = HTTPURLFetcher()
        lang = fetcher._extract_language(html)
        assert lang is None


class TestHTTPURLFetcherExtractPublishedDate:
    """Tests for published date extraction."""

    def test_extract_date_from_schema_org(self):
        """Extracts date from schema.org JSON-LD."""
        html = """<html><head><script type="application/ld+json">{"datePublished": "2024-01-15T10:00:00Z"}</script></head></html>"""
        fetcher = HTTPURLFetcher()
        date = fetcher._extract_published_date(html)
        assert date is not None

    def test_extract_date_from_meta(self):
        """Extracts date from article:published_time."""
        html = '<html><head><meta property="article:published_time" content="2024-01-15T10:00:00Z"></head></html>'
        fetcher = HTTPURLFetcher()
        date = fetcher._extract_published_date(html)
        assert date is not None


class TestHTTPURLFetcherExtractImages:
    """Tests for image extraction."""

    def test_extract_images_from_main(self):
        """Extracts images from main content area."""
        html = """<html><body><main><img src="https://example.com/image1.jpg"></main><footer><img src="https://example.com/footer.jpg"></footer></body></html>"""
        fetcher = HTTPURLFetcher()
        images = fetcher._extract_images(html, "https://example.com/page")
        assert len(images) == 1
        assert images[0].source_uri == "https://example.com/image1.jpg"

    def test_extract_images_from_article(self):
        """Extracts images from article content area."""
        html = """<html><body><article><img src="https://example.com/article.jpg"></article></body></html>"""
        fetcher = HTTPURLFetcher()
        images = fetcher._extract_images(html, "https://example.com/page")
        assert len(images) == 1

    def test_extract_images_from_body(self):
        """Extracts images from body when no main/article."""
        html = """<html><body><img src="https://example.com/body.jpg"></body></html>"""
        fetcher = HTTPURLFetcher()
        images = fetcher._extract_images(html, "https://example.com/page")
        assert len(images) == 1

    def test_extract_images_absolute_url(self):
        """Handles absolute image URLs."""
        html = """<html><body><img src="https://other.com/image.jpg"></body></html>"""
        fetcher = HTTPURLFetcher()
        images = fetcher._extract_images(html, "https://example.com/page")
        assert len(images) == 1
        assert images[0].source_uri == "https://other.com/image.jpg"

    def test_extract_images_relative_url(self):
        """Converts relative image URLs to absolute."""
        html = """<html><body><img src="/images/logo.png"></body></html>"""
        fetcher = HTTPURLFetcher()
        images = fetcher._extract_images(html, "https://example.com/page")
        assert len(images) == 1
        assert images[0].source_uri == "https://example.com/images/logo.png"

    def test_extract_images_protocol_relative(self):
        """Handles protocol-relative URLs."""
        html = """<html><body><img src="//cdn.example.com/image.jpg"></body></html>"""
        fetcher = HTTPURLFetcher()
        images = fetcher._extract_images(html, "https://example.com/page")
        assert len(images) == 1
        assert images[0].source_uri == "https://cdn.example.com/image.jpg"

    def test_extract_images_no_images(self):
        """Returns empty list when no images."""
        html = "<html><body><p>No images here</p></body></html>"
        fetcher = HTTPURLFetcher()
        images = fetcher._extract_images(html, "https://example.com/page")
        assert images == []


class TestHTTPURLFetcherExtractText:
    """Tests for text extraction."""

    def test_extract_text_trafilatura(self):
        """Uses trafilatura when available."""
        html = "<html><body><p>Test content</p></body></html>"
        fetcher = HTTPURLFetcher()

        with patch("omni_fetcher.fetchers.http_url.trafilatura") as mock_trafilatura:
            mock_trafilatura.extract = MagicMock(return_value="Extracted by trafilatura")

            text, fmt = fetcher._extract_text(html)
            assert text == "Extracted by trafilatura"
            assert fmt == TextFormat.MARKDOWN

    def test_extract_text_trafilatura_fails(self):
        """Falls back to readability when trafilatura fails."""
        html = "<html><body><p>Test content</p></body></html>"
        fetcher = HTTPURLFetcher()

        with patch("omni_fetcher.fetchers.http_url.trafilatura") as mock_trafilatura:
            mock_trafilatura.extract = MagicMock(side_effect=Exception("Failed"))

            text, fmt = fetcher._extract_text(html)
            assert text == "Test content"
            assert fmt == TextFormat.PLAIN

    def test_extract_text_trafilatura_returns_none(self):
        """Falls back when trafilatura returns None."""
        html = "<html><body><p>Test content</p></body></html>"
        fetcher = HTTPURLFetcher()

        with patch("omni_fetcher.fetchers.http_url.trafilatura") as mock_trafilatura:
            mock_trafilatura.extract = MagicMock(return_value=None)

            text, fmt = fetcher._extract_text(html)
            assert text == "Test content"
            assert fmt == TextFormat.PLAIN

    def test_fallback_extract_text(self):
        """Fallback extraction uses BeautifulSoup."""
        html = "<html><head><style>/* hide */</style></head><body><script>// hide</script><p>Visible text</p></body></html>"
        fetcher = HTTPURLFetcher()
        text, fmt = fetcher._fallback_extract_text(html)
        assert "Visible text" in text
        assert "hide" not in text.lower()


class TestHTTPURLFetcherFetch:
    """Integration tests for fetch method."""

    @pytest.mark.asyncio
    async def test_fetch_html_page(self):
        """Fetches HTML page and returns WebPageDocument."""
        fetcher = HTTPURLFetcher()

        mock_response = MagicMock()
        mock_response.url = "https://example.com/page"
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.content = b"<html><body><p>Test</p></body></html>"
        mock_response.elapsed.total_seconds = MagicMock(return_value=0.5)
        mock_response.text = "<html><body><p>Test</p></body></html>"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await fetcher.fetch("https://example.com/page")

        assert isinstance(result, WebPageDocument)
        assert result.url == "https://example.com/page"

    @pytest.mark.asyncio
    async def test_fetch_json_response(self):
        """Returns JSONData for application/json content-type."""
        fetcher = HTTPURLFetcher()

        mock_response = MagicMock()
        mock_response.url = "https://example.com/api/data"
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.content = b'{"key": "value"}'
        mock_response.elapsed.total_seconds = MagicMock(return_value=0.1)
        mock_response.json.return_value = {"key": "value"}

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await fetcher.fetch("https://example.com/api/data")

        assert isinstance(result, JSONData)
        assert result.data == {"key": "value"}

    @pytest.mark.asyncio
    async def test_fetch_image_response(self):
        """Returns ImageDocument for image/* content-type."""
        fetcher = HTTPURLFetcher()

        mock_response = MagicMock()
        mock_response.url = "https://example.com/image.png"
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "image/png"}
        mock_response.content = b"fake image data"
        mock_response.elapsed.total_seconds = MagicMock(return_value=0.1)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await fetcher.fetch("https://example.com/image.png")

        assert isinstance(result, ImageDocument)
        assert result.tags == ["web", "image"]

    @pytest.mark.asyncio
    async def test_fetch_text_response(self):
        """Returns TextDocument for text/* content-type."""
        fetcher = HTTPURLFetcher()

        mock_response = MagicMock()
        mock_response.url = "https://example.com/file.txt"
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.content = b"Plain text content"
        mock_response.elapsed.total_seconds = MagicMock(return_value=0.1)
        mock_response.text = "Plain text content"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await fetcher.fetch("https://example.com/file.txt")

        assert isinstance(result, TextDocument)
        assert result.content == "Plain text content"

    @pytest.mark.asyncio
    async def test_fetch_markdown_format(self):
        """Returns TextDocument with MARKUP format for text/markdown."""
        fetcher = HTTPURLFetcher()

        mock_response = MagicMock()
        mock_response.url = "https://example.com/readme.md"
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/markdown"}
        mock_response.content = b"# Markdown"
        mock_response.elapsed.total_seconds = MagicMock(return_value=0.1)
        mock_response.text = "# Markdown"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await fetcher.fetch("https://example.com/readme.md")

        assert isinstance(result, TextDocument)
        assert result.format == TextFormat.MARKDOWN
