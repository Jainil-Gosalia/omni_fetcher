"""Tests for JiraFetcher."""

import pytest
from unittest.mock import patch

from omni_fetcher.fetchers.jira import (
    JiraFetcher,
    parse_jira_uri,
    convert_adf_to_markdown,
)


class TestJiraFetcherCreation:
    def test_creation(self):
        """Can create JiraFetcher."""
        fetcher = JiraFetcher()
        assert fetcher.name == "jira"
        assert fetcher.priority == 15

    def test_creation_with_timeout(self):
        """Can create JiraFetcher with custom timeout."""
        fetcher = JiraFetcher(timeout=60.0)
        assert fetcher.timeout == 60.0


class TestJiraFetcherCanHandle:
    def test_can_handle_jira_browse_urls(self):
        """JiraFetcher handles browse URLs."""
        assert JiraFetcher.can_handle("https://company.atlassian.net/browse/ENG-123")
        assert JiraFetcher.can_handle("https://mycompany.atlassian.net/browse/PROJ-456")

    def test_can_handle_jira_projects_urls(self):
        """JiraFetcher handles projects URLs."""
        assert JiraFetcher.can_handle("https://company.atlassian.net/jira/software/projects/ENG")
        assert JiraFetcher.can_handle("https://company.atlassian.net/jira/software/projects/PROJ")

    def test_can_handle_jira_protocol(self):
        """JiraFetcher handles jira:// URIs."""
        assert JiraFetcher.can_handle("jira://issue/ENG-123")
        assert JiraFetcher.can_handle("jira://project/ENG")
        assert JiraFetcher.can_handle("jira://sprint/42")
        assert JiraFetcher.can_handle("jira://epic/ENG-10")

    def test_cannot_handle_non_jira(self):
        """JiraFetcher rejects non-Jira URLs."""
        assert not JiraFetcher.can_handle("https://github.com/owner/repo")
        assert not JiraFetcher.can_handle("https://example.com/page")
        assert not JiraFetcher.can_handle("confluence://page-id")
        assert not JiraFetcher.can_handle("")


class TestParseJiraUri:
    def test_parse_issue_url(self):
        """Parse issue browse URL."""
        route = parse_jira_uri("https://company.atlassian.net/browse/ENG-123")
        assert route.type == "issue"
        assert route.issue_key == "ENG-123"

    def test_parse_project_url(self):
        """Parse project URL."""
        route = parse_jira_uri("https://company.atlassian.net/jira/software/projects/ENG")
        assert route.type == "project"
        assert route.project_key == "ENG"

    def test_parse_jira_issue_uri(self):
        """Parse jira://issue URI."""
        route = parse_jira_uri("jira://issue/ENG-123")
        assert route.type == "issue"
        assert route.issue_key == "ENG-123"

    def test_parse_jira_project_uri(self):
        """Parse jira://project URI."""
        route = parse_jira_uri("jira://project/ENG")
        assert route.type == "project"
        assert route.project_key == "ENG"

    def test_parse_jira_sprint_uri(self):
        """Parse jira://sprint URI."""
        route = parse_jira_uri("jira://sprint/42")
        assert route.type == "sprint"
        assert route.sprint_id == 42

    def test_parse_jira_epic_uri(self):
        """Parse jira://epic URI."""
        route = parse_jira_uri("jira://epic/ENG-10")
        assert route.type == "epic"
        assert route.epic_key == "ENG-10"

    def test_parse_invalid(self):
        """Invalid URIs raise ValueError."""
        with pytest.raises(ValueError):
            parse_jira_uri("https://example.com/page")

        with pytest.raises(ValueError):
            parse_jira_uri("jira://unknown/123")


class TestConvertAdfToMarkdown:
    def test_paragraph(self):
        """Convert paragraph node."""
        adf = {
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}]
        }
        result = convert_adf_to_markdown(adf)
        assert "Hello world" in result

    def test_heading(self):
        """Convert heading nodes."""
        adf = {
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [{"type": "text", "text": "Title"}],
                },
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "Subtitle"}],
                },
            ]
        }
        result = convert_adf_to_markdown(adf)
        assert "# Title" in result
        assert "## Subtitle" in result

    def test_bullet_list(self):
        """Convert bullet list."""
        adf = {
            "content": [
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Item 1"}],
                                }
                            ],
                        },
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Item 2"}],
                                }
                            ],
                        },
                    ],
                }
            ]
        }
        result = convert_adf_to_markdown(adf)
        assert "- Item 1" in result
        assert "- Item 2" in result

    def test_ordered_list(self):
        """Convert ordered list."""
        adf = {
            "content": [
                {
                    "type": "orderedList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "First"}],
                                }
                            ],
                        },
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Second"}],
                                }
                            ],
                        },
                    ],
                }
            ]
        }
        result = convert_adf_to_markdown(adf)
        assert "1. First" in result
        assert "2. Second" in result

    def test_code_block(self):
        """Convert code block."""
        adf = {
            "content": [
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": "print('hello')"}],
                }
            ]
        }
        result = convert_adf_to_markdown(adf)
        assert "```python" in result
        assert "print('hello')" in result

    def test_blockquote(self):
        """Convert blockquote."""
        adf = {
            "content": [
                {
                    "type": "blockquote",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Quote text"}]}
                    ],
                }
            ]
        }
        result = convert_adf_to_markdown(adf)
        assert "> Quote text" in result

    def test_table(self):
        """Convert table."""
        adf = {
            "content": [
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "content": [{"type": "text", "text": "Header 1"}],
                                },
                                {
                                    "type": "tableCell",
                                    "content": [{"type": "text", "text": "Header 2"}],
                                },
                            ],
                        },
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "content": [{"type": "text", "text": "Cell 1"}],
                                },
                                {
                                    "type": "tableCell",
                                    "content": [{"type": "text", "text": "Cell 2"}],
                                },
                            ],
                        },
                    ],
                }
            ]
        }
        result = convert_adf_to_markdown(adf)
        assert "Header 1" in result
        assert "Header 2" in result
        assert "Cell 1" in result

    def test_inline_card(self):
        """Convert inline card (Jira link)."""
        adf = {
            "content": [
                {
                    "type": "inlineCard",
                    "attrs": {"url": "https://company.atlassian.net/browse/ENG-123"},
                }
            ]
        }
        result = convert_adf_to_markdown(adf)
        assert "[ENG-123]" in result
        assert "https://company.atlassian.net/browse/ENG-123" in result

    def test_mention(self):
        """Convert mention."""
        adf = {"content": [{"type": "mention", "attrs": {"displayName": "Jane Doe"}}]}
        result = convert_adf_to_markdown(adf)
        assert "@Jane Doe" in result

    def test_emoji(self):
        """Convert emoji."""
        adf = {"content": [{"type": "emoji", "attrs": {"shortName": "rocket"}}]}
        result = convert_adf_to_markdown(adf)
        assert ":rocket:" in result

    def test_empty_content(self):
        """Handle empty content."""
        assert convert_adf_to_markdown({}) == ""
        assert convert_adf_to_markdown(None) == ""
        assert convert_adf_to_markdown("") == ""

    def test_string_content(self):
        """Handle string content (plain text)."""
        assert convert_adf_to_markdown("plain text") == "plain text"


class TestJiraFetcherIntegration:
    @pytest.mark.asyncio
    async def test_fetch_issue_without_auth(self):
        """Test fetching issue without auth raises error."""
        fetcher = JiraFetcher()
        with patch("omni_fetcher.fetchers.jira.ATLASSIAN_AVAILABLE", True):
            with patch.object(fetcher, "get_auth_headers", return_value={}):
                with pytest.raises(Exception):
                    await fetcher.fetch("https://company.atlassian.net/browse/ENG-123")

    @pytest.mark.asyncio
    async def test_fetch_project_without_auth(self):
        """Test fetching project without auth raises error."""
        fetcher = JiraFetcher()
        with patch("omni_fetcher.fetchers.jira.ATLASSIAN_AVAILABLE", True):
            with patch.object(fetcher, "get_auth_headers", return_value={}):
                with pytest.raises(Exception):
                    await fetcher.fetch("jira://project/ENG")

    @pytest.mark.asyncio
    async def test_fetch_sprint_without_auth(self):
        """Test fetching sprint without auth raises error."""
        fetcher = JiraFetcher()
        with patch("omni_fetcher.fetchers.jira.ATLASSIAN_AVAILABLE", True):
            with patch.object(fetcher, "get_auth_headers", return_value={}):
                with pytest.raises(Exception):
                    await fetcher.fetch("jira://sprint/42")

    @pytest.mark.asyncio
    async def test_fetch_epic_without_auth(self):
        """Test fetching epic without auth raises error."""
        fetcher = JiraFetcher()
        with patch("omni_fetcher.fetchers.jira.ATLASSIAN_AVAILABLE", True):
            with patch.object(fetcher, "get_auth_headers", return_value={}):
                with pytest.raises(Exception):
                    await fetcher.fetch("jira://epic/ENG-10")
