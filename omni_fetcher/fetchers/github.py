"""GitHub fetcher for OmniFetcher."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from omni_fetcher.core.exceptions import FetchError, SourceNotFoundError
from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.atomics import TextDocument, TextFormat
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


API_BASE = "https://api.github.com"


@dataclass
class GitHubRoute:
    """Parsed GitHub URI route."""

    type: str
    owner: str
    repo: str
    branch: Optional[str] = None
    path: Optional[str] = None
    number: Optional[int] = None


@source(
    name="github",
    uri_patterns=["github.com", "api.github.com"],
    priority=15,
    description="Fetch from GitHub — files, repos, issues, PRs, releases",
    auth={"type": "bearer", "token_env": "GITHUB_TOKEN"},
)
class GitHubFetcher(BaseFetcher):
    """Fetcher for GitHub API - repos, files, issues, PRs, releases."""

    name = "github"
    priority = 15
    BASE_API = API_BASE

    def __init__(self, timeout: float = 30.0):
        super().__init__()
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if URI is a GitHub URL."""
        if not uri:
            return False
        lower_uri = uri.lower()
        return "github.com" in lower_uri or "api.github.com" in lower_uri

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch from GitHub based on URI type."""
        route = self._parse_uri(uri)

        if route.type == "file":
            return await self._fetch_file(route, uri)
        elif route.type == "issue":
            return await self._fetch_issue(route, uri)
        elif route.type == "pr":
            return await self._fetch_pr(route, uri)
        elif route.type == "issues":
            return await self._fetch_issues(route, uri, **kwargs)
        elif route.type == "releases":
            return await self._fetch_releases(route, uri)
        elif route.type == "pulls":
            return await self._fetch_prs(route, uri, **kwargs)
        else:
            return await self._fetch_repo(route, uri, **kwargs)

    def _parse_uri(self, uri: str) -> GitHubRoute:
        """Parse GitHub URI to determine route type."""
        parsed = urlparse(uri)
        path = parsed.path.strip("/")
        parts = path.split("/")

        if "api.github.com" in uri.lower():
            if len(parts) >= 3 and parts[0] == "repos":
                parts = parts[1:]

        if len(parts) < 2:
            raise SourceNotFoundError(f"Invalid GitHub URI: {uri}")

        owner = parts[0]
        repo = parts[1].replace(".git", "")

        if len(parts) >= 4 and parts[2] == "blob":
            branch = parts[3]
            path = "/".join(parts[4:]) if len(parts) > 4 else ""
            return GitHubRoute(type="file", owner=owner, repo=repo, branch=branch, path=path)

        if len(parts) == 2:
            return GitHubRoute(type="repo", owner=owner, repo=repo)

        if len(parts) >= 3 and parts[2] == "issues":
            if len(parts) == 3:
                return GitHubRoute(type="issues", owner=owner, repo=repo)
            try:
                number = int(parts[3])
                return GitHubRoute(type="issue", owner=owner, repo=repo, number=number)
            except ValueError:
                return GitHubRoute(type="issues", owner=owner, repo=repo)

        if len(parts) >= 3 and parts[2] == "pull":
            if len(parts) == 3:
                return GitHubRoute(type="pulls", owner=owner, repo=repo)
            try:
                number = int(parts[3])
                return GitHubRoute(type="pr", owner=owner, repo=repo, number=number)
            except ValueError:
                return GitHubRoute(type="pulls", owner=owner, repo=repo)

        if len(parts) >= 3 and parts[2] == "releases":
            if len(parts) == 3:
                return GitHubRoute(type="releases", owner=owner, repo=repo)
            else:
                return GitHubRoute(
                    type="release",
                    owner=owner,
                    repo=repo,
                )

        return GitHubRoute(type="repo", owner=owner, repo=repo)

    def _get_headers(self) -> dict[str, str]:
        """Get headers including auth if configured."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "omni_fetcher",
        }
        headers.update(self.get_auth_headers())
        return headers

    async def _fetch_repo(self, route: GitHubRoute, uri: str, **kwargs: Any) -> GitHubRepo:
        """Fetch repository metadata and optionally files/issues/PRs."""
        include_files = kwargs.get("include_files", False)
        include_issues = kwargs.get("include_issues", False)
        include_prs = kwargs.get("include_prs", False)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            repo_response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}",
                headers=self._get_headers(),
            )

            if repo_response.status_code == 404:
                raise SourceNotFoundError(f"Repository not found: {route.owner}/{route.repo}")
            if repo_response.status_code == 403:
                raise FetchError(
                    uri, "Rate limit exceeded. Set GITHUB_TOKEN env var for higher limits"
                )
            if repo_response.status_code != 200:
                raise FetchError(uri, f"GitHub API error: {repo_response.status_code}")

            repo_data = repo_response.json()

        readme = None
        files = []
        repo_default_branch = repo_data.get("default_branch", "main")

        try:
            readme_response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/readme",
                headers=self._get_headers(),
            )
            if readme_response.status_code == 200:
                readme_data = readme_response.json()
                content = ""
                if readme_data.get("content"):
                    content = base64.b64decode(readme_data["content"]).decode("utf-8")
                readme = TextDocument(
                    source_uri=f"{uri}/blob/{repo_default_branch}/README.md",
                    content=content,
                    format=TextFormat.MARKDOWN,
                    language="markdown",
                )
        except Exception:
            pass

        if include_files:
            files = await self._fetch_repo_files(route, repo_default_branch, **kwargs)

        return GitHubRepo(
            owner=route.owner,
            name=route.repo,
            full_name=f"{route.owner}/{route.repo}",
            description=repo_data.get("description"),
            default_branch=repo_default_branch,
            stars=repo_data.get("stargazers_count", 0),
            forks=repo_data.get("forks_count", 0),
            language=repo_data.get("language"),
            topics=repo_data.get("topics", []),
            readme=readme,
            files=files,
            url=repo_data.get("html_url", uri),
        )

    async def _fetch_repo_files(
        self, route: GitHubRoute, branch: str, **kwargs: Any
    ) -> list[GitHubFile]:
        """Fetch repository file tree."""
        max_files = kwargs.get("max_files", 100)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/git/trees/{branch}",
                params={"recursive": "1"},
                headers=self._get_headers(),
            )

            if response.status_code != 200:
                return []

            data = response.json()
            tree = data.get("tree", [])

        files = []
        for item in tree[:max_files]:
            if item.get("type") == "blob":
                path = item.get("path", "")
                if "/" not in path or path.count("/") == 1:
                    try:
                        file_obj = await self._fetch_single_file(
                            route.owner, route.repo, path, branch
                        )
                        if file_obj:
                            files.append(file_obj)
                    except Exception:
                        pass

        return files

    async def _fetch_file(self, route: GitHubRoute, uri: str) -> GitHubFile:
        """Fetch a single file from repository."""
        if not route.path:
            raise SourceNotFoundError(f"Invalid file path: {uri}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/contents/{route.path}",
                params={"ref": route.branch},
                headers=self._get_headers(),
            )

            if response.status_code == 404:
                raise SourceNotFoundError(f"File not found: {uri}")
            if response.status_code == 403:
                raise FetchError(
                    uri, "Rate limit exceeded. Set GITHUB_TOKEN env var for higher limits"
                )
            if response.status_code != 200:
                raise FetchError(uri, f"GitHub API error: {response.status_code}")

            data = response.json()

        content = ""
        if data.get("content"):
            content = base64.b64decode(data["content"]).decode("utf-8")

        name = route.path.split("/")[-1]
        language = detect_language(name)

        text_doc = TextDocument(
            source_uri=uri,
            content=content,
            format=TextFormat.CODE,
            language=language,
        )

        return GitHubFile(
            path=route.path,
            name=name,
            sha=data.get("sha", ""),
            size_bytes=data.get("size", 0),
            branch=route.branch or "main",
            content=text_doc,
            language=language,
            url=data.get("html_url", uri),
        )

    async def _fetch_single_file(
        self, owner: str, repo: str, path: str, branch: str
    ) -> Optional[GitHubFile]:
        """Fetch a single file from repo for file listing."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.BASE_API}/repos/{owner}/{repo}/contents/{path}",
                    params={"ref": branch},
                    headers=self._get_headers(),
                )

                if response.status_code != 200:
                    return None

                data = response.json()

            content = ""
            if data.get("content"):
                content = base64.b64decode(data["content"]).decode("utf-8")

            name = path.split("/")[-1]
            language = detect_language(name)

            text_doc = TextDocument(
                source_uri=f"https://github.com/{owner}/{repo}/blob/{branch}/{path}",
                content=content,
                format=TextFormat.CODE,
                language=language,
            )

            return GitHubFile(
                path=path,
                name=name,
                sha=data.get("sha", ""),
                size_bytes=data.get("size", 0),
                branch=branch,
                content=text_doc,
                language=language,
                url=data.get("html_url", f"https://github.com/{owner}/{repo}/blob/{branch}/{path}"),
            )
        except Exception:
            return None

    async def _fetch_issue(self, route: GitHubRoute, uri: str) -> GitHubIssue:
        """Fetch a single issue."""
        include_comments = True

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/issues/{route.number}",
                headers=self._get_headers(),
            )

            if response.status_code == 404:
                raise SourceNotFoundError(f"Issue not found: {uri}")
            if response.status_code == 403:
                raise FetchError(
                    uri, "Rate limit exceeded. Set GITHUB_TOKEN env var for higher limits"
                )
            if response.status_code != 200:
                raise FetchError(uri, f"GitHub API error: {response.status_code}")

            data = response.json()

        body_doc = None
        if data.get("body"):
            body_doc = TextDocument(
                source_uri=uri,
                content=data["body"],
                format=TextFormat.PLAIN,
            )

        comments = []
        if include_comments:
            comments = await self._fetch_issue_comments(route)

        return GitHubIssue(
            number=data.get("number", 0),
            title=data.get("title", ""),
            body=body_doc,
            state=data.get("state", "open"),
            author=data.get("user", {}).get("login", ""),
            labels=[label.get("name", "") for label in data.get("labels", [])],
            comments=comments,
            created_at=datetime.fromisoformat(data.get("created_at", "").replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(data.get("updated_at", "").replace("Z", "+00:00")),
            url=data.get("html_url", uri),
        )

    async def _fetch_issue_comments(self, route: GitHubRoute) -> list[TextDocument]:
        """Fetch comments for an issue."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/issues/{route.number}/comments",
                headers=self._get_headers(),
            )

            if response.status_code != 200:
                return []

            comments_data = response.json()

        comments = []
        for comment in comments_data:
            text_doc = TextDocument(
                source_uri=comment.get("html_url", ""),
                content=comment.get("body", ""),
                format=TextFormat.PLAIN,
            )
            comments.append(text_doc)

        return comments

    async def _fetch_issues(
        self, route: GitHubRoute, uri: str, **kwargs: Any
    ) -> GitHubIssueContainer:
        """Fetch issues for a repository."""
        state = kwargs.get("state", "open")
        max_issues = kwargs.get("max_issues", 50)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/issues",
                params={"state": state, "per_page": min(max_issues, 100)},
                headers=self._get_headers(),
            )

            if response.status_code == 404:
                raise SourceNotFoundError(f"Repository not found: {route.owner}/{route.repo}")
            if response.status_code != 200:
                raise FetchError(uri, f"GitHub API error: {response.status_code}")

            issues_data = response.json()

        issues = []
        for issue_data in issues_data[:max_issues]:
            if "pull_request" in issue_data:
                continue

            body_doc = None
            if issue_data.get("body"):
                body_doc = TextDocument(
                    source_uri=issue_data.get("html_url", ""),
                    content=issue_data["body"],
                    format=TextFormat.PLAIN,
                )

            issue = GitHubIssue(
                number=issue_data.get("number", 0),
                title=issue_data.get("title", ""),
                body=body_doc,
                state=issue_data.get("state", "open"),
                author=issue_data.get("user", {}).get("login", ""),
                labels=[label.get("name", "") for label in issue_data.get("labels", [])],
                comments=[],
                created_at=datetime.fromisoformat(
                    issue_data.get("created_at", "").replace("Z", "+00:00")
                ),
                updated_at=datetime.fromisoformat(
                    issue_data.get("updated_at", "").replace("Z", "+00:00")
                ),
                url=issue_data.get("html_url", ""),
            )
            issues.append(issue)

        return GitHubIssueContainer(
            repo=f"{route.owner}/{route.repo}",
            state=state,
            items=issues,
            source_uri=uri,
            item_count=len(issues),
        )

    async def _fetch_pr(self, route: GitHubRoute, uri: str) -> GitHubPR:
        """Fetch a single pull request."""
        include_diff = False

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/pulls/{route.number}",
                headers=self._get_headers(),
            )

            if response.status_code == 404:
                raise SourceNotFoundError(f"PR not found: {uri}")
            if response.status_code != 200:
                raise FetchError(uri, f"GitHub API error: {response.status_code}")

            data = response.json()

        body_doc = None
        if data.get("body"):
            body_doc = TextDocument(
                source_uri=uri,
                content=data["body"],
                format=TextFormat.PLAIN,
            )

        comments = await self._fetch_pr_comments(route)

        diff_doc = None
        if include_diff:
            diff_doc = await self._fetch_pr_diff(route)

        merged = data.get("merged", False)
        state = "merged" if merged else data.get("state", "open")

        return GitHubPR(
            number=data.get("number", 0),
            title=data.get("title", ""),
            body=body_doc,
            state=state,
            author=data.get("user", {}).get("login", ""),
            base_branch=data.get("base", {}).get("ref", ""),
            head_branch=data.get("head", {}).get("ref", ""),
            labels=[label.get("name", "") for label in data.get("labels", [])],
            comments=comments,
            diff=diff_doc,
            created_at=datetime.fromisoformat(data.get("created_at", "").replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(data.get("updated_at", "").replace("Z", "+00:00")),
            url=data.get("html_url", uri),
        )

    async def _fetch_pr_comments(self, route: GitHubRoute) -> list[TextDocument]:
        """Fetch review comments for a PR."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/pulls/{route.number}/comments",
                headers=self._get_headers(),
            )

            if response.status_code != 200:
                return []

            comments_data = response.json()

        comments = []
        for comment in comments_data:
            text_doc = TextDocument(
                source_uri=comment.get("html_url", ""),
                content=comment.get("body", ""),
                format=TextFormat.PLAIN,
            )
            comments.append(text_doc)

        return comments

    async def _fetch_pr_diff(self, route: GitHubRoute) -> Optional[TextDocument]:
        """Fetch unified diff for a PR."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/pulls/{route.number}",
                headers={**self._get_headers(), "Accept": "application/vnd.github.v3.diff"},
            )

            if response.status_code != 200:
                return None

            return TextDocument(
                source_uri=f"https://github.com/{route.owner}/{route.repo}/pull/{route.number}",
                content=response.text,
                format=TextFormat.PLAIN,
            )

    async def _fetch_prs(self, route: GitHubRoute, uri: str, **kwargs: Any) -> GitHubPRContainer:
        """Fetch PRs for a repository."""
        state = kwargs.get("state", "open")
        max_prs = kwargs.get("max_prs", 50)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/pulls",
                params={"state": state, "per_page": min(max_prs, 100)},
                headers=self._get_headers(),
            )

            if response.status_code != 200:
                return GitHubPRContainer(
                    repo=f"{route.owner}/{route.repo}",
                    state=state,
                    items=[],
                    source_uri=uri,
                    item_count=0,
                )

            prs_data = response.json()

        prs = []
        for pr_data in prs_data[:max_prs]:
            merged = pr_data.get("merged", False)
            pr_state = "merged" if merged else pr_data.get("state", "open")

            body_doc = None
            if pr_data.get("body"):
                body_doc = TextDocument(
                    source_uri=pr_data.get("html_url", ""),
                    content=pr_data["body"],
                    format=TextFormat.PLAIN,
                )

            pr = GitHubPR(
                number=pr_data.get("number", 0),
                title=pr_data.get("title", ""),
                body=body_doc,
                state=pr_state,
                author=pr_data.get("user", {}).get("login", ""),
                base_branch=pr_data.get("base", {}).get("ref", ""),
                head_branch=pr_data.get("head", {}).get("ref", ""),
                labels=[label.get("name", "") for label in pr_data.get("labels", [])],
                comments=[],
                diff=None,
                created_at=datetime.fromisoformat(
                    pr_data.get("created_at", "").replace("Z", "+00:00")
                ),
                updated_at=datetime.fromisoformat(
                    pr_data.get("updated_at", "").replace("Z", "+00:00")
                ),
                url=pr_data.get("html_url", ""),
            )
            prs.append(pr)

        return GitHubPRContainer(
            repo=f"{route.owner}/{route.repo}",
            state=state,
            items=prs,
            source_uri=uri,
            item_count=len(prs),
        )

    async def _fetch_releases(self, route: GitHubRoute, uri: str) -> GitHubReleaseContainer:
        """Fetch releases for a repository."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_API}/repos/{route.owner}/{route.repo}/releases",
                headers=self._get_headers(),
            )

            if response.status_code != 200:
                return GitHubReleaseContainer(
                    repo=f"{route.owner}/{route.repo}",
                    items=[],
                    source_uri=uri,
                    item_count=0,
                )

            releases_data = response.json()

        releases = []
        for release_data in releases_data[:20]:
            body_doc = None
            if release_data.get("body"):
                body_doc = TextDocument(
                    source_uri=release_data.get("html_url", ""),
                    content=release_data["body"],
                    format=TextFormat.MARKDOWN,
                )

            release = GitHubRelease(
                tag_name=release_data.get("tag_name", ""),
                name=release_data.get("name"),
                body=body_doc,
                author=release_data.get("author", {}).get("login", ""),
                draft=release_data.get("draft", False),
                prerelease=release_data.get("prerelease", False),
                created_at=datetime.fromisoformat(
                    release_data.get("created_at", "").replace("Z", "+00:00")
                ),
                url=release_data.get("html_url", ""),
            )
            releases.append(release)

        return GitHubReleaseContainer(
            repo=f"{route.owner}/{route.repo}",
            items=releases,
            source_uri=uri,
            item_count=len(releases),
        )
