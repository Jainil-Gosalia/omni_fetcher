"""External-behaviour tests for the v1 ``http_json`` connector.

These tests exercise only the public surface of ``HTTPJSONConnector`` via
``fetch()`` (the inherited base sugar over ``stream()``). No real network is
used: an ``httpx.MockTransport`` is injected into the connector's client by
monkeypatching ``httpx.AsyncClient`` so every request is served by an
in-test handler.

What is asserted (behaviour, not internals):

- a 2xx JSON object yields a ``Success`` whose tree is a canonical node of
  advisory ``kind`` ``"json"`` carrying a ``Text`` atom, with HTTP status,
  content type, and url filed under ``source_extra["http_json"]``;
- a 2xx JSON array of flat records additionally carries a ``Table`` atom;
- a body that is not valid JSON yields ``Error(PARSE_ERROR)``;
- a 404 yields ``Error(NOT_FOUND)`` and other statuses map onto the taxonomy;
- a network failure yields ``Error(TRANSIENT)``.
"""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors.http_json import HTTPJSONConnector
from omni_fetcher.v1.errors import ErrorKind
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


def _json_handler(
    payload: object,
    *,
    status_code: int = 200,
    content_type: str = "application/json",
) -> Callable[[httpx.Request], httpx.Response]:
    """Return a handler serving ``payload`` as a JSON response."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            headers={"content-type": content_type},
            content=json.dumps(payload).encode("utf-8"),
        )

    return handler


async def test_json_object_yields_success_node(monkeypatch):
    """A JSON object yields a canonical ``"json"`` node with a Text atom."""
    payload = {"id": 7, "name": "widget", "nested": {"k": "v"}}
    _install_transport(monkeypatch, _json_handler(payload))

    result = await HTTPJSONConnector().fetch("https://example.com/api/item")

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "json"

    text_atoms = node.find_atoms(AtomKind.TEXT)
    assert len(text_atoms) == 1
    assert text_atoms[0].format == TextFormat.CODE
    assert json.loads(text_atoms[0].content) == payload

    extra = node.metadata.source_extra["http_json"]
    assert extra["status"] == 200
    assert extra["content_type"] == "application/json"
    assert extra["url"] == "https://example.com/api/item"
    assert node.metadata.source_url == "https://example.com/api/item"


async def test_descriptive_fields_not_on_atom(monkeypatch):
    """Descriptive fields live in metadata, never inline on the Text atom."""
    _install_transport(monkeypatch, _json_handler({"a": 1}))

    result = await HTTPJSONConnector().fetch("https://example.com/api/x")

    assert isinstance(result, Success)
    text_atom = result.tree.find_atoms(AtomKind.TEXT)[0]
    dumped = text_atom.model_dump()
    # The atom carries content only -- no status/url/content-type leakage.
    assert set(dumped) <= {"kind", "content", "format", "language", "encoding"}


async def test_json_array_of_records_yields_table(monkeypatch):
    """An array of flat records additionally yields a Table atom."""
    payload = [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b", "extra": "x"},
    ]
    _install_transport(monkeypatch, _json_handler(payload))

    result = await HTTPJSONConnector().fetch("https://example.com/api/list")

    assert isinstance(result, Success)
    node = result.tree
    tables = node.find_atoms(AtomKind.TABLE)
    assert len(tables) == 1
    table = tables[0]
    assert table.headers == ["id", "name", "extra"]
    assert table.rows == [[1, "a", None], [2, "b", "x"]]
    # The Text atom is still present alongside the Table.
    assert len(node.find_atoms(AtomKind.TEXT)) == 1


async def test_nested_array_has_no_table(monkeypatch):
    """An array with nested values is Text-only (not coerced to a Table)."""
    payload = [{"id": 1, "tags": ["x", "y"]}]
    _install_transport(monkeypatch, _json_handler(payload))

    result = await HTTPJSONConnector().fetch("https://example.com/api/list")

    assert isinstance(result, Success)
    assert result.tree.find_atoms(AtomKind.TABLE) == []
    assert len(result.tree.find_atoms(AtomKind.TEXT)) == 1


async def test_invalid_json_yields_parse_error(monkeypatch):
    """A non-JSON body yields a typed PARSE_ERROR, not a raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            content=b"this is not json {{{",
        )

    _install_transport(monkeypatch, handler)

    result = await HTTPJSONConnector().fetch("https://example.com/api/bad")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PARSE_ERROR


async def test_404_yields_not_found(monkeypatch):
    """A 404 response yields a typed NOT_FOUND error."""
    _install_transport(
        monkeypatch,
        _json_handler({"error": "missing"}, status_code=404),
    )

    result = await HTTPJSONConnector().fetch("https://example.com/api/gone")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (401, ErrorKind.AUTH_FAILED),
        (403, ErrorKind.PERMISSION_DENIED),
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.TRANSIENT),
        (503, ErrorKind.TRANSIENT),
        (422, ErrorKind.INVALID_INPUT),
    ],
)
async def test_status_maps_to_error_kind(monkeypatch, status_code, expected):
    """Non-404 error statuses map onto the taxonomy like ``http_url``."""
    _install_transport(
        monkeypatch,
        _json_handler({"x": 1}, status_code=status_code),
    )

    result = await HTTPJSONConnector().fetch("https://example.com/api/err")

    assert isinstance(result, Error)
    assert result.kind == expected


async def test_network_failure_yields_transient(monkeypatch):
    """A transport/network failure yields a typed TRANSIENT error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    _install_transport(monkeypatch, handler)

    result = await HTTPJSONConnector().fetch("https://example.com/api/down")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.TRANSIENT


async def test_auth_headers_sent(monkeypatch):
    """A bearer credential is resolved into the outgoing request headers."""
    from omni_fetcher.v1.auth import BearerAuth

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            content=b"{}",
        )

    _install_transport(monkeypatch, handler)

    result = await HTTPJSONConnector().fetch(
        "https://example.com/api/secure",
        auth=BearerAuth(token="s3cret"),
    )

    assert isinstance(result, Success)
    assert captured["authorization"] == "Bearer s3cret"
