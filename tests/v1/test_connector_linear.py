"""External-behaviour tests for the v1 ``linear`` connector (issue 004).

No real network: every ``httpx.AsyncClient`` is forced onto a
``MockTransport`` standing in for Linear's GraphQL endpoint. Only the public
surface is exercised via ``fetch()``:

- issue / team routes (custom scheme and linear.app web URLs) map the
  GraphQL payload onto canonical nodes -- description and comments as Text
  atoms, descriptive fields in ``source_extra["linear"]`` + the metadata
  core;
- both documented credential shapes (``BearerAuth`` and ``ApiKeyAuth``)
  reach the request headers;
- GraphQL errors degrade honestly (errors-with-data -> ``Partial``,
  errors-without-data -> typed ``Error``); a null resource is ``NOT_FOUND``;
- HTTP statuses map onto the ErrorKind taxonomy (401, 429).
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import ApiKeyAuth, BearerAuth
from omni_fetcher.v1.connectors.linear import LinearConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success

pytestmark = pytest.mark.asyncio

LinearHandler = Callable[[httpx.Request], httpx.Response]

AUTH = BearerAuth(token="lin_api_secret")

ISSUE_URI = "linear://issue/ABC-1"


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: LinearHandler,
) -> None:
    """Force every ``httpx.AsyncClient`` to use a mock transport."""
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _json(status: int, payload: dict[str, Any]) -> httpx.Response:
    """A JSON response with the given status."""
    return httpx.Response(
        status_code=status,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
    )


def _fixed(response: httpx.Response) -> LinearHandler:
    """A handler returning one fixed response for every request."""

    def handler(request: httpx.Request) -> httpx.Response:
        return response

    return handler


def _issue_payload(**overrides: Any) -> dict[str, Any]:
    """A representative GraphQL issue object."""
    issue: dict[str, Any] = {
        "id": "a1b2c3d4-0000-0000-0000-000000000001",
        "identifier": "ABC-1",
        "title": "Fix the flux capacitor",
        "description": "It **must** flux.",
        "url": "https://linear.app/acme/issue/ABC-1",
        "priority": 2,
        "estimate": 3,
        "dueDate": "2026-08-01",
        "createdAt": "2026-01-15T09:00:00.000Z",
        "updatedAt": "2026-02-01T12:00:00.000Z",
        "state": {"name": "In Progress", "type": "started"},
        "team": {"id": "t1", "key": "ABC", "name": "Alpha"},
        "assignee": {"name": "Ada Lovelace"},
        "creator": {"name": "Bob Reporter"},
        "project": {"name": "Time Travel"},
        "cycle": {"name": "Cycle 7"},
        "labels": {"nodes": [{"name": "bug"}, {"name": "urgent"}]},
        "comments": {"nodes": [{"body": "first comment", "url": ""}]},
    }
    issue.update(overrides)
    return issue


# ---------------------------------------------------------------------------
# Issues


async def test_issue_yields_issue_node_with_content_atoms(monkeypatch) -> None:
    """Description and comments become Text atoms on an ``"issue"`` node."""
    _install_transport(monkeypatch, _fixed(_json(200, {"data": {"issue": _issue_payload()}})))

    result = await LinearConnector().fetch(ISSUE_URI, auth=AUTH)

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "issue"
    texts = node.find_atoms(AtomKind.TEXT)
    assert len(texts) == 2
    assert texts[0].content == "It **must** flux."
    assert texts[0].format == TextFormat.MARKDOWN
    assert texts[1].content == "first comment"


async def test_issue_descriptive_fields_in_source_extra_and_core(monkeypatch) -> None:
    """Descriptive fields land in ``source_extra["linear"]`` + the core."""
    _install_transport(monkeypatch, _fixed(_json(200, {"data": {"issue": _issue_payload()}})))

    result = await LinearConnector().fetch(ISSUE_URI, auth=AUTH)

    assert isinstance(result, Success)
    metadata = result.tree.metadata
    assert metadata.id == "a1b2c3d4-0000-0000-0000-000000000001"
    assert metadata.created is not None and metadata.created.year == 2026

    extra = metadata.source_extra["linear"]
    assert extra["identifier"] == "ABC-1"
    assert extra["title"] == "Fix the flux capacitor"
    assert extra["state"] == "In Progress"
    assert extra["priority_label"] == "High"
    assert extra["assignee"] == "Ada Lovelace"
    assert extra["team_key"] == "ABC"
    assert extra["labels"] == ["bug", "urgent"]
    assert extra["url"] == "https://linear.app/acme/issue/ABC-1"


async def test_web_url_routes_as_issue(monkeypatch) -> None:
    """A linear.app issue URL is recognised and fetched like the scheme URI."""
    _install_transport(monkeypatch, _fixed(_json(200, {"data": {"issue": _issue_payload()}})))

    result = await LinearConnector().fetch("https://linear.app/acme/issue/ABC-1", auth=AUTH)

    assert isinstance(result, Success)
    assert result.tree.metadata.source_extra["linear"]["identifier"] == "ABC-1"


# ---------------------------------------------------------------------------
# Credentials on the wire


@pytest.mark.parametrize(
    ("credential", "expected_header"),
    [
        (BearerAuth(token="lin_api_secret"), "Bearer lin_api_secret"),
        (ApiKeyAuth(api_key="lin_api_raw", header="Authorization"), "lin_api_raw"),
    ],
)
async def test_credential_shapes_reach_the_request(
    monkeypatch, credential, expected_header
) -> None:
    """BearerAuth and ApiKeyAuth both land in the Authorization header."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization", "")
        return _json(200, {"data": {"issue": _issue_payload()}})

    _install_transport(monkeypatch, handler)

    result = await LinearConnector().fetch(ISSUE_URI, auth=credential)

    assert isinstance(result, Success)
    assert seen["authorization"] == expected_header


# ---------------------------------------------------------------------------
# Teams


async def test_team_yields_container_with_issue_children(monkeypatch) -> None:
    """A team route maps onto a ``"team"`` container of issue rows."""
    team_payload = {
        "id": "t1",
        "key": "ENG",
        "name": "Engineering",
        "description": "Builds the thing.",
        "issues": {
            "nodes": [
                _issue_payload(id="i1", identifier="ENG-1", title="One"),
                _issue_payload(id="i2", identifier="ENG-2", title="Two"),
            ]
        },
    }
    _install_transport(monkeypatch, _fixed(_json(200, {"data": {"team": team_payload}})))

    result = await LinearConnector().fetch("linear://team/ENG", auth=AUTH)

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "team"
    children = node.find_by_kind("issue")
    assert len(children) == 2
    identifiers = [child.metadata.source_extra["linear"]["identifier"] for child in children]
    assert identifiers == ["ENG-1", "ENG-2"]


# ---------------------------------------------------------------------------
# Honest failures


async def test_null_resource_is_not_found(monkeypatch) -> None:
    """``data.issue = null`` comes back as ``Error(NOT_FOUND)``."""
    _install_transport(monkeypatch, _fixed(_json(200, {"data": {"issue": None}})))

    result = await LinearConnector().fetch(ISSUE_URI, auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_graphql_errors_without_data_are_typed_error(monkeypatch) -> None:
    """Errors with no data map onto a typed error, never a raise."""
    body = {"errors": [{"message": "Field 'issue' malformed"}]}
    _install_transport(monkeypatch, _fixed(_json(200, body)))

    result = await LinearConnector().fetch(ISSUE_URI, auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PARSE_ERROR


async def test_graphql_errors_with_data_degrade_to_partial(monkeypatch) -> None:
    """Errors alongside data yield ``Partial`` with one gap per error."""
    body = {
        "data": {"issue": _issue_payload()},
        "errors": [{"message": "comments truncated"}],
    }
    _install_transport(monkeypatch, _fixed(_json(200, body)))

    result = await LinearConnector().fetch(ISSUE_URI, auth=AUTH)

    assert isinstance(result, Partial)
    assert result.tree.metadata.source_extra["linear"]["identifier"] == "ABC-1"
    assert len(result.gaps) == 1


@pytest.mark.parametrize(
    ("status", "expected_kind"),
    [(401, ErrorKind.AUTH_FAILED), (429, ErrorKind.RATE_LIMITED)],
)
async def test_http_statuses_map_to_error_kinds(monkeypatch, status, expected_kind) -> None:
    """Auth and rate-limit HTTP statuses map onto the taxonomy."""
    _install_transport(monkeypatch, _fixed(_json(status, {})))

    result = await LinearConnector().fetch(ISSUE_URI, auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind == expected_kind


async def test_invalid_uri_is_invalid_input(monkeypatch) -> None:
    """A non-Linear URI is a typed INVALID_INPUT."""
    _install_transport(monkeypatch, _fixed(_json(200, {})))

    result = await LinearConnector().fetch("https://example.com/x", auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT
