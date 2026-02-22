"""Tests for GitHubFetcher."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime

from omni_fetcher.fetchers.github import GitHubFetcher, GitHubRoute
from omni_fetcher.schemas.github import (
    GitHubFile,
    GitHubIssue,
    GitHubPR,
    GitHubRelease,
    GitHubRepo,
    GitHubIssueContainer,
    GitHubReleaseContainer,
    GitHubPRContainer,
    detect_language,
)
from omni_fetcher.schemas.atomics import TextDocument, TextFormat


class TestGitHubFetcherCreation:
    def test_creation(self):
        """Can create GitHubFetcher."""
        fetcher = GitHubFetcher()
        assert fetcher.name == "github"
        assert fetcher.priority == 15

    def test_creation_with_timeout(self):
        """Can create GitHubFetcher with custom timeout."""
        fetcher = GitHubFetcher(timeout=60.0)
        assert fetcher.timeout == 60.0


class TestGitHubFetcherCanHandle:
    def test_can_handle_github_urls(self):
        """GitHubFetcher handles GitHub URLs."""
        assert GitHubFetcher.can_handle("https://github.com/owner/repo")
        assert GitHubFetcher.can_handle("http://github.com/owner/repo")
        assert GitHubFetcher.can_handle("https://api.github.com/repos/owner/repo")

    def test_cannot_handle_non_github(self):
        """GitHubFetcher rejects non-GitHub URLs."""
        assert not GitHubFetcher.can_handle("https://gitlab.com/owner/repo")
        assert not GitHubFetcher.can_handle("https://bitbucket.org/owner/repo")
        assert not GitHubFetcher.can_handle("https://example.com/repo")


class TestGitHubRouteParsing:
    def test_parse_repo(self):
        """Parse repository URL."""
        fetcher = GitHubFetcher()
        route = fetcher._parse_uri("https://github.com/owner/repo")
        assert route.type == "repo"
        assert route.owner == "owner"
        assert route.repo == "repo"

    def test_parse_file(self):
        """Parse file URL."""
        fetcher = GitHubFetcher()
        route = fetcher._parse_uri("https://github.com/owner/repo/blob/main/README.md")
        assert route.type == "file"
        assert route.owner == "owner"
        assert route.repo == "repo"
        assert route.branch == "main"
        assert route.path == "README.md"

    def test_parse_issue(self):
        """Parse issue URL."""
        fetcher = GitHubFetcher()
        route = fetcher._parse_uri("https://github.com/owner/repo/issues/42")
        assert route.type == "issue"
        assert route.owner == "owner"
        assert route.repo == "repo"
        assert route.number == 42

    def test_parse_pr(self):
        """Parse PR URL."""
        fetcher = GitHubFetcher()
        route = fetcher._parse_uri("https://github.com/owner/repo/pull/123")
        assert route.type == "pr"
        assert route.owner == "owner"
        assert route.repo == "repo"
        assert route.number == 123

    def test_parse_issues_container(self):
        """Parse issues list URL."""
        fetcher = GitHubFetcher()
        route = fetcher._parse_uri("https://github.com/owner/repo/issues")
        assert route.type == "issues"
        assert route.owner == "owner"
        assert route.repo == "repo"

    def test_parse_releases(self):
        """Parse releases URL."""
        fetcher = GitHubFetcher()
        route = fetcher._parse_uri("https://github.com/owner/repo/releases")
        assert route.type == "releases"
        assert route.owner == "owner"
        assert route.repo == "repo"


class TestDetectLanguage:
    def test_detect_python(self):
        """Detect Python files."""
        assert detect_language("test.py") == "python"
        assert detect_language("module.py") == "python"

    def test_detect_javascript(self):
        """Detect JavaScript files."""
        assert detect_language("app.js") == "javascript"
        assert detect_language("component.jsx") == "javascript"

    def test_detect_typescript(self):
        """Detect TypeScript files."""
        assert detect_language("app.ts") == "typescript"
        assert detect_language("component.tsx") == "typescript"

    def test_detect_markdown(self):
        """Detect Markdown files."""
        assert detect_language("README.md") == "markdown"
        assert detect_language("doc.markdown") == "markdown"

    def test_detect_yaml(self):
        """Detect YAML files."""
        assert detect_language("config.yaml") == "yaml"
        assert detect_language("config.yml") == "yaml"

    def test_detect_unknown(self):
        """Return None for unknown extensions."""
        assert detect_language("file.unknown") is None
        assert detect_language("README") is None


class TestGitHubSchemas:
    def test_github_file_tags(self):
        """GitHubFile has correct tags."""
        content = TextDocument(
            source_uri="test.py",
            content="print('hello')",
            format=TextFormat.CODE,
            language="python",
        )
        file = GitHubFile(
            path="test.py",
            name="test.py",
            sha="abc123",
            size_bytes=100,
            content=content,
            language="python",
            url="https://github.com/owner/repo/blob/main/test.py",
        )
        assert "github" in file.tags
        assert "code" in file.tags
        assert "file" in file.tags
        assert "python" in file.tags

    def test_github_issue_tags(self):
        """GitHubIssue has correct tags."""
        issue = GitHubIssue(
            number=1,
            title="Test Issue",
            state="open",
            author="testuser",
            labels=["bug", "urgent"],
            comments=[],
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://github.com/owner/repo/issues/1",
        )
        assert "github" in issue.tags
        assert "issue" in issue.tags
        assert "open" in issue.tags

    def test_github_pr_tags(self):
        """GitHubPR has correct tags."""
        pr = GitHubPR(
            number=1,
            title="Test PR",
            state="open",
            author="testuser",
            base_branch="main",
            head_branch="feature",
            labels=[],
            comments=[],
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://github.com/owner/repo/pull/1",
        )
        assert "github" in pr.tags
        assert "pull_request" in pr.tags
        assert "open" in pr.tags

    def test_github_release_tags(self):
        """GitHubRelease has correct tags."""
        release = GitHubRelease(
            tag_name="v1.0.0",
            name="Release 1.0",
            author="testuser",
            draft=False,
            prerelease=False,
            created_at=datetime.now(),
            url="https://github.com/owner/repo/releases/tag/v1.0.0",
        )
        assert "github" in release.tags
        assert "release" in release.tags

    def test_github_repo_tags(self):
        """GitHubRepo merges tags from children."""
        content = TextDocument(
            source_uri="README.md",
            content="# Test",
            format=TextFormat.MARKDOWN,
            language="markdown",
        )
        file = GitHubFile(
            path="README.md",
            name="README.md",
            sha="abc123",
            size_bytes=100,
            content=content,
            language="markdown",
            url="https://github.com/owner/repo/blob/main/README.md",
        )
        repo = GitHubRepo(
            owner="owner",
            name="repo",
            full_name="owner/repo",
            description="A test repo",
            default_branch="main",
            stars=100,
            forks=10,
            language="Python",
            topics=["test"],
            files=[file],
            url="https://github.com/owner/repo",
        )
        assert "github" in repo.tags
        assert "repo" in repo.tags
        assert "python" in repo.tags

    def test_github_issue_container_tags(self):
        """GitHubIssueContainer merges tags."""
        issue = GitHubIssue(
            number=1,
            title="Test",
            state="open",
            author="user",
            labels=[],
            comments=[],
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://github.com/owner/repo/issues/1",
        )
        container = GitHubIssueContainer(
            repo="owner/repo",
            state="open",
            items=[issue],
        )
        assert "github" in container.tags
        assert "issues" in container.tags


class TestGitHubFetcherIntegration:
    @pytest.mark.asyncio
    async def test_fetch_repo(self):
        """Test fetching a repository."""
        fetcher = GitHubFetcher()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "description": "A test repo",
            "default_branch": "main",
            "stargazers_count": 100,
            "forks_count": 10,
            "language": "Python",
            "topics": ["test"],
            "html_url": "https://github.com/owner/repo",
        }

        mock_readme_response = MagicMock()
        mock_readme_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=[mock_response, mock_readme_response]
            )

            route = GitHubRoute(type="repo", owner="owner", repo="repo")
            result = await fetcher._fetch_repo(route, "https://github.com/owner/repo")

            assert result.owner == "owner"
            assert result.name == "repo"
            assert result.full_name == "owner/repo"
            assert result.stars == 100

    @pytest.mark.asyncio
    async def test_fetch_file(self):
        """Test fetching a file."""
        fetcher = GitHubFetcher()

        import base64

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": base64.b64encode(b"print('hello')").decode("utf-8"),
            "sha": "abc123",
            "size": 14,
            "html_url": "https://github.com/owner/repo/blob/main/test.py",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            route = GitHubRoute(
                type="file", owner="owner", repo="repo", branch="main", path="test.py"
            )
            result = await fetcher._fetch_file(
                route, "https://github.com/owner/repo/blob/main/test.py"
            )

            assert result.name == "test.py"
            assert result.language == "python"
            assert result.content.content == "print('hello')"

    @pytest.mark.asyncio
    async def test_fetch_issue(self):
        """Test fetching an issue."""
        fetcher = GitHubFetcher()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "number": 1,
            "title": "Test Issue",
            "body": "Issue body",
            "state": "open",
            "user": {"login": "testuser"},
            "labels": [{"name": "bug"}],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "html_url": "https://github.com/owner/repo/issues/1",
        }

        mock_comments_response = MagicMock()
        mock_comments_response.status_code = 200
        mock_comments_response.json.return_value = []

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=[mock_response, mock_comments_response]
            )

            route = GitHubRoute(type="issue", owner="owner", repo="repo", number=1)
            result = await fetcher._fetch_issue(route, "https://github.com/owner/repo/issues/1")

            assert result.number == 1
            assert result.title == "Test Issue"
            assert result.author == "testuser"

    @pytest.mark.asyncio
    async def test_fetch_issues_container(self):
        """Test fetching issues container."""
        fetcher = GitHubFetcher()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "number": 1,
                "title": "Issue 1",
                "body": "Body",
                "state": "open",
                "user": {"login": "user1"},
                "labels": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "html_url": "https://github.com/owner/repo/issues/1",
            }
        ]

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            route = GitHubRoute(type="issues", owner="owner", repo="repo")
            result = await fetcher._fetch_issues(route, "https://github.com/owner/repo/issues")

            assert result.repo == "owner/repo"
            assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_fetch_releases(self):
        """Test fetching releases."""
        fetcher = GitHubFetcher()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "tag_name": "v1.0.0",
                "name": "Release 1.0",
                "body": "Release notes",
                "author": {"login": "testuser"},
                "draft": False,
                "prerelease": False,
                "created_at": "2024-01-01T00:00:00Z",
                "html_url": "https://github.com/owner/repo/releases/tag/v1.0.0",
            }
        ]

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            route = GitHubRoute(type="releases", owner="owner", repo="repo")
            result = await fetcher._fetch_releases(route, "https://github.com/owner/repo/releases")

            assert result.repo == "owner/repo"
            assert len(result.items) == 1
            assert result.items[0].tag_name == "v1.0.0"
