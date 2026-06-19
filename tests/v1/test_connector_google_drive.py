"""External-behaviour tests for the v1 ``google_drive`` connector.

These tests exercise only the connector's public surface (``stream()`` /
inherited ``fetch()``) and never hit a real network: every test installs an
``httpx.MockTransport`` onto the connector's client via monkeypatch, so the
connector's real request/route/map logic runs against canned Google API
responses keyed by request path. No real Google call is ever made.

Asserted external behaviours:

- a single Google Doc yields a ``Success`` with a canonical ``kind`` ``"file"``
  node whose content lives in a ``Text`` atom and whose descriptive fields live
  in ``source_extra["google_drive"]``;
- a Google Sheet yields a file node carrying a ``Table`` atom;
- a folder yields a ``kind`` ``"folder"`` container whose children are
  ``kind`` ``"file"`` sub-trees;
- a ``403`` maps onto ``error(PERMISSION_DENIED)`` (and ``404`` -> ``NOT_FOUND``);
- the per-call ``OAuth2Auth`` token is the credential put on the wire (a
  ``Bearer`` header), never an ambient one;
- a call with no usable credential is ``error(AUTH_FAILED)`` without any
  request being made.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import OAuth2Auth
from omni_fetcher.v1.connectors.google_drive import (
    FILE_KIND,
    FOLDER_KIND,
    SOURCE_NAMESPACE,
    GoogleDriveFetcher,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Error, Partial, Success

_AUTH = OAuth2Auth(access_token="oauth-token-xyz")


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Force the connector's httpx client to use a ``MockTransport``."""
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _router(
    routes: dict[str, dict[str, Any]],
    *,
    recorder: dict[str, Any] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a handler dispatching on the first matching path substring.

    ``routes`` maps a path substring onto a JSON body; ``recorder`` (if given)
    captures every request's auth header so a test can assert the per-call
    token reached the wire.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.setdefault("auth_headers", []).append(
                request.headers.get("authorization")
            )
            recorder.setdefault("urls", []).append(str(request.url))
        path = request.url.path
        for needle, body in routes.items():
            if needle in path:
                return httpx.Response(200, json=body, request=request)
        return httpx.Response(404, json={"error": "no route"}, request=request)

    return handler


def _status_handler(
    status_code: int,
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a handler that always responds with a fixed status code."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": status_code}, request=request)

    return handler


def test_can_handle() -> None:
    assert GoogleDriveFetcher.can_handle("https://drive.google.com/file/d/ABC")
    assert GoogleDriveFetcher.can_handle("https://docs.google.com/document/d/ABC")
    assert not GoogleDriveFetcher.can_handle("s3://bucket/key")


async def test_document_is_canonical_success(monkeypatch) -> None:
    """A Google Doc yields a ``"file"`` node with a Text atom + drive metadata."""
    routes = {
        # First call: mimeType probe.
        "/drive/v3/files/DOC1": {
            "id": "DOC1",
            "name": "My Doc",
            "mimeType": "application/vnd.google-apps.document",
            "createdTime": "2026-01-01T00:00:00Z",
            "modifiedTime": "2026-01-02T00:00:00Z",
            "webViewLink": "https://docs.google.com/document/d/DOC1/view",
        },
        "/v1/documents/DOC1": {
            "title": "My Doc",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {"textRun": {"content": "Hello world.\n"}}
                            ]
                        }
                    }
                ]
            },
        },
    }
    _install_transport(monkeypatch, _router(routes))

    result = await GoogleDriveFetcher().fetch(
        "https://docs.google.com/document/d/DOC1/edit", auth=_AUTH
    )

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == FILE_KIND

    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind is AtomKind.TEXT
    assert atoms[0].content == "Hello world.\n"
    assert atoms[0].format is TextFormat.MARKDOWN

    extra = node.metadata.source_extra[SOURCE_NAMESPACE]
    assert extra["file_id"] == "DOC1"
    assert extra["name"] == "My Doc"
    assert extra["title"] == "My Doc"
    assert extra["mime_type"] == "application/vnd.google-apps.document"


async def test_spreadsheet_becomes_table(monkeypatch) -> None:
    """A Google Sheet yields a file node carrying a ``Table`` atom."""
    routes = {
        "/drive/v3/files/SHEET1": {
            "id": "SHEET1",
            "name": "Numbers",
            "mimeType": "application/vnd.google-apps.spreadsheet",
        },
        # Sheets metadata (no trailing /values segment).
        "/v4/spreadsheets/SHEET1/values/Tab1": {
            "values": [["a", "b"], ["1", "2"], ["3", "4"]],
        },
        "/v4/spreadsheets/SHEET1": {
            "properties": {"title": "Numbers"},
            "sheets": [{"properties": {"title": "Tab1"}}],
        },
    }
    _install_transport(monkeypatch, _router(routes))

    result = await GoogleDriveFetcher().fetch(
        "https://docs.google.com/spreadsheets/d/SHEET1/edit", auth=_AUTH
    )

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind is AtomKind.TABLE
    assert atoms[0].headers == ["a", "b"]
    assert atoms[0].rows == [["1", "2"], ["3", "4"]]

    extra = result.tree.metadata.source_extra[SOURCE_NAMESPACE]
    assert extra["sheet_names"] == ["Tab1"]
    assert extra["sheet_count"] == 1


async def test_folder_is_container_with_file_children(monkeypatch) -> None:
    """A folder yields a ``"folder"`` container whose children are files."""
    routes = {
        "/drive/v3/files/FOLDER1": {
            "id": "FOLDER1",
            "name": "My Folder",
            "mimeType": "application/vnd.google-apps.folder",
            "webViewLink": "https://drive.google.com/drive/folders/FOLDER1",
        },
        # Folder listing (the /files collection endpoint).
        "/drive/v3/files": {
            "files": [
                {
                    "id": "CHILD_A",
                    "name": "a.txt",
                    "mimeType": "text/plain",
                    "webViewLink": "https://drive.google.com/file/d/CHILD_A",
                },
                {
                    "id": "CHILD_B",
                    "name": "b.txt",
                    "mimeType": "text/plain",
                    "webViewLink": "https://drive.google.com/file/d/CHILD_B",
                },
            ],
        },
    }
    _install_transport(monkeypatch, _router(routes))

    result = await GoogleDriveFetcher().fetch(
        "https://drive.google.com/drive/folders/FOLDER1", auth=_AUTH
    )

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == FOLDER_KIND
    assert node.metadata.source_extra[SOURCE_NAMESPACE]["file_count"] == 2

    children = [c for c in node.children if isinstance(c, CompositionNode)]
    assert len(children) == 2
    assert all(child.metadata.kind == FILE_KIND for child in children)
    child_ids = {c.metadata.source_extra[SOURCE_NAMESPACE]["file_id"] for c in children}
    assert child_ids == {"CHILD_A", "CHILD_B"}


async def test_permission_denied_maps_to_error(monkeypatch) -> None:
    """A ``403`` from the Drive API maps onto ``error(PERMISSION_DENIED)``."""
    _install_transport(monkeypatch, _status_handler(403))

    result = await GoogleDriveFetcher().fetch(
        "https://drive.google.com/file/d/SECRET", auth=_AUTH
    )

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PERMISSION_DENIED
    assert result.locator == "https://drive.google.com/file/d/SECRET"


async def test_not_found_maps_to_error(monkeypatch) -> None:
    """A ``404`` from the Drive API maps onto ``error(NOT_FOUND)``."""
    _install_transport(monkeypatch, _status_handler(404))

    result = await GoogleDriveFetcher().fetch(
        "https://drive.google.com/file/d/GONE", auth=_AUTH
    )

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_per_call_oauth_token_is_used_not_ambient(monkeypatch) -> None:
    """The per-call OAuth2 token is the Bearer credential put on the wire."""
    recorder: dict[str, Any] = {}
    routes = {
        "/drive/v3/files/DOC1": {
            "id": "DOC1",
            "name": "Doc",
            "mimeType": "application/vnd.google-apps.document",
        },
        "/v1/documents/DOC1": {"title": "Doc", "body": {"content": []}},
    }
    _install_transport(monkeypatch, _router(routes, recorder=recorder))

    auth = OAuth2Auth(access_token="per-call-secret-123")
    result = await GoogleDriveFetcher().fetch(
        "https://docs.google.com/document/d/DOC1", auth=auth
    )

    assert isinstance(result, Success)
    headers = recorder["auth_headers"]
    assert headers, "expected at least one authenticated request"
    assert all(h == "Bearer per-call-secret-123" for h in headers)


async def test_missing_auth_is_auth_failed_without_request(monkeypatch) -> None:
    """A call with no credential is ``AUTH_FAILED`` and makes no request."""

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no HTTP request must be made without auth")

    monkeypatch.setattr(httpx, "AsyncClient", _explode)

    result = await GoogleDriveFetcher().fetch(
        "https://drive.google.com/file/d/ABC", auth=None
    )

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_unparseable_uri_is_invalid_input(monkeypatch) -> None:
    """A Drive URL with no extractable id is ``INVALID_INPUT``."""
    _install_transport(monkeypatch, _status_handler(200))

    result = await GoogleDriveFetcher().fetch(
        "https://drive.google.com/", auth=_AUTH
    )

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_opaque_binary_file_is_partial(monkeypatch) -> None:
    """A generic file with no canonical body is a ``Partial`` with a gap."""
    routes = {
        "/drive/v3/files/BIN1": {
            "id": "BIN1",
            "name": "archive.zip",
            "mimeType": "application/zip",
            "size": "1024",
        },
    }
    _install_transport(monkeypatch, _router(routes))

    result = await GoogleDriveFetcher().fetch(
        "https://drive.google.com/file/d/BIN1", auth=_AUTH
    )

    assert isinstance(result, Partial)
    assert result.tree.metadata.kind == FILE_KIND
    assert any(g.kind == ErrorKind.UNSUPPORTED for g in result.gaps)
    assert result.tree.metadata.source_extra[SOURCE_NAMESPACE]["size"] == 1024
