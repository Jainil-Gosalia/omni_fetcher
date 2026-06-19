"""External-behaviour tests for the v1 GraphQL connector.

These tests exercise only the connector's public surface (``can_handle`` and
``stream`` / inherited ``fetch``) against a fake ``httpx`` transport -- no
real network is touched. They assert the canonical contract:

- clean GraphQL ``data`` yields a ``Success`` carrying a
  ``"graphql_response"`` node whose JSON content lives in a ``Text`` atom and
  whose descriptive fields live in ``source_extra["graphql"]``;
- a GraphQL ``errors`` array is never silently swallowed: errors with no
  data become an ``Error``; data alongside errors becomes a ``Partial`` with
  one gap per error;
- HTTP status failures map onto the typed error taxonomy.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import BearerAuth
from omni_fetcher.v1.connectors.graphql import (
    GRAPHQL_KIND,
    GRAPHQL_NAMESPACE,
    GraphQLConnector,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success

ENDPOINT = "https://api.example.com/graphql"


def _connector_with(handler) -> GraphQLConnector:
    """A connector whose POSTs are served by a fake httpx transport."""
    transport = httpx.MockTransport(handler)
    connector = GraphQLConnector()

    # Patch the connector's client factory to use the fake transport so no
    # real network call is ever made.
    original_post = connector._post

    async def _post(endpoint, payload, headers):
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.post(
                endpoint, json=payload, headers=headers
            )

    connector._post = _post  # type: ignore[method-assign]
    del original_post
    return connector


def _json_response(
    body: dict[str, Any], *, status: int = 200
) -> httpx.Response:
    """A fake JSON httpx response."""
    return httpx.Response(status, json=body)


def _uri(query: str = "{ ping }", **params: str) -> str:
    """Build a GraphQL endpoint URI with the operation on the query string."""
    merged = {"query": query, **params}
    return f"{ENDPOINT}?{urlencode(merged)}"


# ---------------------------------------------------------------------------
# can_handle


def test_can_handle_graphql_uris() -> None:
    """can_handle claims graphql/gql URIs and rejects others."""
    assert GraphQLConnector.can_handle("https://x/graphql")
    assert GraphQLConnector.can_handle("https://x/gql")
    assert not GraphQLConnector.can_handle("https://x/rest/api")


# ---------------------------------------------------------------------------
# Clean data -> Success


async def test_clean_data_is_success() -> None:
    """A response with data and no errors is a Success node."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"data": {"viewer": {"name": "ada"}}})

    connector = _connector_with(handler)

    result = await connector.fetch(_uri("{ viewer { name } }"))

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == GRAPHQL_KIND
    atoms = list(result.tree.iter_atoms())
    text = next(a for a in atoms if a.kind is AtomKind.TEXT)
    assert text.format is TextFormat.CODE
    assert json.loads(text.content) == {"viewer": {"name": "ada"}}


async def test_descriptive_fields_in_source_extra() -> None:
    """Status, query and variables live in source_extra, not on the atom."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"data": {"ok": True}})

    connector = _connector_with(handler)
    variables = json.dumps({"id": 7})

    result = await connector.fetch(
        _uri("query Q($id: Int) { node(id: $id) }", variables=variables)
    )

    assert isinstance(result, Success)
    extra = result.tree.metadata.source_extra[GRAPHQL_NAMESPACE]
    assert extra["status_code"] == 200
    assert "node(id: $id)" in extra["query"]
    assert extra["variables"] == {"id": 7}

    # Descriptive data is NOT inlined onto the content atom.
    atom = next(
        a for a in result.tree.iter_atoms() if a.kind is AtomKind.TEXT
    )
    assert set(atom.model_dump().keys()) == {
        "kind",
        "content",
        "format",
        "language",
        "encoding",
    }


async def test_list_data_yields_table_atom() -> None:
    """A clean list of flat records is offered as a Table atom too."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "data": {
                "users": [
                    {"id": 1, "name": "ada"},
                    {"id": 2, "name": "bob"},
                ]
            }
        }
        return _json_response(body)

    connector = _connector_with(handler)

    result = await connector.fetch(_uri("{ users { id name } }"))

    assert isinstance(result, Success)
    tables = result.tree.find_atoms(AtomKind.TABLE)
    assert len(tables) == 1
    assert tables[0].headers == ["id", "name"]
    assert tables[0].rows == [[1, "ada"], [2, "bob"]]


# ---------------------------------------------------------------------------
# GraphQL errors are never silently swallowed


async def test_errors_without_data_is_error() -> None:
    """GraphQL errors with no data map to an Error, not a silent success."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = {"errors": [{"message": "field 'x' does not exist"}]}
        return _json_response(body)  # HTTP 200 with errors

    connector = _connector_with(handler)

    result = await connector.fetch(_uri("{ x }"))

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.PARSE_ERROR
    assert "field 'x' does not exist" in (result.message or "")


async def test_data_with_errors_is_partial() -> None:
    """Data alongside GraphQL errors is a Partial with one gap per error."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "data": {"viewer": {"name": "ada"}},
            "errors": [
                {"message": "friends unavailable"},
                {"message": "rate window exceeded"},
            ],
        }
        return _json_response(body)

    connector = _connector_with(handler)

    result = await connector.fetch(_uri("{ viewer { name friends } }"))

    assert isinstance(result, Partial)
    # The partial data is preserved in the tree.
    text = next(
        a for a in result.tree.iter_atoms() if a.kind is AtomKind.TEXT
    )
    assert json.loads(text.content) == {"viewer": {"name": "ada"}}
    # Every GraphQL error is surfaced as a gap.
    assert len(result.gaps) == 2
    details = [g.detail for g in result.gaps]
    assert "friends unavailable" in details
    assert "rate window exceeded" in details
    assert all(g.kind is ErrorKind.PARSE_ERROR for g in result.gaps)


# ---------------------------------------------------------------------------
# HTTP status mapping


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
async def test_http_status_maps_to_error(
    status: int, expected: ErrorKind
) -> None:
    """Non-2xx HTTP statuses map onto the typed error taxonomy."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="nope")

    connector = _connector_with(handler)

    result = await connector.fetch(_uri())

    assert isinstance(result, Error)
    assert result.kind is expected


async def test_non_json_body_is_parse_error() -> None:
    """A 2xx response with a non-JSON body is a PARSE_ERROR."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    connector = _connector_with(handler)

    result = await connector.fetch(_uri())

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.PARSE_ERROR


async def test_invalid_variables_is_invalid_input() -> None:
    """Malformed 'variables' JSON on the URI is an INVALID_INPUT error."""
    connector = _connector_with(
        lambda request: _json_response({"data": {}})
    )

    uri = f"{ENDPOINT}?query={{ping}}&variables=not-json"
    result = await connector.fetch(uri)

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.INVALID_INPUT


# ---------------------------------------------------------------------------
# Auth is injected per call


async def test_auth_header_is_sent() -> None:
    """A per-call bearer credential is injected as an Authorization header."""
    seen: dict[str, Optional[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return _json_response({"data": {"ok": True}})

    connector = _connector_with(handler)

    result = await connector.fetch(
        _uri(), auth=BearerAuth(token="secret-token")
    )

    assert isinstance(result, Success)
    assert seen["authorization"] == "Bearer secret-token"
