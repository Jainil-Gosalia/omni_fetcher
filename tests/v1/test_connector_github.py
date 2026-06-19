"""External-behaviour tests for the v1 ``github`` connector.

These tests exercise only the public surface of ``GitHubConnector`` via
``fetch()`` (the inherited base sugar over ``stream()``). No real network is
used: an ``httpx.MockTransport`` is injected by monkeypatching
``httpx.AsyncClient`` so every request is served by an in-test handler that
routes on the request path.

What is asserted (behaviour, not internals):

- a single issue yields a canonical node of advisory ``kind`` ``"issue"``
  whose body/comments are ``Text`` atoms and whose descriptive fields (state,
  author, labels, timestamps, url, number) live in
  ``source_extra["github"]`` -- never inline on an atom;
- a file yields a ``"file"`` node, a PR a ``"pull_request"`` node, a release
  a ``"release"`` node, a repo a ``"repo"`` node;
- a list endpoint yields a container node whose children are per-item nodes;
- a 404 yields ``Error(NOT_FOUND)``, a 403 ``Error(PERMISSION_DENIED)`` (and
  ``RATE_LIMITED`` when a rate-limit header is present), other statuses map
  onto the taxonomy;
- a per-call bearer credential is resolved into the outgoing request headers;
- no ``GitHub*`` resource types appear anywhere in the output.
"""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.connectors.github import GitHubConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Error, Success

pytestmark = pytest.mark.asyncio


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Force every ``httpx.AsyncClient`` to use a mock transport."""
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _json_response(payload: object, status_code: int = 200) -> httpx.Response:
    """Build a JSON ``httpx.Response``."""
    return httpx.Response(
        status_code=status_code,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
    )


def _route_handler(
    routes: dict[str, object],
) -> Callable[[httpx.Request], httpx.Response]:
    """Serve JSON keyed by request path suffix; 404 when unmatched."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for suffix, payload in routes.items():
            if path.endswith(suffix):
                return _json_response(payload)
        return _json_response({"message": "Not Found"}, status_code=404)

    return handler


def _b64(text: str) -> str:
    """Base64-encode text the way the GitHub contents API returns it."""
    import base64

    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _assert_no_github_types(node: CompositionNode) -> None:
    """Assert the whole tree is canonical -- no legacy ``GitHub*`` types."""
    assert isinstance(node, CompositionNode)
    for descendant in node.iter_descendants():
        assert isinstance(descendant, CompositionNode)
    for atom in node.iter_atoms():
        assert type(atom).__name__ in {
            "Text",
            "Image",
            "Audio",
            "Video",
            "Table",
        }


async def test_single_issue_yields_canonical_issue_node(monkeypatch):
    """A single issue -> ``"issue"`` node with content atoms + metadata."""
    issue = {
        "number": 42,
        "title": "A bug",
        "body": "Something is broken.",
        "state": "open",
        "user": {"login": "octocat"},
        "labels": [{"name": "bug"}, {"name": "p1"}],
        "assignees": [{"login": "hubber"}],
        "comments": 1,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "html_url": "https://github.com/o/r/issues/42",
    }
    comments = [{"body": "I can repro."}]
    _install_transport(
        monkeypatch,
        _route_handler(
            {
                "/issues/42/comments": comments,
                "/issues/42": issue,
            }
        ),
    )

    result = await GitHubConnector().fetch(
        "https://github.com/o/r/issues/42"
    )

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "issue"
    _assert_no_github_types(node)

    # Content (body + comment) lives in Text atoms.
    text_atoms = node.find_atoms(AtomKind.TEXT)
    contents = [atom.content for atom in text_atoms]
    assert "Something is broken." in contents
    assert "I can repro." in contents

    # Descriptive fields live in source_extra["github"], not on atoms.
    extra = node.metadata.source_extra["github"]
    assert extra["number"] == 42
    assert extra["state"] == "open"
    assert extra["author"] == "octocat"
    assert extra["labels"] == ["bug", "p1"]
    assert extra["assignees"] == ["hubber"]
    assert extra["url"] == "https://github.com/o/r/issues/42"
    assert node.metadata.author == "octocat"
    assert node.metadata.source_url == "https://github.com/o/r/issues/42"


async def test_descriptive_fields_not_on_atoms(monkeypatch):
    """Issue atoms carry content only -- no state/author/url leakage."""
    issue = {
        "number": 1,
        "title": "t",
        "body": "the body",
        "state": "closed",
        "user": {"login": "me"},
        "labels": [],
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "html_url": "https://github.com/o/r/issues/1",
    }
    _install_transport(
        monkeypatch,
        _route_handler({"/issues/1/comments": [], "/issues/1": issue}),
    )

    result = await GitHubConnector().fetch("https://github.com/o/r/issues/1")

    assert isinstance(result, Success)
    atom = result.tree.find_atoms(AtomKind.TEXT)[0]
    dumped = atom.model_dump()
    assert set(dumped) <= {
        "kind",
        "content",
        "format",
        "language",
        "encoding",
    }


async def test_file_yields_file_node(monkeypatch):
    """A blob URL -> ``"file"`` node with decoded content in a Text atom."""
    file_payload = {
        "sha": "abc123",
        "size": 12,
        "content": _b64("print('hi')"),
        "html_url": "https://github.com/o/r/blob/main/app.py",
    }
    _install_transport(
        monkeypatch, _route_handler({"/contents/app.py": file_payload})
    )

    result = await GitHubConnector().fetch(
        "https://github.com/o/r/blob/main/app.py"
    )

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "file"
    _assert_no_github_types(node)
    text = node.find_atoms(AtomKind.TEXT)[0]
    assert text.content == "print('hi')"
    assert text.language == "python"
    extra = node.metadata.source_extra["github"]
    assert extra["path"] == "app.py"
    assert extra["sha"] == "abc123"


async def test_pull_request_yields_pr_node(monkeypatch):
    """A pull URL -> ``"pull_request"`` node; merged state reflected."""
    pr = {
        "number": 7,
        "title": "Add feature",
        "body": "PR body text",
        "state": "closed",
        "merged": True,
        "user": {"login": "dev"},
        "base": {"ref": "main"},
        "head": {"ref": "feature"},
        "labels": [{"name": "enhancement"}],
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "html_url": "https://github.com/o/r/pull/7",
    }
    _install_transport(
        monkeypatch,
        _route_handler({"/pulls/7/comments": [], "/pulls/7": pr}),
    )

    result = await GitHubConnector().fetch("https://github.com/o/r/pull/7")

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "pull_request"
    extra = node.metadata.source_extra["github"]
    assert extra["state"] == "merged"
    assert extra["base_branch"] == "main"
    assert extra["head_branch"] == "feature"
    assert "PR body text" in [
        a.content for a in node.find_atoms(AtomKind.TEXT)
    ]


async def test_repo_yields_repo_node_with_readme(monkeypatch):
    """A repo URL -> ``"repo"`` node; README is a markdown Text atom."""
    repo = {
        "full_name": "o/r",
        "description": "a repo",
        "default_branch": "main",
        "stargazers_count": 5,
        "forks_count": 2,
        "language": "Python",
        "topics": ["cli"],
        "html_url": "https://github.com/o/r",
    }
    readme = {"content": _b64("# Title")}
    _install_transport(
        monkeypatch,
        _route_handler({"/readme": readme, "/repos/o/r": repo}),
    )

    result = await GitHubConnector().fetch("https://github.com/o/r")

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "repo"
    extra = node.metadata.source_extra["github"]
    assert extra["stars"] == 5
    assert extra["language"] == "Python"
    assert "# Title" in [a.content for a in node.find_atoms(AtomKind.TEXT)]


async def test_issues_list_yields_container_with_child_nodes(monkeypatch):
    """A list endpoint -> container node whose children are per-item nodes."""
    issues = [
        {
            "number": 1,
            "title": "one",
            "body": "b1",
            "state": "open",
            "user": {"login": "a"},
            "labels": [],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "html_url": "https://github.com/o/r/issues/1",
        },
        {
            "number": 2,
            "title": "two",
            "body": "b2",
            "state": "open",
            "user": {"login": "b"},
            "labels": [],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "html_url": "https://github.com/o/r/issues/2",
        },
        # A PR masquerading in the issues feed -- must be filtered out.
        {
            "number": 3,
            "title": "pr",
            "pull_request": {"url": "x"},
            "state": "open",
            "user": {"login": "c"},
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "html_url": "https://github.com/o/r/pull/3",
        },
    ]
    _install_transport(
        monkeypatch, _route_handler({"/issues": issues})
    )

    result = await GitHubConnector().fetch("https://github.com/o/r/issues")

    assert isinstance(result, Success)
    container = result.tree
    assert container.metadata.kind == "issues"
    _assert_no_github_types(container)

    child_nodes = [
        c for c in container.children if isinstance(c, CompositionNode)
    ]
    assert len(child_nodes) == 2
    assert all(c.metadata.kind == "issue" for c in child_nodes)
    assert container.metadata.source_extra["github"]["item_count"] == 2


async def test_releases_list_yields_container(monkeypatch):
    """A releases list endpoint -> ``"releases"`` container of children."""
    releases = [
        {
            "tag_name": "v1.0",
            "name": "First",
            "body": "notes",
            "author": {"login": "rel"},
            "draft": False,
            "prerelease": False,
            "created_at": "2024-01-01T00:00:00Z",
            "html_url": "https://github.com/o/r/releases/tag/v1.0",
        }
    ]
    _install_transport(
        monkeypatch, _route_handler({"/releases": releases})
    )

    result = await GitHubConnector().fetch(
        "https://github.com/o/r/releases"
    )

    assert isinstance(result, Success)
    container = result.tree
    assert container.metadata.kind == "releases"
    children = [
        c for c in container.children if isinstance(c, CompositionNode)
    ]
    assert len(children) == 1
    assert children[0].metadata.kind == "release"
    assert children[0].metadata.source_extra["github"]["tag_name"] == "v1.0"


async def test_404_yields_not_found(monkeypatch):
    """A 404 response yields a typed NOT_FOUND error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"message": "Not Found"}, status_code=404)

    _install_transport(monkeypatch, handler)

    result = await GitHubConnector().fetch(
        "https://github.com/o/r/issues/999"
    )

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_403_without_rate_limit_yields_permission_denied(monkeypatch):
    """A plain 403 yields PERMISSION_DENIED."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=403,
            headers={"content-type": "application/json"},
            content=b'{"message": "Forbidden"}',
        )

    _install_transport(monkeypatch, handler)

    result = await GitHubConnector().fetch("https://github.com/o/r/issues/1")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PERMISSION_DENIED


async def test_403_with_rate_limit_header_yields_rate_limited(monkeypatch):
    """A 403 carrying an exhausted rate-limit header yields RATE_LIMITED."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=403,
            headers={
                "content-type": "application/json",
                "x-ratelimit-remaining": "0",
            },
            content=b'{"message": "rate limit exceeded"}',
        )

    _install_transport(monkeypatch, handler)

    result = await GitHubConnector().fetch("https://github.com/o/r/issues/1")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.RATE_LIMITED


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (401, ErrorKind.AUTH_FAILED),
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.TRANSIENT),
        (503, ErrorKind.TRANSIENT),
        (422, ErrorKind.INVALID_INPUT),
    ],
)
async def test_status_maps_to_error_kind(monkeypatch, status_code, expected):
    """Non-404 error statuses map onto the taxonomy."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"x": 1}, status_code=status_code)

    _install_transport(monkeypatch, handler)

    result = await GitHubConnector().fetch("https://github.com/o/r/issues/1")

    assert isinstance(result, Error)
    assert result.kind == expected


async def test_invalid_uri_yields_invalid_input(monkeypatch):
    """A URI that cannot be parsed yields INVALID_INPUT, not a raise."""
    _install_transport(monkeypatch, _route_handler({}))

    result = await GitHubConnector().fetch("https://github.com/onlyowner")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_network_failure_yields_transient(monkeypatch):
    """A transport/network failure yields a typed TRANSIENT error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    _install_transport(monkeypatch, handler)

    result = await GitHubConnector().fetch("https://github.com/o/r/issues/1")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.TRANSIENT


async def test_per_call_bearer_used(monkeypatch):
    """A per-call bearer credential is resolved into request headers."""
    from omni_fetcher.v1.auth import BearerAuth

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        if request.url.path.endswith("/comments"):
            return _json_response([])
        return _json_response(
            {
                "number": 1,
                "title": "t",
                "body": "b",
                "state": "open",
                "user": {"login": "u"},
                "labels": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "html_url": "https://github.com/o/r/issues/1",
            }
        )

    _install_transport(monkeypatch, handler)

    result = await GitHubConnector().fetch(
        "https://github.com/o/r/issues/1",
        auth=BearerAuth(token="s3cret"),
    )

    assert isinstance(result, Success)
    assert captured["authorization"] == "Bearer s3cret"


async def test_can_handle():
    """``can_handle`` claims github.com / api.github.com URLs only."""
    assert GitHubConnector.can_handle("https://github.com/o/r")
    assert GitHubConnector.can_handle("https://api.github.com/repos/o/r")
    assert not GitHubConnector.can_handle("https://example.com/o/r")
    assert not GitHubConnector.can_handle("")
