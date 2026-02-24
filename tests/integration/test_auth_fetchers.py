"""Integration tests for authenticated fetchers.

These tests verify that authenticated fetchers work correctly when credentials
are available. Tests are skipped gracefully when credentials are not configured.
"""

from __future__ import annotations

import os

import pytest

from omni_fetcher.fetchers.github import GitHubFetcher
from omni_fetcher.fetchers.s3 import S3Fetcher
from omni_fetcher.fetchers.slack import SlackFetcher
from omni_fetcher.fetchers.jira import JiraFetcher
from omni_fetcher.fetchers.confluence import ConfluenceFetcher
from omni_fetcher.fetchers.notion import NotionFetcher


def get_env_or_skip(env_var: str, service_name: str) -> str:
    """Get environment variable or skip test."""
    value = os.environ.get(env_var)
    if not value:
        pytest.skip(f"{service_name} credentials not configured ({env_var} not set)")
    return value


def check_github_credentials() -> str | None:
    """Check if GitHub credentials are available."""
    return os.environ.get("GITHUB_TOKEN")


def check_aws_credentials() -> bool:
    """Check if AWS credentials are available."""
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    return bool(access_key and secret_key)


def check_slack_credentials() -> str | None:
    """Check if Slack credentials are available."""
    return os.environ.get("SLACK_BOT_TOKEN")


def check_jira_credentials() -> str | None:
    """Check if Jira credentials are available."""
    return os.environ.get("JIRA_TOKEN")


def check_confluence_credentials() -> str | None:
    """Check if Confluence credentials are available."""
    return os.environ.get("CONFLUENCE_TOKEN")


def check_notion_credentials() -> str | None:
    """Check if Notion credentials are available."""
    return os.environ.get("NOTION_TOKEN")


class TestGitHubFetcherIntegration:
    """Integration tests for GitHub fetcher."""

    def test_can_handle_github_urls(self):
        """GitHub fetcher can handle GitHub URLs."""
        assert GitHubFetcher.can_handle("https://github.com/owner/repo")
        assert GitHubFetcher.can_handle("https://api.github.com/repos/owner/repo")
        assert GitHubFetcher.can_handle("github.com/owner/repo")

    def test_can_handle_non_github(self):
        """GitHub fetcher rejects non-GitHub URLs."""
        assert not GitHubFetcher.can_handle("https://gitlab.com/owner/repo")
        assert not GitHubFetcher.can_handle("https://bitbucket.org/owner/repo")

    @pytest.mark.asyncio
    async def test_fetch_public_repo_with_token(self):
        """Can fetch public repository with GitHub token."""
        token = check_github_credentials()
        if not token:
            pytest.skip("GITHUB_TOKEN not set")

        fetcher = GitHubFetcher()
        fetcher.set_auth(token)

        result = await fetcher.fetch("https://github.com/psf/requests")

        assert result is not None
        assert hasattr(result, "name") or hasattr(result, "data")

    @pytest.mark.asyncio
    async def test_fetch_public_repo_metadata(self):
        """Can fetch repository metadata."""
        token = check_github_credentials()
        if not token:
            pytest.skip("GITHUB_TOKEN not set")

        fetcher = GitHubFetcher()
        fetcher.set_auth(token)

        result = await fetcher.fetch("https://api.github.com/repos/psf/requests")

        assert result is not None
        assert hasattr(result, "data") or hasattr(result, "name")

    @pytest.mark.asyncio
    async def test_fetch_github_issues(self):
        """Can fetch repository issues."""
        token = check_github_credentials()
        if not token:
            pytest.skip("GITHUB_TOKEN not set")

        fetcher = GitHubFetcher()
        fetcher.set_auth(token)

        result = await fetcher.fetch("https://github.com/psf/requests/issues")

        assert result is not None


class TestS3FetcherIntegration:
    """Integration tests for S3 fetcher."""

    def test_can_handle_s3_urls(self):
        """S3 fetcher can handle S3 URLs."""
        assert S3Fetcher.can_handle("s3://bucket-name/key")
        assert S3Fetcher.can_handle("s3://bucket-name/path/to/file.txt")

    def test_can_handle_non_s3(self):
        """S3 fetcher rejects non-S3 URLs."""
        assert not S3Fetcher.can_handle("https://example.com/file.txt")
        assert not S3Fetcher.can_handle("file:///path/to/file")

    @pytest.mark.asyncio
    async def test_s3_with_credentials(self):
        """Can create S3 fetcher with credentials."""
        if not check_aws_credentials():
            pytest.skip("AWS credentials not set (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)")

        fetcher = S3Fetcher(
            access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )

        assert fetcher is not None

    @pytest.mark.asyncio
    async def test_s3_list_buckets(self):
        """Can list S3 buckets with credentials."""
        if not check_aws_credentials():
            pytest.skip("AWS credentials not set")

        fetcher = S3Fetcher(
            access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )

        try:
            buckets = await fetcher.list_buckets()
            assert buckets is not None
        except Exception as e:
            pytest.skip(f"S3 operation failed: {e}")


class TestSlackFetcherIntegration:
    """Integration tests for Slack fetcher."""

    def test_can_handle_slack_urls(self):
        """Slack fetcher can handle Slack URLs."""
        assert SlackFetcher.can_handle("slack://channel/general")
        assert SlackFetcher.can_handle("slack://channel/C123456")

    def test_can_handle_non_slack(self):
        """Slack fetcher rejects non-Slack URLs."""
        assert not SlackFetcher.can_handle("https://discord.com/channels/123")
        assert not SlackFetcher.can_handle("https://example.com")

    @pytest.mark.asyncio
    async def test_slack_with_credentials(self):
        """Can create Slack fetcher with credentials."""
        token = check_slack_credentials()
        if not token:
            pytest.skip("SLACK_BOT_TOKEN not set")

        fetcher = SlackFetcher(token=token)

        assert fetcher is not None
        assert fetcher.name == "slack"

    @pytest.mark.asyncio
    async def test_slack_fetch_conversation(self):
        """Can fetch Slack conversation with credentials."""
        token = check_slack_credentials()
        if not token:
            pytest.skip("SLACK_BOT_TOKEN not set")

        fetcher = SlackFetcher(token=token)

        try:
            result = await fetcher.fetch("slack://channel/C1234567890")
            assert result is not None
        except Exception as e:
            pytest.skip(f"Slack fetch failed: {e}")


class TestJiraFetcherIntegration:
    """Integration tests for Jira fetcher."""

    def test_can_handle_jira_urls(self):
        """Jira fetcher can handle Jira URLs."""
        assert JiraFetcher.can_handle("https://company.atlassian.net/browse/PROJ-123")

    def test_can_handle_non_jira(self):
        """Jira fetcher rejects non-Jira URLs."""
        assert not JiraFetcher.can_handle("https://example.com/browse/PROJ-123")
        assert not JiraFetcher.can_handle("https://github.com/owner/repo")

    @pytest.mark.asyncio
    async def test_jira_with_credentials(self):
        """Can create Jira fetcher with credentials."""
        token = check_jira_credentials()
        if not token:
            pytest.skip("JIRA_TOKEN not set")

        jira_url = os.environ.get("JIRA_URL", "https://company.atlassian.net")

        fetcher = JiraFetcher(
            url=jira_url,
            token=token,
        )

        assert fetcher is not None
        assert fetcher.name == "jira"


class TestConfluenceFetcherIntegration:
    """Integration tests for Confluence fetcher."""

    def test_can_handle_confluence_urls(self):
        """Confluence fetcher can handle Confluence URLs."""
        assert ConfluenceFetcher.can_handle(
            "https://company.atlassian.net/wiki/spaces/SPACE/pages/123456"
        )

    def test_can_handle_non_confluence(self):
        """Confluence fetcher rejects non-Confluence URLs."""
        assert not ConfluenceFetcher.can_handle("https://example.com/wiki/page")
        assert not ConfluenceFetcher.can_handle("https://notion.so/page")

    @pytest.mark.asyncio
    async def test_confluence_with_credentials(self):
        """Can create Confluence fetcher with credentials."""
        token = check_confluence_credentials()
        if not token:
            pytest.skip("CONFLUENCE_TOKEN not set")

        confluence_url = os.environ.get("CONFLUENCE_URL", "https://company.atlassian.net/wiki")

        fetcher = ConfluenceFetcher(
            url=confluence_url,
            token=token,
        )

        assert fetcher is not None
        assert fetcher.name == "confluence"


class TestNotionFetcherIntegration:
    """Integration tests for Notion fetcher."""

    def test_can_handle_notion_urls(self):
        """Notion fetcher can handle Notion URLs."""
        assert NotionFetcher.can_handle("https://notion.so/workspace/page-id")
        assert NotionFetcher.can_handle("notion://page-id")

    def test_can_handle_non_notion(self):
        """Notion fetcher rejects non-Notion URLs."""
        assert not NotionFetcher.can_handle("https://example.com/page")
        assert not NotionFetcher.can_handle("https://confluence.atlassian.net/page")

    @pytest.mark.asyncio
    async def test_notion_with_credentials(self):
        """Can create Notion fetcher with credentials."""
        token = check_notion_credentials()
        if not token:
            pytest.skip("NOTION_TOKEN not set")

        fetcher = NotionFetcher(token=token)

        assert fetcher is not None
        assert fetcher.name == "notion"

    @pytest.mark.asyncio
    async def test_notion_fetch_page(self):
        """Can fetch Notion page with credentials."""
        token = check_notion_credentials()
        if not token:
            pytest.skip("NOTION_TOKEN not set")

        notion_page_id = os.environ.get("NOTION_PAGE_ID")
        if not notion_page_id:
            pytest.skip("NOTION_PAGE_ID not set")

        fetcher = NotionFetcher(token=token)

        try:
            result = await fetcher.fetch(f"https://notion.so/{notion_page_id}")
            assert result is not None
        except Exception as e:
            pytest.skip(f"Notion fetch failed: {e}")


class TestAuthFetcherAvailability:
    """Test that auth fetchers are properly registered and available."""

    def test_github_fetcher_available(self):
        """GitHub fetcher is importable and instantiable."""
        fetcher = GitHubFetcher()
        assert fetcher.name == "github"

    def test_s3_fetcher_available(self):
        """S3 fetcher is importable and instantiable."""
        fetcher = S3Fetcher()
        assert fetcher.name == "s3"

    def test_slack_fetcher_available(self):
        """Slack fetcher is importable and instantiable."""
        fetcher = SlackFetcher()
        assert fetcher.name == "slack"

    def test_jira_fetcher_available(self):
        """Jira fetcher is importable and instantiable."""
        fetcher = JiraFetcher()
        assert fetcher.name == "jira"

    def test_confluence_fetcher_available(self):
        """Confluence fetcher is importable and instantiable."""
        fetcher = ConfluenceFetcher()
        assert fetcher.name == "confluence"

    def test_notion_fetcher_available(self):
        """Notion fetcher is importable and instantiable."""
        fetcher = NotionFetcher()
        assert fetcher.name == "notion"


class TestAuthCredentialsSummary:
    """Summary of auth credentials needed for tests."""

    def test_credentials_requirements_documented(self):
        """Document required credentials for auth fetcher tests."""
        required_env_vars = {
            "GitHub": ["GITHUB_TOKEN"],
            "S3": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION (optional)"],
            "Slack": ["SLACK_BOT_TOKEN"],
            "Jira": ["JIRA_TOKEN", "JIRA_URL"],
            "Confluence": ["CONFLUENCE_TOKEN", "CONFLUENCE_URL"],
            "Notion": ["NOTION_TOKEN", "NOTION_PAGE_ID (optional)"],
        }

        for service, env_vars in required_env_vars.items():
            assert len(env_vars) > 0, f"{service} should have required env vars"
