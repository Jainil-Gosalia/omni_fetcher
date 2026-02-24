"""Bulk integration tests for public (non-auth) fetchers.

These tests fetch data from multiple real public URLs to verify that each fetcher
works correctly with various data sources.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from omni_fetcher.fetchers.local_file import LocalFileFetcher
from omni_fetcher.fetchers.http_url import HTTPURLFetcher
from omni_fetcher.fetchers.http_json import HTTPJSONFetcher
from omni_fetcher.fetchers.rss import RSSFetcher
from omni_fetcher.fetchers.csv import CSVFetcher
from omni_fetcher.fetchers.pdf import PDFFetcher
from omni_fetcher.fetchers.youtube import YouTubeFetcher
from omni_fetcher.fetchers.graphql import GraphQLFetcher

from omni_fetcher.schemas.documents import WebPageDocument, PDFDocument
from omni_fetcher.schemas.structured import JSONData, GraphQLResponse
from omni_fetcher.schemas.atomics import SpreadsheetDocument
from omni_fetcher.schemas.media import YouTubeVideo


HTTP_URL_BULK_TESTS = [
    ("https://example.com", WebPageDocument),
    ("https://httpbin.org/html", WebPageDocument),
    ("https://www.python.org", WebPageDocument),
    ("https://www.wikipedia.org", WebPageDocument),
    ("https://news.ycombinator.com", WebPageDocument),
]


HTTP_JSON_BULK_TESTS = [
    ("https://jsonplaceholder.typicode.com/posts/1", JSONData),
    ("https://jsonplaceholder.typicode.com/users/1", JSONData),
    ("https://jsonplaceholder.typicode.com/todos/1", JSONData),
    ("https://jsonplaceholder.typicode.com/albums/1", JSONData),
    ("https://jsonplaceholder.typicode.com/photos/1", JSONData),
    ("https://jsonplaceholder.typicode.com/comments/1", JSONData),
    ("https://jsonplaceholder.typicode.com/posts", JSONData),
    ("https://httpbin.org/json", JSONData),
    ("https://httpbin.org/get", JSONData),
    ("https://dog.ceo/api/breeds/image/random", JSONData),
]


RSS_BULK_TESTS = [
    ("https://hnrss.org/frontpage", JSONData),
    ("https://feeds.bbci.co.uk/news/rss.xml", JSONData),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", JSONData),
    ("https://www.reddit.com/r/python/.rss", JSONData),
]


CSV_BULK_TESTS = [
    ("https://people.sc.fsu.edu/~jburkardt/data/csv/addresses.csv", SpreadsheetDocument),
    ("https://people.sc.fsu.edu/~jburkardt/data/csv/hooke.csv", SpreadsheetDocument),
    ("https://raw.githubusercontent.com/mwaskom/seaborn-data/master/iris.csv", SpreadsheetDocument),
]


PDF_BULK_TESTS = [
    ("https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf", PDFDocument),
    ("https://pdfobject.com/pdf/sample.pdf", PDFDocument),
    ("https://unec.edu.az/application/uploads/2014/12/pdf-sample.pdf", PDFDocument),
]


YOUTUBE_BULK_TESTS = [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", YouTubeVideo),
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", YouTubeVideo),
    ("https://www.youtube.com/watch?v=9bZkp7q19f0", YouTubeVideo),
    ("https://www.youtube.com/watch?v=kJQP7kiw5Fk", YouTubeVideo),
    ("https://www.youtube.com/watch?v=3JZ_D3ELwOQ", YouTubeVideo),
]


def skip_if_ssl_not_working():
    """Skip test if SSL is not working in this environment."""
    try:
        import httpx
        import asyncio

        asyncio.get_event_loop().run_until_complete(httpx.AsyncClient().get("https://example.com"))
        return False
    except Exception:
        return True


GRAPHQL_BULK_TESTS = [
    (
        "https://countries.trevorblades.com/graphql",
        """
        {
            countries {
                code
                name
            }
        }
        """,
        ["data", "countries"],
        None,
    ),
    (
        "https://countries.trevorblades.com/graphql",
        """
        query GetCountry($code: ID!) {
            country(code: $code) {
                code
                name
                capital
            }
        }
        """,
        ["data", "country"],
        {"code": "US"},
    ),
    (
        "https://countries.trevorblades.com/graphql",
        """
        {
            continents {
                code
                name
            }
        }
        """,
        ["data", "continents"],
        None,
    ),
    (
        "https://countries.trevorblades.com/graphql",
        """
        {
            languages {
                code
                name
            }
        }
        """,
        ["data", "languages"],
        None,
    ),
    (
        "https://countries.trevorblades.com/graphql",
        """
        query {
            country(code: "JP") {
                name
                native
                emoji
            }
        }
        """,
        ["data", "country"],
        None,
    ),
]


class TestHTTPURLBulk:
    """Bulk tests for HTTP URL fetcher."""

    @pytest.mark.skipif(skip_if_ssl_not_working(), reason="SSL not working in this environment")
    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", HTTP_URL_BULK_TESTS)
    async def test_fetch_html_pages(self, url: str, expected_schema: type):
        """Can fetch various HTML pages."""
        fetcher = HTTPURLFetcher()
        result = await fetcher.fetch(url)

        assert result is not None, f"Failed to fetch {url}"
        assert isinstance(result, expected_schema), (
            f"Expected {expected_schema.__name__}, got {type(result).__name__}"
        )
        assert hasattr(result, "url"), "Result should have url attribute"


class TestHTTPJSONBulk:
    """Bulk tests for HTTP JSON fetcher."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", HTTP_JSON_BULK_TESTS)
    async def test_fetch_json_apis(self, url: str, expected_schema: type):
        """Can fetch various JSON APIs."""
        fetcher = HTTPJSONFetcher()
        result = await fetcher.fetch(url)

        assert result is not None, f"Failed to fetch {url}"
        assert isinstance(result, expected_schema), (
            f"Expected {expected_schema.__name__}, got {type(result).__name__}"
        )
        assert result.data is not None, "JSON data should not be None"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", HTTP_JSON_BULK_TESTS)
    async def test_json_has_valid_structure(self, url: str, expected_schema: type):
        """JSON response has valid data structure."""
        fetcher = HTTPJSONFetcher()
        result = await fetcher.fetch(url)

        assert isinstance(result.data, (dict, list)), "JSON data should be dict or list"


class TestRSSBulk:
    """Bulk tests for RSS fetcher."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", RSS_BULK_TESTS)
    async def test_fetch_rss_feeds(self, url: str, expected_schema: type):
        """Can fetch various RSS/Atom feeds."""
        fetcher = RSSFetcher()
        result = await fetcher.fetch(url)

        assert result is not None, f"Failed to fetch {url}"
        assert isinstance(result, expected_schema), (
            f"Expected {expected_schema.__name__}, got {type(result).__name__}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", RSS_BULK_TESTS)
    async def test_rss_has_entries(self, url: str, expected_schema: type):
        """RSS feed has entries."""
        fetcher = RSSFetcher()
        result = await fetcher.fetch(url)

        data = result.data
        has_entries = "entries" in data or "items" in data or "item" in data
        assert has_entries, "RSS feed should have entries/items"


class TestCSVBulk:
    """Bulk tests for CSV fetcher."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", CSV_BULK_TESTS)
    async def test_fetch_csv_files(self, url: str, expected_schema: type):
        """Can fetch various CSV files."""
        fetcher = CSVFetcher()
        result = await fetcher.fetch(url)

        assert result is not None, f"Failed to fetch {url}"
        assert isinstance(result, expected_schema), (
            f"Expected {expected_schema.__name__}, got {type(result).__name__}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", CSV_BULK_TESTS)
    async def test_csv_has_headers(self, url: str, expected_schema: type):
        """CSV file has headers."""
        fetcher = CSVFetcher()
        result = await fetcher.fetch(url)

        assert result.sheets is not None and len(result.sheets) > 0
        sheet = result.sheets[0]
        assert sheet.headers is not None and len(sheet.headers) > 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", CSV_BULK_TESTS)
    async def test_csv_has_rows(self, url: str, expected_schema: type):
        """CSV file has data rows."""
        fetcher = CSVFetcher()
        result = await fetcher.fetch(url)

        sheet = result.sheets[0]
        assert sheet.rows is not None and len(sheet.rows) > 0


class TestPDFBulk:
    """Bulk tests for PDF fetcher."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", PDF_BULK_TESTS)
    async def test_fetch_pdf_files(self, url: str, expected_schema: type):
        """Can fetch various PDF files."""
        fetcher = PDFFetcher()
        result = await fetcher.fetch(url)

        assert result is not None, f"Failed to fetch {url}"
        assert isinstance(result, expected_schema), (
            f"Expected {expected_schema.__name__}, got {type(result).__name__}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", PDF_BULK_TESTS)
    async def test_pdf_has_page_count(self, url: str, expected_schema: type):
        """PDF has page count."""
        fetcher = PDFFetcher()
        result = await fetcher.fetch(url)

        assert result.page_count is not None
        assert result.page_count > 0


class TestYouTubeBulk:
    """Bulk tests for YouTube fetcher."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", YOUTUBE_BULK_TESTS)
    async def test_fetch_youtube_videos(self, url: str, expected_schema: type):
        """Can fetch various YouTube videos."""
        fetcher = YouTubeFetcher()
        result = await fetcher.fetch(url)

        assert result is not None, f"Failed to fetch {url}"
        assert isinstance(result, expected_schema), (
            f"Expected {expected_schema.__name__}, got {type(result).__name__}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", YOUTUBE_BULK_TESTS)
    async def test_youtube_has_video_id(self, url: str, expected_schema: type):
        """YouTube video has video ID."""
        fetcher = YouTubeFetcher()
        result = await fetcher.fetch(url)

        assert result.video_id is not None
        assert len(result.video_id) > 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url,expected_schema", YOUTUBE_BULK_TESTS)
    async def test_youtube_has_title(self, url: str, expected_schema: type):
        """YouTube video has title."""
        fetcher = YouTubeFetcher()
        result = await fetcher.fetch(url)

        assert result.title is not None
        assert len(result.title) > 0


class TestGraphQLBulk:
    """Bulk tests for GraphQL fetcher."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint,query,response_path,variables",
        [
            (
                "https://countries.trevorblades.com/graphql",
                """
                {
                    countries {
                        code
                        name
                    }
                }
                """,
                ["data", "countries"],
                None,
            ),
            (
                "https://countries.trevorblades.com/graphql",
                """
                query GetCountry($code: ID!) {
                    country(code: $code) {
                        code
                        name
                        capital
                    }
                }
                """,
                ["data", "country"],
                {"code": "US"},
            ),
            (
                "https://countries.trevorblades.com/graphql",
                """
                {
                    continents {
                        code
                        name
                    }
                }
                """,
                ["data", "continents"],
                None,
            ),
            (
                "https://countries.trevorblades.com/graphql",
                """
                {
                    languages {
                        code
                        name
                    }
                }
                """,
                ["data", "languages"],
                None,
            ),
            (
                "https://countries.trevorblades.com/graphql",
                """
                query {
                    country(code: "JP") {
                        name
                        native
                        emoji
                    }
                }
                """,
                ["data", "country"],
                None,
            ),
        ],
    )
    async def test_graphql_queries(
        self,
        endpoint: str,
        query: str,
        response_path: list,
        variables: dict | None,
    ):
        """Can execute various GraphQL queries."""
        fetcher = GraphQLFetcher(endpoint=endpoint)

        if variables:
            result = await fetcher.query(query, variables=variables)
        else:
            result = await fetcher.query(query)

        assert result is not None
        assert isinstance(result, GraphQLResponse)
        assert result.data is not None
        assert isinstance(result.data, dict), "GraphQL data should be a dict"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint,query,response_path,variables",
        GRAPHQL_BULK_TESTS,
    )
    async def test_graphql_no_errors(
        self,
        endpoint: str,
        query: str,
        response_path: list,
        variables: dict | None,
    ):
        """GraphQL response has no errors."""
        fetcher = GraphQLFetcher(endpoint=endpoint)

        if variables:
            result = await fetcher.query(query, variables=variables)
        else:
            result = await fetcher.query(query)

        assert result.errors is None or len(result.errors) == 0, f"GraphQL errors: {result.errors}"


class TestLocalFileBulk:
    """Bulk tests for local file fetcher."""

    @pytest.mark.asyncio
    async def test_fetch_multiple_text_files(self):
        """Can fetch multiple text files."""
        files_content = [
            ("test1.txt", "Hello World"),
            ("test2.txt", "Line 1\nLine 2\nLine 3"),
            ("test3.txt", "Unicode: \u00e9\u00e8\u00ea"),
            ("test4.txt", "Special chars: <>&'\""),
            ("test5.txt", ""),
        ]

        for filename, content in files_content:
            with tempfile.NamedTemporaryFile(mode="w", suffix=f"_{filename}", delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                fetcher = LocalFileFetcher()
                result = await fetcher.fetch(temp_path)

                assert result is not None
                assert content in result.content or result.content == content
            finally:
                os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_fetch_multiple_json_files(self):
        """Can fetch multiple JSON files."""
        json_content = [
            ('{"key": "value"}', "value"),
            ('{"nested": {"key": "value"}}', "value"),
            ('{"array": [1, 2, 3]}', [1, 2, 3]),
            ('{"number": 42}', 42),
            ('{"boolean": true}', True),
        ]

        for i, (content, expected_value) in enumerate(json_content):
            with tempfile.NamedTemporaryFile(mode="w", suffix=f"_{i}.json", delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                fetcher = LocalFileFetcher()
                result = await fetcher.fetch(temp_path)

                assert result is not None
                assert result.data is not None
            finally:
                os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_fetch_multiple_markdown_files(self):
        """Can fetch multiple markdown files."""
        md_content = [
            "# Heading 1\n\nContent",
            "## Heading 2\n\n**Bold** and *italic*",
            "- Item 1\n- Item 2\n- Item 3",
            "```python\nprint('code')\n```",
            "[Link](https://example.com)",
        ]

        for i, content in enumerate(md_content):
            with tempfile.NamedTemporaryFile(mode="w", suffix=f"_{i}.md", delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                fetcher = LocalFileFetcher()
                result = await fetcher.fetch(temp_path)

                assert result is not None
                assert content in result.content or result.content.startswith("#")
            finally:
                os.unlink(temp_path)


class TestBulkSummary:
    """Summary test to verify bulk test counts."""

    def test_bulk_test_counts(self):
        """Verify we have bulk tests for each fetcher."""
        assert len(HTTP_URL_BULK_TESTS) >= 5, "Need at least 5 HTTP URL tests"
        assert len(HTTP_JSON_BULK_TESTS) >= 5, "Need at least 5 HTTP JSON tests"
        assert len(RSS_BULK_TESTS) >= 4, "Need at least 4 RSS tests"
        assert len(CSV_BULK_TESTS) >= 3, "Need at least 3 CSV tests"
        assert len(PDF_BULK_TESTS) >= 2, "Need at least 2 PDF tests"
        assert len(YOUTUBE_BULK_TESTS) >= 5, "Need at least 5 YouTube tests"
        assert len(GRAPHQL_BULK_TESTS) >= 5, "Need at least 5 GraphQL tests"
