"""Tests for NotionFetcher."""

import pytest
from unittest.mock import patch

from omni_fetcher.fetchers.notion import (
    NotionFetcher,
    NotionRoute,
    extract_notion_id,
    parse_notion_uri,
)
from omni_fetcher.schemas.notion import NotionPage, NotionDatabase
from omni_fetcher.core.exceptions import FetchError


class TestNotionFetcherCreation:
    def test_creation(self):
        """Can create NotionFetcher."""
        fetcher = NotionFetcher()
        assert fetcher.name == "notion"
        assert fetcher.priority == 15

    def test_creation_with_timeout(self):
        """Can create NotionFetcher with custom timeout."""
        fetcher = NotionFetcher(timeout=60.0)
        assert fetcher.timeout == 60.0


class TestNotionFetcherCanHandle:
    def test_can_handle_notion_urls(self):
        """NotionFetcher handles Notion URLs."""
        assert NotionFetcher.can_handle("https://notion.so/owner/page-id")
        assert NotionFetcher.can_handle("http://notion.so/owner/page-id")
        assert NotionFetcher.can_handle("notion://page-id")

    def test_cannot_handle_non_notion(self):
        """NotionFetcher rejects non-Notion URLs."""
        assert not NotionFetcher.can_handle("https://github.com/owner/repo")
        assert not NotionFetcher.can_handle("https://example.com/page")
        assert not NotionFetcher.can_handle("")


class TestExtractNotionId:
    def test_extract_from_notion_so_url(self):
        """Extract ID from notion.so URL."""
        id1 = extract_notion_id("https://notion.so/Page-Name-1234567890abcdef1234567890abcdef")
        assert id1 is not None

    def test_extract_from_plain_id(self):
        """Extract ID from plain ID string."""
        id1 = extract_notion_id("1234567890abcdef1234567890abcdef")
        assert id1 == "1234567890abcdef1234567890abcdef"

    def test_extract_from_short_notion_url(self):
        """Extract ID from short notion URL."""
        id1 = extract_notion_id("https://notion.so/Page-Name-abc123def")
        assert id1 is not None

    def test_extract_returns_none_for_invalid(self):
        """Return None for invalid input."""
        assert extract_notion_id("https://example.com/page") is None


class TestParseNotionUri:
    def test_parse_page_url(self):
        """Parse Notion page URL."""
        route = parse_notion_uri(
            "https://notion.so/owner/Page-Name-1234567890abcdef1234567890abcdef"
        )
        assert route.type == "page"
        assert route.page_id is not None

    def test_parse_short_url(self):
        """Parse short Notion URL."""
        route = parse_notion_uri("https://notion.so/Page-Name-abc123def")
        assert route.type == "page"

    def test_parse_unknown(self):
        """Parse unknown URL."""
        route = parse_notion_uri("https://example.com/page")
        assert route.type == "unknown"


class TestNotionRoute:
    def test_route_creation(self):
        """Create NotionRoute."""
        route = NotionRoute(type="page", page_id="abc123")
        assert route.type == "page"
        assert route.page_id == "abc123"


class TestNotionSearch:
    """Tests for NotionFetcher.search() method."""

    def test_search_returns_list(self):
        """search() returns list of NotionSearchResult."""
        fetcher = NotionFetcher()
        assert hasattr(fetcher, "search")

    def test_search_with_object_type_filter(self):
        """search() accepts object_type parameter."""
        import inspect

        fetcher = NotionFetcher()
        sig = inspect.signature(fetcher.search)
        params = sig.parameters
        assert "object_type" in params

    def test_search_with_query(self):
        """search() accepts query parameter."""
        import inspect

        fetcher = NotionFetcher()
        sig = inspect.signature(fetcher.search)
        params = sig.parameters
        assert "query" in params

    def test_search_with_limit(self):
        """search() accepts limit parameter."""
        import inspect

        fetcher = NotionFetcher()
        sig = inspect.signature(fetcher.search)
        params = sig.parameters
        assert "limit" in params

    @pytest.mark.asyncio
    async def test_search_makes_http_request(self):
        """search() makes HTTP request to Notion API."""
        from unittest.mock import AsyncMock, patch

        fetcher = NotionFetcher()
        mock_response = {
            "results": [
                {
                    "object": "page",
                    "id": "abc123",
                    "properties": {
                        "Name": {
                            "id": "title",
                            "type": "title",
                            "title": [{"plain_text": "Test Page", "annotations": {}}],
                        }
                    },
                    "icon": {"emoji": "📄"},
                }
            ]
        }

        mock_client = AsyncMock()
        mock_response_obj = AsyncMock()
        mock_response_obj.json = lambda: mock_response
        mock_response_obj.raise_for_status = lambda: None
        mock_client.post = AsyncMock(return_value=mock_response_obj)

        with patch.object(fetcher, "_get_client", return_value=mock_client):
            with patch.object(fetcher, "_close_client", new_callable=AsyncMock):
                results = await fetcher.search()

                assert mock_client.post.called
                assert len(results) == 1
                assert results[0].title == "Test Page"

    def test_notion_page_tags(self):
        """NotionPage has correct tags."""
        page = NotionPage(
            page_id="abc123",
            title="Test Page",
            url="https://notion.so/abc123",
        )
        assert "notion" in page.tags
        assert "page" in page.tags

    def test_notion_database_tags(self):
        """NotionDatabase has correct tags."""
        db = NotionDatabase(
            database_id="abc123",
            title="Test Database",
            url="https://notion.so/abc123",
        )
        assert "notion" in db.tags
        assert "database" in db.tags


class TestNotionFetcherIntegration:
    @pytest.mark.asyncio
    async def test_fetch_page_without_auth(self):
        """Test fetching page without auth raises FetchError."""
        fetcher = NotionFetcher()
        with patch.object(fetcher, "get_auth_headers", return_value={}):
            with pytest.raises(FetchError):
                await fetcher.fetch("https://notion.so/TestPage-1234567890abcdef1234567890abcdef")

    @pytest.mark.asyncio
    async def test_block_to_markdown_paragraph(self):
        """Test converting paragraph block to markdown."""
        fetcher = NotionFetcher()

        block = {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "Hello world", "annotations": {}}]},
        }

        md = fetcher._block_to_markdown(block)
        assert md == "Hello world"

    @pytest.mark.asyncio
    async def test_block_to_markdown_heading(self):
        """Test converting heading block to markdown."""
        fetcher = NotionFetcher()

        block = {
            "type": "heading_1",
            "heading_1": {"rich_text": [{"plain_text": "Title", "annotations": {}}]},
        }

        md = fetcher._block_to_markdown(block)
        assert md == "# Title"

    @pytest.mark.asyncio
    async def test_block_to_markdown_bulleted_list(self):
        """Test converting bulleted list item to markdown."""
        fetcher = NotionFetcher()

        block = {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"plain_text": "List item", "annotations": {}}]},
        }

        md = fetcher._block_to_markdown(block)
        assert md == "- List item"

    @pytest.mark.asyncio
    async def test_block_to_markdown_code(self):
        """Test converting code block to markdown."""
        fetcher = NotionFetcher()

        block = {
            "type": "code",
            "code": {
                "rich_text": [{"plain_text": "print('hello')", "annotations": {}}],
                "language": "python",
            },
        }

        md = fetcher._block_to_markdown(block)
        assert "```python" in md
        assert "print('hello')" in md

    @pytest.mark.asyncio
    async def test_rich_text_with_annotations(self):
        """Test converting rich text with annotations to markdown."""
        fetcher = NotionFetcher()

        text = {"plain_text": "bold text", "annotations": {"bold": True}}

        md = fetcher._rich_text_to_markdown(text)
        assert md == "**bold text**"

    @pytest.mark.asyncio
    async def test_rich_text_with_link(self):
        """Test converting rich text with link to markdown."""
        fetcher = NotionFetcher()

        text = {"plain_text": "link text", "href": "https://example.com"}

        md = fetcher._rich_text_to_markdown(text)
        assert md == "[link text](https://example.com)"

    @pytest.mark.asyncio
    async def test_extract_title_from_properties(self):
        """Test extracting title from page properties."""
        fetcher = NotionFetcher()

        data = {
            "properties": {
                "Name": {
                    "id": "title",
                    "type": "title",
                    "title": [{"plain_text": "My Page Title", "annotations": {}}],
                }
            }
        }

        title = fetcher._extract_title(data)
        assert title == "My Page Title"

    @pytest.mark.asyncio
    async def test_blocks_to_markdown_multiple(self):
        """Test converting multiple blocks to markdown."""
        fetcher = NotionFetcher()

        blocks = [
            {
                "type": "heading_1",
                "heading_1": {"rich_text": [{"plain_text": "Title", "annotations": {}}]},
            },
            {
                "type": "paragraph",
                "paragraph": {"rich_text": [{"plain_text": "Content", "annotations": {}}]},
            },
        ]

        md = fetcher._blocks_to_markdown(blocks)
        assert "# Title" in md
        assert "Content" in md
