"""External-behaviour tests for the v1 ``notion`` connector (issue 003).

No real network: every ``httpx.AsyncClient`` is forced onto a
``MockTransport`` whose handler dispatches on the Notion API path. Only the
public surface is exercised via ``fetch()``:

- a page URI yields a ``"page"`` node whose block content lands in markdown
  ``Text`` atoms and whose descriptive fields live in
  ``source_extra["notion"]`` plus the uniform metadata core;
- a database URI yields a ``"database"`` container with one child page node
  per row;
- the per-call ``BearerAuth`` integration token (and the pinned
  ``Notion-Version``) reach the request headers;
- missing resources / bad tokens come back as typed ``Error`` values;
- a failed block-children fetch degrades to ``Partial`` with a typed gap
  instead of silently dropping content.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import BearerAuth
from omni_fetcher.v1.connectors.notion import NotionConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success

pytestmark = pytest.mark.asyncio

NotionHandler = Callable[[httpx.Request], httpx.Response]

PAGE_ID = "0123456789abcdef0123456789abcdef"
DB_ID = "abcdefabcdefabcdefabcdefabcdef12"
ROW_ID = "1111111111111111aaaaaaaaaaaaaaaa"

AUTH = BearerAuth(token="secret-integration-token")


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: NotionHandler,
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


def _page_payload(page_id: str, title: str) -> dict[str, Any]:
    """A minimal Notion page object."""
    return {
        "object": "page",
        "id": page_id,
        "url": f"https://www.notion.so/{title}-{page_id}",
        "created_time": "2026-01-15T09:00:00.000Z",
        "last_edited_time": "2026-02-01T12:00:00.000Z",
        "created_by": {"name": "Ada Lovelace"},
        "last_edited_by": {"name": "Grace Hopper"},
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": title}]},
            "Status": {"type": "select", "select": {"name": "In Progress"}},
        },
    }


def _blocks_payload(*texts: str) -> dict[str, Any]:
    """Paragraph blocks carrying the given plain texts."""
    return {
        "results": [
            {
                "type": "paragraph",
                "paragraph": {"rich_text": [{"plain_text": text}]},
            }
            for text in texts
        ]
    }


def _routes_handler(routes: dict[str, httpx.Response]) -> NotionHandler:
    """Dispatch by request path; unknown paths 404 like the real API."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in routes:
            return routes[path]
        return _json(404, {"object": "error", "code": "object_not_found", "message": "Not found"})

    return handler


# ---------------------------------------------------------------------------
# Pages


async def test_page_yields_page_node_with_text_atoms(monkeypatch) -> None:
    """A page URI maps onto a ``"page"`` node with markdown Text atoms."""
    routes = {
        f"/v1/pages/{PAGE_ID}": _json(200, _page_payload(PAGE_ID, "My Page")),
        f"/v1/blocks/{PAGE_ID}/children": _json(
            200, _blocks_payload("Hello from Notion", "Second paragraph")
        ),
    }
    _install_transport(monkeypatch, _routes_handler(routes))

    result = await NotionConnector().fetch(
        f"https://www.notion.so/My-Page-{PAGE_ID}", auth=AUTH
    )

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "page"
    texts = node.find_atoms(AtomKind.TEXT)
    assert [atom.format for atom in texts] == [TextFormat.MARKDOWN] * 2
    assert "Hello from Notion" in texts[0].content


async def test_page_descriptive_fields_in_source_extra_and_core(monkeypatch) -> None:
    """Descriptive fields live in ``source_extra["notion"]`` + the core."""
    routes = {
        f"/v1/pages/{PAGE_ID}": _json(200, _page_payload(PAGE_ID, "My Page")),
        f"/v1/blocks/{PAGE_ID}/children": _json(200, _blocks_payload("body")),
    }
    _install_transport(monkeypatch, _routes_handler(routes))

    result = await NotionConnector().fetch(f"notion://{PAGE_ID}", auth=AUTH)

    assert isinstance(result, Success)
    metadata = result.tree.metadata
    assert metadata.id == PAGE_ID
    assert metadata.author == "Ada Lovelace"
    assert metadata.created is not None and metadata.created.year == 2026

    extra = metadata.source_extra["notion"]
    assert extra["page_id"] == PAGE_ID
    assert extra["title"] == "My Page"
    assert extra["last_edited_by"] == "Grace Hopper"
    assert extra["properties"]["Status"] == "In Progress"


async def test_bearer_token_and_version_reach_the_request(monkeypatch) -> None:
    """The per-call token and pinned Notion-Version are sent on the wire."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization", "")
        seen["notion-version"] = request.headers.get("notion-version", "")
        if request.url.path == f"/v1/pages/{PAGE_ID}":
            return _json(200, _page_payload(PAGE_ID, "P"))
        return _json(200, _blocks_payload("x"))

    _install_transport(monkeypatch, handler)

    result = await NotionConnector().fetch(f"notion://{PAGE_ID}", auth=AUTH)

    assert isinstance(result, Success)
    assert seen["authorization"] == "Bearer secret-integration-token"
    assert seen["notion-version"], "Notion-Version header missing"


# ---------------------------------------------------------------------------
# Databases


async def test_database_yields_container_with_row_children(monkeypatch) -> None:
    """A database URI maps onto a container with one child per row."""
    db_payload = {
        "object": "database",
        "id": DB_ID,
        "url": f"https://www.notion.so/{DB_ID}",
        "created_time": "2026-01-01T00:00:00.000Z",
        "last_edited_time": "2026-01-02T00:00:00.000Z",
        "created_by": {"name": "Ada Lovelace"},
        "title": [{"plain_text": "Tracker"}],
        "properties": {"Name": {"type": "title"}, "Status": {"type": "select"}},
    }
    routes = {
        f"/v1/databases/{DB_ID}": _json(200, db_payload),
        f"/v1/databases/{DB_ID}/query": _json(
            200, {"results": [_page_payload(ROW_ID, "Row One")]}
        ),
        f"/v1/blocks/{ROW_ID}/children": _json(200, _blocks_payload("row body")),
    }
    _install_transport(monkeypatch, _routes_handler(routes))

    result = await NotionConnector().fetch(f"notion://database/{DB_ID}", auth=AUTH)

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "database"
    assert node.metadata.source_extra["notion"]["title"] == "Tracker"
    assert node.metadata.source_extra["notion"]["properties_schema"] == ["Name", "Status"]
    children = node.find_by_kind("page")
    assert len(children) == 1
    assert children[0].metadata.id == ROW_ID


# ---------------------------------------------------------------------------
# Typed failures


async def test_missing_page_is_not_found(monkeypatch) -> None:
    """A 404 comes back as ``Error(NOT_FOUND)``, never a raise."""
    _install_transport(monkeypatch, _routes_handler({}))

    result = await NotionConnector().fetch(f"notion://{PAGE_ID}", auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_bad_token_is_auth_failed(monkeypatch) -> None:
    """A 401 comes back as ``Error(AUTH_FAILED)``."""
    routes = {
        f"/v1/pages/{PAGE_ID}": _json(
            401, {"object": "error", "code": "unauthorized", "message": "Invalid token"}
        ),
    }
    _install_transport(monkeypatch, _routes_handler(routes))

    result = await NotionConnector().fetch(f"notion://{PAGE_ID}", auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_uri_without_notion_id_is_invalid_input(monkeypatch) -> None:
    """A Notion URI carrying no object id is a typed INVALID_INPUT."""
    _install_transport(monkeypatch, _routes_handler({}))

    result = await NotionConnector().fetch("notion://not-an-id", auth=AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_failed_block_fetch_degrades_to_partial(monkeypatch) -> None:
    """A 500 on block children yields Partial with a typed gap, not silence."""
    routes = {
        f"/v1/pages/{PAGE_ID}": _json(200, _page_payload(PAGE_ID, "My Page")),
        f"/v1/blocks/{PAGE_ID}/children": _json(
            500, {"object": "error", "message": "boom"}
        ),
    }
    _install_transport(monkeypatch, _routes_handler(routes))

    result = await NotionConnector().fetch(f"notion://{PAGE_ID}", auth=AUTH)

    assert isinstance(result, Partial)
    assert result.tree.metadata.source_extra["notion"]["title"] == "My Page"
    assert result.gaps and result.gaps[0].kind == ErrorKind.TRANSIENT
