"""External-behaviour tests for the v1 Jira connector.

These tests exercise only the connector's public surface (``can_handle`` and
``stream`` / the inherited ``fetch``) against a fake in-memory Jira client --
no real network is touched. They assert the canonical contract:

- a Jira resource yields a canonical ``CompositionNode`` (no ``Jira*`` types)
  whose advisory ``kind`` is the semantic label of the resource;
- issue description and comments become ``Text`` atoms (content), while
  descriptive fields (status, assignee, reporter, priority, key, ...) live in
  ``source_extra["jira"]`` and the metadata core -- never on an atom;
- list resources (project / sprint / epic) become a container node with one
  child issue node each;
- HTTP failures map onto the typed error taxonomy (401 -> AUTH_FAILED, ...);
- credentials are resolved per call from the injected ``auth`` credential.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import AuthCredential, BasicAuth, BearerAuth
from omni_fetcher.v1.connectors.jira import (
    EPIC_KIND,
    ISSUE_KIND,
    JIRA_NAMESPACE,
    PROJECT_KIND,
    SPRINT_KIND,
    JiraConnector,
    parse_jira_uri,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Error, Success

BASE_URL = "https://acme.atlassian.net"
AUTH = BasicAuth(username="dev@acme.io", password="api-token")


# ---------------------------------------------------------------------------
# Fakes


class _FakeHTTPError(Exception):
    """A fake HTTP error carrying a status code, like the atlassian client."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _FakeClient:
    """An in-memory stand-in for the atlassian ``Jira`` client.

    Records the auth it was built with so tests can assert per-call auth, and
    serves canned issue/sprint/project payloads (or raises) without any
    network.
    """

    def __init__(
        self,
        *,
        issues: Optional[dict[str, Any]] = None,
        comments: Optional[dict[str, list[dict[str, Any]]]] = None,
        sprints: Optional[dict[int, Any]] = None,
        projects: Optional[dict[str, Any]] = None,
        jql_issues: Optional[list[dict[str, Any]]] = None,
        raises: Optional[Exception] = None,
        auth: Optional[AuthCredential] = None,
    ) -> None:
        self.url = BASE_URL
        self._issues = issues or {}
        self._comments = comments or {}
        self._sprints = sprints or {}
        self._projects = projects or {}
        self._jql_issues = jql_issues or []
        self._raises = raises
        self.auth = auth

    def issue(self, key: str) -> Any:
        if self._raises:
            raise self._raises
        return self._issues.get(key)

    def get_comments(self, key: str) -> dict[str, Any]:
        return {"comments": self._comments.get(key, [])}

    def sprint(self, sprint_id: int) -> Any:
        if self._raises:
            raise self._raises
        return self._sprints.get(sprint_id)

    def project(self, key: str) -> Any:
        if self._raises:
            raise self._raises
        return self._projects.get(key)

    def jql(self, jql: str, *, start: int, limit: int, fields: list[str]) -> Any:
        # Single page of canned issues.
        if start > 0:
            return {"issues": []}
        return {"issues": self._jql_issues}


def _connector_with(client: _FakeClient) -> JiraConnector:
    """A connector whose ``_get_client`` returns the supplied fake client.

    The fake records the ``auth`` argument so tests can assert that the
    per-call credential reached client construction.
    """
    connector = JiraConnector()

    def _get_client(auth: Optional[AuthCredential], base_url: str) -> _FakeClient:
        client.auth = auth
        return client

    connector._get_client = _get_client  # type: ignore[method-assign]
    return connector


def _issue(
    key: str = "PROJ-1",
    *,
    summary: str = "Fix the thing",
    description: Any = "A clear **description**.",
    issuetype: str = "Task",
) -> dict[str, Any]:
    """A canned raw Jira issue payload."""
    return {
        "id": "10001",
        "key": key,
        "fields": {
            "summary": summary,
            "description": description,
            "issuetype": {"name": issuetype},
            "status": {"name": "In Progress"},
            "priority": {"name": "High"},
            "assignee": {"displayName": "Ada Lovelace"},
            "reporter": {"displayName": "Bob Reporter"},
            "labels": ["backend"],
            "components": [{"name": "api"}],
            "fixVersions": [{"name": "v1.0"}],
            "created": "2026-01-01T10:00:00.000+0000",
            "updated": "2026-02-01T12:00:00.000+0000",
            "resolutiondate": None,
            "customfield_10016": 5,
        },
    }


# ---------------------------------------------------------------------------
# URI parsing & can_handle


def test_can_handle_jira_uris() -> None:
    """can_handle claims jira:// and atlassian URLs, rejects others."""
    assert JiraConnector.can_handle("jira://issue/PROJ-1")
    assert JiraConnector.can_handle(f"{BASE_URL}/browse/PROJ-1")
    assert JiraConnector.can_handle(f"{BASE_URL}/projects/PROJ")
    assert not JiraConnector.can_handle("https://example.com/api")


def test_parse_uri_routes() -> None:
    """parse_jira_uri decodes each scheme/URL form into a route."""
    assert parse_jira_uri("jira://issue/PROJ-1").issue_key == "PROJ-1"
    assert parse_jira_uri("jira://project/PROJ").project_key == "PROJ"
    assert parse_jira_uri("jira://sprint/42").sprint_id == 42
    assert parse_jira_uri("jira://epic/PROJ-9").epic_key == "PROJ-9"
    assert parse_jira_uri(f"{BASE_URL}/browse/PROJ-7").issue_key == "PROJ-7"


# ---------------------------------------------------------------------------
# Issue -> canonical node


async def test_issue_is_canonical_node() -> None:
    """An issue yields a Success carrying an 'issue'-kind canonical node."""
    client = _FakeClient(issues={"PROJ-1": _issue()})
    connector = _connector_with(client)

    result = await connector.fetch("jira://issue/PROJ-1", auth=AUTH)

    assert isinstance(result, Success)
    assert isinstance(result.tree, CompositionNode)
    assert result.tree.metadata.kind == ISSUE_KIND


async def test_issue_description_in_text_atom() -> None:
    """The issue description is content -> a markdown Text atom."""
    client = _FakeClient(issues={"PROJ-1": _issue()})
    connector = _connector_with(client)

    result = await connector.fetch("jira://issue/PROJ-1", auth=AUTH)

    assert isinstance(result, Success)
    texts = result.tree.find_atoms(AtomKind.TEXT)
    assert len(texts) == 1
    assert texts[0].format is TextFormat.MARKDOWN
    assert "A clear **description**." in texts[0].content


async def test_issue_comments_become_text_atoms() -> None:
    """Comments are content -> additional Text atoms in order."""
    client = _FakeClient(
        issues={"PROJ-1": _issue()},
        comments={
            "PROJ-1": [
                {"body": "First comment"},
                {"body": "Second comment"},
            ]
        },
    )
    connector = _connector_with(client)

    result = await connector.fetch("jira://issue/PROJ-1", auth=AUTH)

    assert isinstance(result, Success)
    texts = result.tree.find_atoms(AtomKind.TEXT)
    contents = [t.content for t in texts]
    assert "First comment" in contents
    assert "Second comment" in contents


async def test_descriptive_fields_in_source_extra_not_atoms() -> None:
    """Status/assignee/reporter/priority/key live in source_extra, not atoms."""
    client = _FakeClient(issues={"PROJ-1": _issue()})
    connector = _connector_with(client)

    result = await connector.fetch("jira://issue/PROJ-1", auth=AUTH)

    assert isinstance(result, Success)
    extra = result.tree.metadata.source_extra[JIRA_NAMESPACE]
    assert extra["status"] == "In Progress"
    assert extra["assignee"] == "Ada Lovelace"
    assert extra["reporter"] == "Bob Reporter"
    assert extra["priority"] == "High"
    assert extra["key"] == "PROJ-1"
    assert extra["story_points"] == 5.0
    assert extra["url"] == f"{BASE_URL}/browse/PROJ-1"

    # Metadata core is populated too.
    assert result.tree.metadata.id == "PROJ-1"
    assert result.tree.metadata.author == "Bob Reporter"
    assert result.tree.metadata.source_url == f"{BASE_URL}/browse/PROJ-1"
    assert result.tree.metadata.created is not None

    # Descriptive data is NOT inlined onto the content atom.
    atom = result.tree.find_atoms(AtomKind.TEXT)[0]
    assert set(atom.model_dump().keys()) == {
        "kind",
        "content",
        "format",
        "language",
        "encoding",
    }


async def test_adf_description_rendered_to_markdown() -> None:
    """An ADF (dict) description is rendered into markdown text."""
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Hello world"}],
            }
        ],
    }
    client = _FakeClient(issues={"PROJ-1": _issue(description=adf)})
    connector = _connector_with(client)

    result = await connector.fetch("jira://issue/PROJ-1", auth=AUTH)

    assert isinstance(result, Success)
    text = result.tree.find_atoms(AtomKind.TEXT)[0]
    assert "Hello world" in text.content


# ---------------------------------------------------------------------------
# List resources -> container node with child item nodes


async def test_project_is_container_node() -> None:
    """A project yields a 'project' node whose children are issue nodes."""
    client = _FakeClient(
        projects={
            "PROJ": {
                "id": "1",
                "name": "Project X",
                "projectTypeKey": "software",
                "lead": {"displayName": "Lead Person"},
                "description": "Project description text",
            }
        },
        jql_issues=[_issue("PROJ-1"), _issue("PROJ-2")],
    )
    connector = _connector_with(client)

    result = await connector.fetch("jira://project/PROJ", auth=AUTH)

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == PROJECT_KIND
    child_nodes = [c for c in result.tree.children if isinstance(c, CompositionNode)]
    assert len(child_nodes) == 2
    assert all(c.metadata.kind == ISSUE_KIND for c in child_nodes)
    extra = result.tree.metadata.source_extra[JIRA_NAMESPACE]
    assert extra["name"] == "Project X"
    assert extra["lead"] == "Lead Person"
    assert extra["issue_count"] == 2


async def test_sprint_is_container_node() -> None:
    """A sprint yields a 'sprint' node with child issue nodes and a goal atom."""
    client = _FakeClient(
        sprints={
            42: {
                "name": "Sprint 42",
                "state": "active",
                "goal": "Ship the feature",
                "boardId": 7,
                "startDate": "2026-01-01T00:00:00.000+0000",
                "issues": [_issue("PROJ-1")],
            }
        }
    )
    connector = _connector_with(client)

    result = await connector.fetch("jira://sprint/42", auth=AUTH)

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == SPRINT_KIND
    child_nodes = [c for c in result.tree.children if isinstance(c, CompositionNode)]
    assert len(child_nodes) == 1
    assert child_nodes[0].metadata.kind == ISSUE_KIND
    # Sprint goal is content -> a Text atom directly on the sprint node.
    goal_atoms = [c for c in result.tree.children if getattr(c, "kind", None) is AtomKind.TEXT]
    assert any("Ship the feature" in a.content for a in goal_atoms)
    assert result.tree.metadata.source_extra[JIRA_NAMESPACE]["state"] == "active"


async def test_epic_is_container_node() -> None:
    """An epic yields an 'epic' node with its linked issues as children."""
    client = _FakeClient(
        issues={"PROJ-9": _issue("PROJ-9", issuetype="Epic")},
        jql_issues=[_issue("PROJ-1"), _issue("PROJ-2")],
    )
    connector = _connector_with(client)

    result = await connector.fetch("jira://epic/PROJ-9", auth=AUTH)

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == EPIC_KIND
    child_nodes = [c for c in result.tree.children if isinstance(c, CompositionNode)]
    assert len(child_nodes) == 2


async def test_not_an_epic_is_invalid_input() -> None:
    """An epic URI pointing at a non-epic issue is an INVALID_INPUT error."""
    client = _FakeClient(issues={"PROJ-9": _issue("PROJ-9", issuetype="Task")})
    connector = _connector_with(client)

    result = await connector.fetch("jira://epic/PROJ-9", auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.INVALID_INPUT


# ---------------------------------------------------------------------------
# Errors are typed, never raised


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, ErrorKind.AUTH_FAILED),
        (403, ErrorKind.PERMISSION_DENIED),
        (404, ErrorKind.NOT_FOUND),
        (429, ErrorKind.RATE_LIMITED),
        (503, ErrorKind.TRANSIENT),
    ],
)
async def test_http_status_maps_to_error(status: int, expected: ErrorKind) -> None:
    """An HTTP failure from the client maps onto the typed taxonomy."""
    client = _FakeClient(raises=_FakeHTTPError(status))
    connector = _connector_with(client)

    result = await connector.fetch("jira://issue/PROJ-1", auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind is expected


async def test_401_is_auth_failed() -> None:
    """A 401 from the source is an AUTH_FAILED error, not a raise."""
    client = _FakeClient(raises=_FakeHTTPError(401))
    connector = _connector_with(client)

    result = await connector.fetch("jira://issue/PROJ-1", auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.AUTH_FAILED


async def test_missing_issue_is_not_found() -> None:
    """An absent issue (client returns falsy) is a NOT_FOUND error."""
    client = _FakeClient(issues={})
    connector = _connector_with(client)

    result = await connector.fetch("jira://issue/MISSING-1", auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND


async def test_invalid_uri_is_invalid_input() -> None:
    """An unroutable Jira URI is an INVALID_INPUT error."""
    client = _FakeClient()
    connector = _connector_with(client)

    result = await connector.fetch("jira://bogus/thing", auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.INVALID_INPUT


async def test_missing_auth_is_auth_failed() -> None:
    """No per-call credential yields an AUTH_FAILED error, never a raise.

    Uses a real ``_get_client`` (not the fake) so the missing-credential path
    is exercised. The optional ``atlassian`` dependency may be absent, in
    which case the connector reports UNSUPPORTED instead -- both are typed
    errors, never a raise, which is the contract being asserted.
    """
    connector = JiraConnector()

    result = await connector.fetch("jira://issue/PROJ-1", auth=None)

    assert isinstance(result, Error)
    assert result.kind in {ErrorKind.AUTH_FAILED, ErrorKind.UNSUPPORTED}


# ---------------------------------------------------------------------------
# Auth is resolved per call


async def test_per_call_auth_reaches_client() -> None:
    """The injected per-call credential is what builds the client."""
    client = _FakeClient(issues={"PROJ-1": _issue()})
    connector = _connector_with(client)

    cred = BearerAuth(token="secret-token")
    result = await connector.fetch("jira://issue/PROJ-1", auth=cred)

    assert isinstance(result, Success)
    assert client.auth is cred


async def test_distinct_auth_per_call() -> None:
    """A second call with a different credential uses that credential."""
    client = _FakeClient(issues={"PROJ-1": _issue()})
    connector = _connector_with(client)

    first = BasicAuth(username="a@x.io", password="t1")
    await connector.fetch("jira://issue/PROJ-1", auth=first)
    assert client.auth is first

    second = BearerAuth(token="t2")
    await connector.fetch("jira://issue/PROJ-1", auth=second)
    assert client.auth is second
