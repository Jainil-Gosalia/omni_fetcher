"""The canonical ``github`` connector for the OmniFetcher v1 contract.

Fetches GitHub resources -- repositories, files, single issues / pull
requests / releases, and the issue / PR / release *lists* -- over the GitHub
REST API and emits each as a canonical ``CompositionNode`` tree wrapped in a
``Result``. The former ``GitHubIssue`` / ``GitHubPR`` / ``GitHubRepo`` /
``GitHubFile`` / ``GitHubRelease`` shapes are gone: their content is
re-expressed as canonical atoms and their descriptive fields move into the
``Metadata`` core plus the namespaced ``source_extra["github"]`` mapping.

Each resource gets a sensible advisory semantic ``kind``:

- a repository      -> ``"repo"``
- a single file     -> ``"file"``
- a single issue    -> ``"issue"``
- a single PR       -> ``"pull_request"``
- a single release  -> ``"release"``
- a list endpoint   -> a container node (``"issues"`` / ``"pull_requests"`` /
  ``"releases"``) whose children are per-item nodes.

Content (issue/PR/release bodies, comments, file contents, the README) lives
in ``Text`` atoms only. Descriptive fields (state, author, labels, assignees,
timestamps, url, numbers, stars, ...) live in metadata, never inline on an
atom.

Expected failures are returned as typed ``Error`` results, never raised: HTTP
status maps onto the taxonomy (404 -> not-found, 401 -> auth-failed,
403 -> permission-denied or rate-limited when a rate-limit header is present,
429 -> rate-limited, 5xx -> transient, other 4xx -> invalid-input), and a
body that is not valid JSON is a ``PARSE_ERROR``. Auth is injected per call
via ``auth`` and resolved transiently into request headers; nothing is read
from the ambient environment and nothing is stored on the instance.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

import httpx

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential, NormalizedAuthResolver
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Error, Result, error, success
from omni_fetcher.v1.zoom import ZoomSpec

# The source namespace under which this connector files descriptive fields in
# ``Metadata.source_extra``.
SOURCE_NAMESPACE = "github"

# The GitHub REST API base.
API_BASE = "https://api.github.com"

# Default transport timeout (seconds) for a single request.
DEFAULT_TIMEOUT = 30.0

# Bounds for list endpoints, keeping a "list" fetch terminating and modest.
MAX_LIST_ITEMS = 50
MAX_RELEASES = 20

# File-extension -> language hint, for tagging a file's ``Text`` atom with a
# content language. Content-only: the language describes the text itself.
_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".r": "r",
    ".lua": "lua",
    ".sh": "shell",
    ".bash": "bash",
    ".zsh": "zsh",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".log": "log",
    ".csv": "csv",
    ".tsv": "tsv",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".fs": "fsharp",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".vue": "vue",
    ".svelte": "svelte",
    ".graphql": "graphql",
    ".gql": "graphql",
}


def _detect_language(path: str) -> Optional[str]:
    """Detect a content-language hint from a file path's extension."""
    lower_path = path.lower()
    for ext, lang in _EXTENSION_TO_LANGUAGE.items():
        if lower_path.endswith(ext):
            return lang
    return None


def _has_rate_limit_signal(response: httpx.Response) -> bool:
    """Report whether a 403 looks like a rate-limit rather than a deny.

    GitHub returns 403 for both "rate-limited" and "forbidden". A rate-limit
    response carries ``X-RateLimit-Remaining: 0`` and/or a ``Retry-After``
    header; otherwise it is a genuine permission denial.
    """
    headers = response.headers
    if headers.get("retry-after"):
        return True
    remaining = headers.get("x-ratelimit-remaining")
    return remaining is not None and remaining.strip() == "0"


def _status_to_error_kind(response: httpx.Response) -> ErrorKind:
    """Map an HTTP response onto a taxonomy ``ErrorKind``."""
    status_code = response.status_code
    if status_code == 401:
        return ErrorKind.AUTH_FAILED
    if status_code == 403:
        if _has_rate_limit_signal(response):
            return ErrorKind.RATE_LIMITED
        return ErrorKind.PERMISSION_DENIED
    if status_code == 404:
        return ErrorKind.NOT_FOUND
    if status_code == 429:
        return ErrorKind.RATE_LIMITED
    if 500 <= status_code <= 599:
        return ErrorKind.TRANSIENT
    return ErrorKind.INVALID_INPUT


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse a GitHub ISO-8601 timestamp into a datetime, if present."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _login(user: Any) -> Optional[str]:
    """Extract a ``login`` from a GitHub user object, defensively."""
    if isinstance(user, dict):
        login = user.get("login")
        if isinstance(login, str) and login:
            return login
    return None


def _label_names(labels: Any) -> list[str]:
    """Extract label name strings from a GitHub labels array."""
    names: list[str] = []
    if isinstance(labels, list):
        for label in labels:
            if isinstance(label, dict):
                name = label.get("name")
                if isinstance(name, str):
                    names.append(name)
    return names


@dataclass
class _Route:
    """A parsed GitHub URI route."""

    type: str
    owner: str
    repo: str
    branch: Optional[str] = None
    path: Optional[str] = None
    number: Optional[int] = None


class GitHubConnector(BaseFetcher):
    """
    Canonical GitHub connector
    ===============================================
    Fetches GitHub repositories, files, issues, pull requests, and releases
    (single and list) over the REST API and streams each as a canonical
    ``CompositionNode`` tree (advisory ``kind`` per resource). Content goes
    into ``Text`` atoms; descriptive GitHub fields are filed in the metadata
    core plus ``source_extra["github"]``. List endpoints yield a container
    node whose children are per-item nodes. Expected failures are returned as
    typed ``Error`` results, never raised.
    ===============================================
    NOTE:
        1. This connector implements only ``stream()``; ``fetch()`` is the
           inherited base sugar that collects the bounded stream.
        2. Credentials are passed per call via ``auth`` and resolved
           transiently into request headers; nothing is stored on the
           instance and the ambient environment is never read.
        3. Every emitted node carries an advisory semantic ``kind`` and files
           its descriptive fields under ``source_extra["github"]``.

    Attributes
    ----------
        timeout:
            Per-request transport timeout in seconds.

    Methods
    -------
        can_handle:
        stream:
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """
        Create a GitHub connector

        Parameters
        ----------
            timeout:
                Per-request transport timeout in seconds.
        """
        self.timeout = timeout
        self._auth_resolver = NormalizedAuthResolver()

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI is a GitHub URL

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` when ``uri`` points at github.com or the GitHub API.
        """
        if not uri:
            return False
        lowered = uri.lower()
        return "github.com" in lowered or "api.github.com" in lowered

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the GitHub resource at a URI as canonical results

        Parses the URI to route to a repository, file, single issue / PR /
        release, or a list endpoint, performs the bounded GitHub REST
        request(s), and yields exactly one ``Result``: a ``Success`` whose
        tree is a canonical node (single resource) or container node (list),
        or a typed ``Error``. The advisory ``kind`` reflects the resource
        (``repo`` / ``file`` / ``issue`` / ``pull_request`` / ``release``, or
        a container kind for lists). Content is carried in ``Text`` atoms;
        descriptive fields are filed under ``source_extra["github"]``.

        NOTE:
            1. Expected failures are yielded as ``Error`` results, never
               raised; an unparseable URI yields ``Error(INVALID_INPUT)``.
            2. ``zoom`` is accepted for contract conformance; this connector
               emits nodes at natural granularity and does not act on it.

        Parameters
        ----------
            uri:
                The GitHub URL identifying the resource to fetch.
            auth:
                The per-call credential, or ``None`` for unauthenticated
                access. Resolved transiently into request headers.
            zoom:
                Optional per-atom-type zoom spec; accepted but not acted on.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        try:
            route = self._parse_uri(uri)
        except ValueError as exc:
            yield error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"invalid GitHub URI: {exc}",
                locator=uri,
            )
            return

        headers = self._request_headers(auth)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                result = await self._dispatch(client, route, uri, headers)
        except httpx.HTTPError as exc:
            yield error(
                kind=ErrorKind.TRANSIENT,
                message=f"request failed: {exc}",
                locator=uri,
            )
            return

        yield result

    def _request_headers(
        self, auth: Optional[AuthCredential]
    ) -> dict[str, str]:
        """Build request headers, resolving the per-call credential."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "omni_fetcher",
        }
        headers.update(self._auth_resolver.resolve_headers(auth))
        return headers

    async def _dispatch(
        self,
        client: httpx.AsyncClient,
        route: _Route,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Route a parsed URI to the matching fetch handler."""
        if route.type == "file":
            return await self._fetch_file(client, route, uri, headers)
        if route.type == "issue":
            return await self._fetch_issue(client, route, uri, headers)
        if route.type == "pull_request":
            return await self._fetch_pr(client, route, uri, headers)
        if route.type == "release":
            return await self._fetch_release(client, route, uri, headers)
        if route.type == "issues":
            return await self._fetch_issues(client, route, uri, headers)
        if route.type == "pulls":
            return await self._fetch_prs(client, route, uri, headers)
        if route.type == "releases":
            return await self._fetch_releases(client, route, uri, headers)
        return await self._fetch_repo(client, route, uri, headers)

    def _parse_uri(self, uri: str) -> _Route:
        """Parse a GitHub URI into a route, or raise ``ValueError``."""
        parsed = urlparse(uri)
        path = parsed.path.strip("/")
        parts = [p for p in path.split("/") if p]

        if "api.github.com" in uri.lower():
            if len(parts) >= 3 and parts[0] == "repos":
                parts = parts[1:]

        if len(parts) < 2:
            raise ValueError(uri)

        owner = parts[0]
        repo = parts[1].replace(".git", "")

        if len(parts) >= 4 and parts[2] == "blob":
            branch = parts[3]
            file_path = "/".join(parts[4:]) if len(parts) > 4 else ""
            return _Route(
                type="file",
                owner=owner,
                repo=repo,
                branch=branch,
                path=file_path,
            )

        if len(parts) == 2:
            return _Route(type="repo", owner=owner, repo=repo)

        section = parts[2]
        if section == "issues":
            return self._parse_numbered(parts, owner, repo, "issue", "issues")
        if section == "pull":
            return self._parse_numbered(
                parts, owner, repo, "pull_request", "pulls"
            )
        if section in ("pulls",):
            return _Route(type="pulls", owner=owner, repo=repo)
        if section == "releases":
            return self._parse_release_section(parts, owner, repo)

        return _Route(type="repo", owner=owner, repo=repo)

    @staticmethod
    def _parse_numbered(
        parts: list[str],
        owner: str,
        repo: str,
        single_type: str,
        list_type: str,
    ) -> _Route:
        """Resolve a ``.../<section>[/<n>]`` URI to single-or-list route."""
        if len(parts) == 3:
            return _Route(type=list_type, owner=owner, repo=repo)
        try:
            number = int(parts[3])
        except ValueError:
            return _Route(type=list_type, owner=owner, repo=repo)
        return _Route(
            type=single_type, owner=owner, repo=repo, number=number
        )

    @staticmethod
    def _parse_release_section(
        parts: list[str], owner: str, repo: str
    ) -> _Route:
        """Resolve a ``.../releases[/tag/<tag>]`` URI to a route."""
        # A specific release is addressed as ``releases/tag/<tag>``; the tag
        # itself is not a number, so single-release fetch needs the tag.
        if len(parts) >= 5 and parts[3] == "tag":
            return _Route(
                type="release",
                owner=owner,
                repo=repo,
                path=parts[4],
            )
        return _Route(type="releases", owner=owner, repo=repo)

    def _repo_api(self, route: _Route, suffix: str = "") -> str:
        """Build a ``/repos/<owner>/<repo>`` API URL with a suffix."""
        base = f"{API_BASE}/repos/{route.owner}/{route.repo}"
        return f"{base}{suffix}"

    @staticmethod
    def _decode_content(data: dict[str, Any]) -> str:
        """Decode a base64 ``content`` field from a GitHub contents payload."""
        raw = data.get("content")
        if not raw:
            return ""
        try:
            return base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeError):
            return ""

    async def _fetch_repo(
        self,
        client: httpx.AsyncClient,
        route: _Route,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch repository metadata plus its README as one ``repo`` node."""
        response = await client.get(self._repo_api(route), headers=headers)
        if response.status_code >= 400:
            return self._http_error(response, uri)
        try:
            data = response.json()
        except ValueError as exc:
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=f"repository body is not valid JSON: {exc}",
                locator=uri,
            )

        default_branch = data.get("default_branch", "main")
        atoms: list[Text] = []
        readme = await self._fetch_readme(client, route, headers)
        if readme is not None:
            atoms.append(readme)

        node = build_node(
            kind="repo",
            atoms=atoms,
            id=data.get("full_name"),
            author=route.owner,
            source_url=data.get("html_url", uri),
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "owner": route.owner,
                "name": route.repo,
                "full_name": data.get("full_name"),
                "description": data.get("description"),
                "default_branch": default_branch,
                "stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "language": data.get("language"),
                "topics": data.get("topics", []),
                "url": data.get("html_url", uri),
            },
        )
        return success(node)

    async def _fetch_readme(
        self,
        client: httpx.AsyncClient,
        route: _Route,
        headers: dict[str, str],
    ) -> Optional[Text]:
        """Best-effort fetch of a repo README as a markdown ``Text`` atom."""
        try:
            response = await client.get(
                self._repo_api(route, "/readme"), headers=headers
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        content = self._decode_content(data)
        if not content:
            return None
        return Text(
            content=content,
            format=TextFormat.MARKDOWN,
            language="markdown",
        )

    async def _fetch_file(
        self,
        client: httpx.AsyncClient,
        route: _Route,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch a single file as a ``file`` node with a ``Text`` atom."""
        if not route.path:
            return error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"missing file path: {uri}",
                locator=uri,
            )
        params = {"ref": route.branch} if route.branch else None
        response = await client.get(
            self._repo_api(route, f"/contents/{route.path}"),
            params=params,
            headers=headers,
        )
        if response.status_code >= 400:
            return self._http_error(response, uri)
        try:
            data = response.json()
        except ValueError as exc:
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=f"file body is not valid JSON: {exc}",
                locator=uri,
            )

        name = route.path.split("/")[-1]
        language = _detect_language(name)
        content = self._decode_content(data)
        atom = Text(
            content=content,
            format=TextFormat.CODE,
            language=language,
        )
        node = build_node(
            kind="file",
            atoms=[atom],
            id=data.get("sha"),
            source_url=data.get("html_url", uri),
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "path": route.path,
                "name": name,
                "sha": data.get("sha", ""),
                "size_bytes": data.get("size", 0),
                "branch": route.branch or "main",
                "language": language,
                "url": data.get("html_url", uri),
            },
        )
        return success(node)

    async def _fetch_issue(
        self,
        client: httpx.AsyncClient,
        route: _Route,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch a single issue (with comments) as an ``issue`` node."""
        response = await client.get(
            self._repo_api(route, f"/issues/{route.number}"),
            headers=headers,
        )
        if response.status_code >= 400:
            return self._http_error(response, uri)
        try:
            data = response.json()
        except ValueError as exc:
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=f"issue body is not valid JSON: {exc}",
                locator=uri,
            )

        comments = await self._fetch_comments(
            client, self._repo_api(route, f"/issues/{route.number}/comments"),
            headers,
        )
        node = self._build_issue_node(data, uri, comments)
        return success(node)

    def _build_issue_node(
        self,
        data: dict[str, Any],
        uri: str,
        comments: list[Text],
    ) -> CompositionNode:
        """Assemble an ``issue`` node from issue JSON + comment atoms."""
        atoms: list[Text] = []
        body = data.get("body")
        if body:
            atoms.append(Text(content=body, format=TextFormat.MARKDOWN))
        atoms.extend(comments)
        return build_node(
            kind="issue",
            atoms=atoms,
            id=str(data.get("number")) if data.get("number") else None,
            created=_parse_timestamp(data.get("created_at")),
            updated=_parse_timestamp(data.get("updated_at")),
            author=_login(data.get("user")),
            source_url=data.get("html_url", uri),
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "number": data.get("number"),
                "title": data.get("title", ""),
                "state": data.get("state", "open"),
                "author": _login(data.get("user")),
                "labels": _label_names(data.get("labels")),
                "assignees": [
                    login
                    for login in (
                        _login(a) for a in data.get("assignees", []) or []
                    )
                    if login
                ],
                "comment_count": data.get("comments", 0),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "url": data.get("html_url", uri),
            },
        )

    async def _fetch_pr(
        self,
        client: httpx.AsyncClient,
        route: _Route,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch a single pull request as a ``pull_request`` node."""
        response = await client.get(
            self._repo_api(route, f"/pulls/{route.number}"),
            headers=headers,
        )
        if response.status_code >= 400:
            return self._http_error(response, uri)
        try:
            data = response.json()
        except ValueError as exc:
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=f"PR body is not valid JSON: {exc}",
                locator=uri,
            )

        comments = await self._fetch_comments(
            client, self._repo_api(route, f"/pulls/{route.number}/comments"),
            headers,
        )
        node = self._build_pr_node(data, uri, comments)
        return success(node)

    def _build_pr_node(
        self,
        data: dict[str, Any],
        uri: str,
        comments: list[Text],
    ) -> CompositionNode:
        """Assemble a ``pull_request`` node from PR JSON + comment atoms."""
        atoms: list[Text] = []
        body = data.get("body")
        if body:
            atoms.append(Text(content=body, format=TextFormat.MARKDOWN))
        atoms.extend(comments)
        merged = data.get("merged", False)
        state = "merged" if merged else data.get("state", "open")
        base = data.get("base") or {}
        head = data.get("head") or {}
        return build_node(
            kind="pull_request",
            atoms=atoms,
            id=str(data.get("number")) if data.get("number") else None,
            created=_parse_timestamp(data.get("created_at")),
            updated=_parse_timestamp(data.get("updated_at")),
            author=_login(data.get("user")),
            source_url=data.get("html_url", uri),
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "number": data.get("number"),
                "title": data.get("title", ""),
                "state": state,
                "author": _login(data.get("user")),
                "base_branch": base.get("ref", ""),
                "head_branch": head.get("ref", ""),
                "labels": _label_names(data.get("labels")),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "url": data.get("html_url", uri),
            },
        )

    async def _fetch_release(
        self,
        client: httpx.AsyncClient,
        route: _Route,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch a single release (by tag) as a ``release`` node."""
        suffix = f"/releases/tags/{route.path}"
        response = await client.get(
            self._repo_api(route, suffix), headers=headers
        )
        if response.status_code >= 400:
            return self._http_error(response, uri)
        try:
            data = response.json()
        except ValueError as exc:
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=f"release body is not valid JSON: {exc}",
                locator=uri,
            )
        return success(self._build_release_node(data, uri))

    def _build_release_node(
        self, data: dict[str, Any], uri: str
    ) -> CompositionNode:
        """Assemble a ``release`` node from release JSON."""
        atoms: list[Text] = []
        body = data.get("body")
        if body:
            atoms.append(Text(content=body, format=TextFormat.MARKDOWN))
        return build_node(
            kind="release",
            atoms=atoms,
            id=data.get("tag_name"),
            created=_parse_timestamp(data.get("created_at")),
            author=_login(data.get("author")),
            source_url=data.get("html_url", uri),
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "tag_name": data.get("tag_name", ""),
                "name": data.get("name"),
                "author": _login(data.get("author")),
                "draft": data.get("draft", False),
                "prerelease": data.get("prerelease", False),
                "created_at": data.get("created_at"),
                "url": data.get("html_url", uri),
            },
        )

    async def _fetch_comments(
        self,
        client: httpx.AsyncClient,
        api_url: str,
        headers: dict[str, str],
    ) -> list[Text]:
        """Best-effort fetch of comment bodies as ``Text`` atoms."""
        try:
            response = await client.get(api_url, headers=headers)
        except httpx.HTTPError:
            return []
        if response.status_code != 200:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        atoms: list[Text] = []
        if isinstance(payload, list):
            for comment in payload:
                if not isinstance(comment, dict):
                    continue
                body = comment.get("body")
                if body:
                    atoms.append(
                        Text(content=body, format=TextFormat.MARKDOWN)
                    )
        return atoms

    async def _fetch_issues(
        self,
        client: httpx.AsyncClient,
        route: _Route,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch a repo's issues as an ``issues`` container of child nodes."""
        response = await client.get(
            self._repo_api(route, "/issues"),
            params={"state": "open", "per_page": MAX_LIST_ITEMS},
            headers=headers,
        )
        if response.status_code >= 400:
            return self._http_error(response, uri)
        try:
            payload = response.json()
        except ValueError as exc:
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=f"issues body is not valid JSON: {exc}",
                locator=uri,
            )

        children: list[CompositionNode] = []
        if isinstance(payload, list):
            for item in payload[:MAX_LIST_ITEMS]:
                if not isinstance(item, dict):
                    continue
                # The issues endpoint also returns PRs; skip them here.
                if "pull_request" in item:
                    continue
                children.append(self._build_issue_node(item, uri, []))
        return success(
            self._build_container("issues", route, uri, children)
        )

    async def _fetch_prs(
        self,
        client: httpx.AsyncClient,
        route: _Route,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch a repo's PRs as a ``pull_requests`` container node."""
        response = await client.get(
            self._repo_api(route, "/pulls"),
            params={"state": "open", "per_page": MAX_LIST_ITEMS},
            headers=headers,
        )
        if response.status_code >= 400:
            return self._http_error(response, uri)
        try:
            payload = response.json()
        except ValueError as exc:
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=f"PRs body is not valid JSON: {exc}",
                locator=uri,
            )

        children: list[CompositionNode] = []
        if isinstance(payload, list):
            for item in payload[:MAX_LIST_ITEMS]:
                if isinstance(item, dict):
                    children.append(self._build_pr_node(item, uri, []))
        return success(
            self._build_container("pull_requests", route, uri, children)
        )

    async def _fetch_releases(
        self,
        client: httpx.AsyncClient,
        route: _Route,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch a repo's releases as a ``releases`` container node."""
        response = await client.get(
            self._repo_api(route, "/releases"), headers=headers
        )
        if response.status_code >= 400:
            return self._http_error(response, uri)
        try:
            payload = response.json()
        except ValueError as exc:
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=f"releases body is not valid JSON: {exc}",
                locator=uri,
            )

        children: list[CompositionNode] = []
        if isinstance(payload, list):
            for item in payload[:MAX_RELEASES]:
                if isinstance(item, dict):
                    children.append(self._build_release_node(item, uri))
        return success(
            self._build_container("releases", route, uri, children)
        )

    @staticmethod
    def _build_container(
        kind: str,
        route: _Route,
        uri: str,
        children: list[CompositionNode],
    ) -> CompositionNode:
        """Assemble a list container node wrapping per-item child nodes."""
        return build_node(
            kind=kind,
            children=children,
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "repo": f"{route.owner}/{route.repo}",
                "item_count": len(children),
                "url": uri,
            },
        )

    @staticmethod
    def _http_error(response: httpx.Response, uri: str) -> Error:
        """Build a typed ``Error`` from a non-2xx GitHub response."""
        return error(
            kind=_status_to_error_kind(response),
            message=f"HTTP {response.status_code} for {uri}",
            locator=uri,
        )
