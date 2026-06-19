"""External-behaviour tests for the v1 ``http_url`` connector.

These tests exercise only the connector's public surface (``stream()`` /
inherited ``fetch()``) and never hit a real network: every test installs a
``httpx.MockTransport`` onto the connector's client via monkeypatch, so the
connector's real request/parse/map logic runs against a canned response.

Asserted external behaviours:

- a 2xx HTML response yields a ``Success`` whose node is a canonical
  ``"webpage"`` with a ``Text`` atom and the HTTP descriptive fields
  recorded in ``source_extra["http_url"]`` (not on the atom);
- a 404 maps to ``error(NOT_FOUND)``;
- other non-2xx statuses map onto their taxonomy kinds;
- a transport failure maps onto ``TRANSIENT``.
"""

from __future__ import annotations

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors.http_url import (
    SOURCE_NAMESPACE,
    WEBPAGE_KIND,
    HTTPURLConnector,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

_HTML = (
    "<html><head><title>Hello Page</title></head>"
    "<body><h1>Header</h1><p>Body text here.</p>"
    "<script>ignored()</script></body></html>"
)


def _install_transport(monkeypatch, handler) -> None:
    """Force the connector's httpx client to use a MockTransport.

    Patches ``httpx.AsyncClient`` so any construction inside the connector
    is given a ``MockTransport`` driving ``handler``; no socket is opened.
    """
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _html_handler(request: httpx.Request) -> httpx.Response:
    """Respond 200 with a small HTML page."""
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        text=_HTML,
        request=request,
    )


def _status_handler(status_code: int):
    """Build a handler that always responds with a fixed status code."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="nope", request=request)

    return handler


async def test_html_success_yields_canonical_webpage(monkeypatch) -> None:
    """A 2xx HTML response yields a canonical ``"webpage"`` Success."""
    _install_transport(monkeypatch, _html_handler)
    connector = HTTPURLConnector()

    result = await connector.fetch("https://example.com/page")

    assert isinstance(result, Success)
    node = result.tree
    # Semantic kind and source url on the metadata core.
    assert node.metadata.kind == WEBPAGE_KIND
    assert node.metadata.source_url == "https://example.com/page"

    # Content is carried as Text atoms (title + body), none of them HTML kind.
    atoms = list(node.iter_atoms())
    assert atoms, "expected at least one content atom"
    assert all(atom.kind is AtomKind.TEXT for atom in atoms)
    assert all(atom.format is TextFormat.PLAIN for atom in atoms)
    contents = [atom.content for atom in atoms]
    assert "Hello Page" in contents  # title atom
    body_text = "\n".join(contents)
    assert "Body text here." in body_text
    # Script content is stripped, never surfaced as content.
    assert "ignored()" not in body_text

    # HTTP descriptive fields live in source_extra, not on the atoms.
    extra = node.metadata.source_extra[SOURCE_NAMESPACE]
    assert extra["status_code"] == 200
    assert extra["mime_type"] == "text/html"
    assert extra["final_url"] == "https://example.com/page"
    assert "content-type" in extra["headers"]
    # Atoms carry content only -- no descriptive HTTP fields leak onto them.
    for atom in atoms:
        dumped = atom.model_dump()
        assert "status_code" not in dumped
        assert "headers" not in dumped


async def test_404_maps_to_not_found(monkeypatch) -> None:
    """A 404 response maps to a typed NOT_FOUND error (never raised)."""
    _install_transport(monkeypatch, _status_handler(404))
    connector = HTTPURLConnector()

    result = await connector.fetch("https://example.com/missing")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND
    assert result.locator == "https://example.com/missing"


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, ErrorKind.AUTH_FAILED),
        (403, ErrorKind.PERMISSION_DENIED),
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.TRANSIENT),
        (503, ErrorKind.TRANSIENT),
        (400, ErrorKind.INVALID_INPUT),
    ],
)
async def test_non_2xx_status_maps_to_taxonomy(monkeypatch, status, kind) -> None:
    """Each non-2xx status maps onto its expected taxonomy kind."""
    _install_transport(monkeypatch, _status_handler(status))
    connector = HTTPURLConnector()

    result = await connector.fetch("https://example.com/x")

    assert isinstance(result, Error)
    assert result.kind is kind


async def test_transport_error_maps_to_transient(monkeypatch) -> None:
    """A transport-level failure is returned as a TRANSIENT error."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_transport(monkeypatch, boom)
    connector = HTTPURLConnector()

    result = await connector.fetch("https://example.com/down")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.TRANSIENT


async def test_non_html_text_becomes_plain_text_atom(monkeypatch) -> None:
    """A non-HTML text response is carried as a single plain Text atom."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="just some text",
            request=request,
        )

    _install_transport(monkeypatch, handler)
    connector = HTTPURLConnector()

    result = await connector.fetch("https://example.com/plain.txt")

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].content == "just some text"
    assert atoms[0].format is TextFormat.PLAIN


async def test_stream_yields_single_item_with_temporal(monkeypatch) -> None:
    """stream() yields exactly one node carrying a temporal position."""
    _install_transport(monkeypatch, _html_handler)
    connector = HTTPURLConnector()

    items = []
    async for item in connector.stream("https://example.com/page"):
        items.append(item)

    assert len(items) == 1
    assert isinstance(items[0], Success)
    temporal = items[0].tree.metadata.temporal
    assert temporal.sequence == 0
    assert temporal.timestamp is not None
