"""Tests for ConfluenceFetcher."""

import pytest
from unittest.mock import patch

from omni_fetcher.fetchers.confluence import (
    ConfluenceFetcher,
    extract_confluence_id,
    extract_space_key,
    parse_confluence_uri,
)
from omni_fetcher.schemas.confluence import ConfluencePage, ConfluenceSpace, ConfluenceAttachment


class TestConfluenceFetcherCreation:
    def test_creation(self):
        """Can create ConfluenceFetcher."""
        fetcher = ConfluenceFetcher()
        assert fetcher.name == "confluence"
        assert fetcher.priority == 15

    def test_creation_with_timeout(self):
        """Can create ConfluenceFetcher with custom timeout."""
        fetcher = ConfluenceFetcher(timeout=60.0)
        assert fetcher.timeout == 60.0


class TestConfluenceFetcherCanHandle:
    def test_can_handle_confluence_urls(self):
        """ConfluenceFetcher handles Confluence URLs."""
        assert ConfluenceFetcher.can_handle(
            "https://company.atlassian.net/wiki/spaces/SPACE/pages/123456"
        )
        assert ConfluenceFetcher.can_handle(
            "https://confluence.company.com/pages/viewpage.action?pageId=123456"
        )
        assert ConfluenceFetcher.can_handle("confluence://page-id")

    def test_cannot_handle_non_confluence(self):
        """ConfluenceFetcher rejects non-Confluence URLs."""
        assert not ConfluenceFetcher.can_handle("https://github.com/owner/repo")
        assert not ConfluenceFetcher.can_handle("https://example.com/page")
        assert not ConfluenceFetcher.can_handle("")


class TestExtractConfluenceId:
    def test_extract_from_page_url(self):
        """Extract ID from Confluence page URL."""
        id1 = extract_confluence_id("https://company.atlassian.net/wiki/spaces/SPACE/pages/123456")
        assert id1 == "SPACE"

    def test_extract_from_viewpage_url(self):
        """Extract ID from viewpage.action URL."""
        id1 = extract_confluence_id(
            "https://confluence.company.com/pages/viewpage.action?pageId=123456"
        )
        assert id1 == "123456"

    def test_extract_returns_none_for_invalid(self):
        """Return None for invalid input."""
        assert extract_confluence_id("https://example.com/page") is None


class TestExtractSpaceKey:
    def test_extract_space_key_from_url(self):
        """Extract space key from Confluence URL."""
        key = extract_space_key("https://company.atlassian.net/wiki/spaces/MYSPACE/pages/123456")
        assert key == "MYSPACE"

    def test_extract_space_key_from_display(self):
        """Extract space key from display URL."""
        key = extract_space_key("https://company.atlassian.net/wiki/display/MYSPACE")
        assert key == "MYSPACE"

    def test_extract_returns_none_for_invalid(self):
        """Return None for invalid input."""
        assert extract_space_key("https://example.com/page") is None


class TestParseConfluenceUri:
    def test_parse_page_url(self):
        """Parse Confluence page URL."""
        route = parse_confluence_uri("https://company.atlassian.net/wiki/spaces/SPACE/pages/123456")
        assert route["type"] == "page"
        assert route["page_id"] == "123456"

    def test_parse_space_url(self):
        """Parse Confluence space URL."""
        route = parse_confluence_uri("https://company.atlassian.net/wiki/spaces/MYSPACE")
        assert route["type"] == "space"
        assert route["space_key"] == "MYSPACE"

    def test_parse_display_url(self):
        """Parse Confluence display URL."""
        route = parse_confluence_uri("https://company.atlassian.net/wiki/display/MYSPACE")
        assert route["type"] == "space"
        assert route["space_key"] == "MYSPACE"

    def test_parse_unknown(self):
        """Parse unknown URL."""
        route = parse_confluence_uri("https://example.com/page")
        assert route["type"] == "unknown"

    def test_parse_root_url_with_slash(self):
        """Parse root URL with trailing slash."""
        route = parse_confluence_uri("https://omnifetcher.atlassian.net/")
        assert route["type"] == "root"

    def test_parse_root_url_without_slash(self):
        """Parse root URL without trailing slash."""
        route = parse_confluence_uri("https://omnifetcher.atlassian.net")
        assert route["type"] == "root"

    def test_parse_root_url_with_wiki_path(self):
        """Parse root URL with /wiki path."""
        route = parse_confluence_uri("https://omnifetcher.atlassian.net/wiki/")
        assert route["type"] == "root"


class TestConfluenceSchemas:
    def test_confluence_page_tags(self):
        """ConfluencePage has correct tags."""
        page = ConfluencePage(
            page_id="123456",
            title="Test Page",
            space_key="TEST",
        )
        assert "confluence" in page.tags
        assert "page" in page.tags
        assert "space:test" in page.tags

    def test_confluence_space_tags(self):
        """ConfluenceSpace has correct tags."""
        space = ConfluenceSpace(
            space_key="TEST",
            name="Test Space",
        )
        assert "confluence" in space.tags
        assert "space" in space.tags

    def test_confluence_attachment_tags(self):
        """ConfluenceAttachment has correct tags."""
        att = ConfluenceAttachment(
            attachment_id="123",
            filename="test.pdf",
            media_type="application/pdf",
            size=1000,
        )
        assert "confluence" in att.tags
        assert "attachment" in att.tags
        assert "pdf" in att.tags

    def test_confluence_attachment_image_tags(self):
        """ConfluenceAttachment has correct image tags."""
        att = ConfluenceAttachment(
            attachment_id="123",
            filename="test.png",
            media_type="image/png",
            size=1000,
        )
        assert "image" in att.tags


class TestConfluenceFetcherIntegration:
    @pytest.mark.asyncio
    async def test_fetch_page_without_auth(self):
        """Test fetching page without auth raises error."""
        fetcher = ConfluenceFetcher()
        with patch("omni_fetcher.fetchers.confluence.ATLASSIAN_AVAILABLE", True):
            with patch.object(fetcher, "get_auth_headers", return_value={}):
                with pytest.raises(Exception):
                    await fetcher.fetch(
                        "https://company.atlassian.net/wiki/spaces/SPACE/pages/123456"
                    )

    @pytest.mark.asyncio
    async def test_html_to_markdown_heading(self):
        """Test converting HTML heading to markdown."""
        fetcher = ConfluenceFetcher()

        html = "<h1>Title</h1>"
        md = fetcher._html_to_markdown(html)
        assert "# Title" in md

    @pytest.mark.asyncio
    async def test_html_to_markdown_paragraph(self):
        """Test converting HTML paragraph to markdown."""
        fetcher = ConfluenceFetcher()

        html = "<p>Paragraph text</p>"
        md = fetcher._html_to_markdown(html)
        assert "Paragraph text" in md

    @pytest.mark.asyncio
    async def test_html_to_markdown_code(self):
        """Test converting HTML code block to markdown."""
        fetcher = ConfluenceFetcher()

        html = '<pre><code class="language-python">print("hello")</code></pre>'
        md = fetcher._html_to_markdown(html)
        assert "```python" in md
        assert 'print("hello")' in md

    @pytest.mark.asyncio
    async def test_html_to_markdown_list(self):
        """Test converting HTML list to markdown."""
        fetcher = ConfluenceFetcher()

        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
        md = fetcher._html_to_markdown(html)
        assert "- Item 1" in md
        assert "- Item 2" in md

    @pytest.mark.asyncio
    async def test_get_domain_from_client(self):
        """Test extracting domain from URI."""
        fetcher = ConfluenceFetcher()

        domain = fetcher._get_domain_from_client(
            "https://company.atlassian.net/wiki/spaces/SPACE/pages/123"
        )
        assert "atlassian.net" in domain

    @pytest.mark.asyncio
    async def test_space_key_extraction(self):
        """Test extracting space key for building URLs."""
        fetcher = ConfluenceFetcher()

        uri = "https://mycompany.atlassian.net/wiki/spaces/TEST/pages/123"
        domain = fetcher._get_domain_from_client(uri)
        assert "mycompany.atlassian.net" in domain
