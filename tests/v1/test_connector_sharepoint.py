"""External-behaviour tests for the v1 ``sharepoint`` connector.

These tests exercise only the public contract of ``SharePointConnector`` via
a fake ``httpx`` transport routed against the Microsoft Graph API -- no real
network access. They assert that:

- a site URI yields a ``"site"`` container whose children are ``"library"``
  container nodes, with descriptive fields under
  ``source_extra["sharepoint"]``;
- a library URI yields a ``"library"`` container whose children are
  ``"file"`` sub-trees;
- a file URI yields a single ``"file"`` node carrying text content in a
  content-only ``Text`` atom, with descriptive fields namespaced (never on
  the atom);
- Graph HTTP statuses map onto the canonical error taxonomy (401 ->
  AUTH_FAILED, 403 -> PERMISSION_DENIED, 404 -> NOT_FOUND, 429 ->
  RATE_LIMITED, 5xx -> TRANSIENT) and are returned, never raised;
- per-call OAuth2/Bearer credentials are resolved onto the outgoing
  Authorization header, and a missing/unusable credential is AUTH_FAILED;
- no ambient environment is consulted for credentials.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import ApiKeyAuth, BearerAuth, OAuth2Auth
from omni_fetcher.v1.connectors.sharepoint import (
    FILE_KIND,
    LIBRARY_KIND,
    SITE_KIND,
    SOURCE_NAMESPACE,
    SharePointConnector,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success

# ---------------------------------------------------------------------------
# Graph fixtures + a routing mock transport


_SITE = {
    "id": "site-123",
    "name": "team",
    "displayName": "Team Site",
    "description": "A team site",
    "webUrl": "https://contoso.sharepoint.com/sites/team",
}

_DRIVES = {
    "value": [
        {
            "id": "drive-docs",
            "name": "Documents",
            "driveType": "documentLibrary",
            "description": "Default library",
            "webUrl": "https://contoso.sharepoint.com/sites/team/Documents",
        },
        {
            "id": "drive-media",
            "name": "Media",
            "driveType": "documentLibrary",
            "webUrl": "https://contoso.sharepoint.com/sites/team/Media",
        },
    ]
}

_README_ITEM = {
    "id": "file-1",
    "name": "readme.txt",
    "size": 12,
    "file": {"mimeType": "text/plain"},
    "createdDateTime": "2026-01-01T00:00:00Z",
    "lastModifiedDateTime": "2026-02-02T00:00:00Z",
    "webUrl": "https://contoso.sharepoint.com/sites/team/Documents/readme.txt",
    "createdBy": {"user": {"displayName": "Ada"}},
    "lastModifiedBy": {"user": {"displayName": "Grace"}},
}

_BINARY_ITEM = {
    "id": "file-2",
    "name": "logo.png",
    "size": 999,
    "file": {"mimeType": "image/png"},
    "createdDateTime": "2026-01-01T00:00:00Z",
    "lastModifiedDateTime": "2026-02-02T00:00:00Z",
    "webUrl": "https://contoso.sharepoint.com/sites/team/Documents/logo.png",
}

_LIBRARY_CHILDREN = {
    "value": [
        _README_ITEM,
        {"id": "folder-1", "name": "sub", "folder": {"childCount": 0}},
    ]
}


def _json(payload: Any, status_code: int = 200) -> httpx.Response:
    """A JSON Graph response."""
    return httpx.Response(
        status_code,
        headers={"content-type": "application/json"},
        text=json.dumps(payload),
    )


def graph_transport(
    captured: list[httpx.Request],
    *,
    item: dict[str, Any] = _README_ITEM,
    children: dict[str, Any] = _LIBRARY_CHILDREN,
    file_body: bytes = b"hello world!",
    sites_status: int = 200,
    item_status: int = 200,
) -> httpx.MockTransport:
    """Build a MockTransport that routes Graph endpoints and records requests.

    Each handler returns canned fixtures so the connector's traversal can be
    exercised end to end without a real network. ``sites_status`` /
    ``item_status`` let a test force an error status on the site lookup or the
    file item fetch.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if path == "/v1.0/sites":
            if sites_status != 200:
                return _json({"error": "x"}, status_code=sites_status)
            return _json({"value": [_SITE]})
        if path == "/v1.0/sites/site-123/drives":
            return _json(_DRIVES)
        if path == "/v1.0/drives/drive-docs/root/children":
            return _json(children)
        if path.endswith("/content"):
            return httpx.Response(200, content=file_body)
        if "/root:/" in path:
            if item_status != 200:
                return _json({"error": "x"}, status_code=item_status)
            return _json(item)
        return _json({"error": "unexpected"}, status_code=404)

    return httpx.MockTransport(handler)


def _connector(transport: httpx.MockTransport) -> SharePointConnector:
    return SharePointConnector(transport=transport)


async def _one(connector: SharePointConnector, uri: str, **kwargs):
    """Drain the single-item stream and return its one result."""
    items = [item async for item in connector.stream(uri, **kwargs)]
    assert len(items) == 1
    return items[0]


_OAUTH = OAuth2Auth(access_token="graph-token")


# ---------------------------------------------------------------------------
# Site -> library container structure


async def test_site_yields_site_container_of_library_children() -> None:
    """A site URI builds a 'site' node whose children are 'library' nodes."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(
        connector, "sharepoint://team", auth=_OAUTH
    )

    assert isinstance(result, Success)
    root = result.tree
    assert root.metadata.kind == SITE_KIND
    libs = [c for c in root.children if c.metadata.kind == LIBRARY_KIND]
    assert len(libs) == 2
    assert {lib.metadata.source_extra[SOURCE_NAMESPACE]["name"] for lib in libs} == {
        "Documents",
        "Media",
    }


async def test_site_descriptive_fields_in_source_extra() -> None:
    """Site descriptive fields live namespaced under source_extra."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(connector, "sharepoint://team", auth=_OAUTH)

    assert isinstance(result, Success)
    extra = result.tree.metadata.source_extra[SOURCE_NAMESPACE]
    assert extra["site_id"] == "site-123"
    assert extra["display_name"] == "Team Site"


# ---------------------------------------------------------------------------
# Library -> file sub-tree structure


async def test_library_yields_library_container_of_file_children() -> None:
    """A library URI builds a 'library' node whose children are 'file' nodes."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(
        connector, "sharepoint://team/Documents", auth=_OAUTH
    )

    assert isinstance(result, Success)
    root = result.tree
    assert root.metadata.kind == LIBRARY_KIND
    files = [c for c in root.children if c.metadata.kind == FILE_KIND]
    # Only the file item is mapped; the folder entry is skipped.
    assert len(files) == 1
    assert files[0].metadata.source_extra[SOURCE_NAMESPACE]["name"] == "readme.txt"


# ---------------------------------------------------------------------------
# File node + canonical atom / metadata split


async def test_file_yields_file_node_with_text_atom() -> None:
    """A text file URI yields one 'file' node carrying a Text content atom."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured, file_body=b"file data"))

    result = await _one(
        connector, "sharepoint://team/Documents/readme.txt", auth=_OAUTH
    )

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == FILE_KIND
    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind is AtomKind.TEXT
    assert atoms[0].content == "file data"
    assert atoms[0].format is TextFormat.PLAIN


async def test_file_descriptive_fields_in_source_extra_not_on_atom() -> None:
    """File descriptive fields are namespaced; the atom is content-only."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(
        connector, "sharepoint://team/Documents/readme.txt", auth=_OAUTH
    )

    assert isinstance(result, Success)
    node = result.tree
    extra = node.metadata.source_extra[SOURCE_NAMESPACE]
    assert extra["file_id"] == "file-1"
    assert extra["mime_type"] == "text/plain"
    assert extra["size_bytes"] == 12
    assert node.metadata.author == "Ada"
    # The content atom carries no descriptive fields.
    atom = next(node.iter_atoms())
    assert set(atom.model_dump().keys()) == {
        "kind",
        "content",
        "format",
        "language",
        "encoding",
    }


async def test_binary_file_is_partial_with_unsupported_gap() -> None:
    """A binary file yields a Partial node + an UNSUPPORTED gap, no silent ok."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured, item=_BINARY_ITEM))

    result = await _one(
        connector, "sharepoint://team/Documents/logo.png", auth=_OAUTH
    )

    assert isinstance(result, Partial)
    assert result.tree.metadata.kind == FILE_KIND
    assert list(result.tree.iter_atoms()) == []
    assert len(result.gaps) == 1
    assert result.gaps[0].kind is ErrorKind.UNSUPPORTED


async def test_root_node_carries_temporal_sequence() -> None:
    """The single emitted root is stamped with sequence 0 + a timestamp."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(connector, "sharepoint://team", auth=_OAUTH)

    assert isinstance(result, Success)
    temporal = result.tree.metadata.temporal
    assert temporal.sequence == 0
    assert temporal.timestamp is not None


# ---------------------------------------------------------------------------
# Per-call auth


async def test_oauth_token_applied_to_graph_request() -> None:
    """A per-call OAuth2 token is resolved onto the outgoing Authorization."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    await _one(connector, "sharepoint://team", auth=_OAUTH)

    assert captured
    assert captured[0].headers["Authorization"] == "Bearer graph-token"


async def test_bearer_token_also_accepted() -> None:
    """A BearerAuth credential is equally usable for Graph access."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(
        connector, "sharepoint://team", auth=BearerAuth(token="bt")
    )

    assert isinstance(result, Success)
    assert captured[0].headers["Authorization"] == "Bearer bt"


async def test_different_calls_use_different_credentials() -> None:
    """Credentials are per call: each call carries only its own token."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    await _one(connector, "sharepoint://team", auth=OAuth2Auth(access_token="one"))
    await _one(connector, "sharepoint://team", auth=OAuth2Auth(access_token="two"))

    auth_headers = [
        r.headers["Authorization"]
        for r in captured
        if "Authorization" in r.headers
    ]
    assert "Bearer one" in auth_headers
    assert "Bearer two" in auth_headers


async def test_missing_credential_is_auth_failed() -> None:
    """No credential -> AUTH_FAILED, and no Graph request is attempted."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(connector, "sharepoint://team")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.AUTH_FAILED
    assert captured == []


async def test_wrong_credential_type_is_auth_failed() -> None:
    """A non-OAuth2/Bearer credential is rejected as AUTH_FAILED."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(
        connector, "sharepoint://team", auth=ApiKeyAuth(api_key="k")
    )

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.AUTH_FAILED
    assert captured == []


# ---------------------------------------------------------------------------
# Status -> error taxonomy


@pytest.mark.parametrize(
    ("status_code", "expected_kind"),
    [
        (401, ErrorKind.AUTH_FAILED),
        (403, ErrorKind.PERMISSION_DENIED),
        (404, ErrorKind.NOT_FOUND),
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.TRANSIENT),
        (503, ErrorKind.TRANSIENT),
    ],
)
async def test_graph_status_maps_to_error_kind(
    status_code: int,
    expected_kind: ErrorKind,
) -> None:
    """Each Graph HTTP error status maps to its canonical ErrorKind."""
    captured: list[httpx.Request] = []
    connector = _connector(
        graph_transport(captured, sites_status=status_code)
    )

    result = await _one(connector, "sharepoint://team", auth=_OAUTH)

    assert isinstance(result, Error)
    assert result.kind is expected_kind


async def test_missing_file_item_is_not_found() -> None:
    """A 404 on the file item lookup surfaces NOT_FOUND."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured, item_status=404))

    result = await _one(
        connector, "sharepoint://team/Documents/missing.txt", auth=_OAUTH
    )

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND


async def test_unknown_library_is_not_found() -> None:
    """Addressing a library that no drive matches yields NOT_FOUND."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(
        connector, "sharepoint://team/Nonexistent", auth=_OAUTH
    )

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND


# ---------------------------------------------------------------------------
# Invalid input + network failures


async def test_non_sharepoint_uri_is_invalid_input() -> None:
    """A URI the connector cannot handle is INVALID_INPUT, no request made."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(connector, "https://example.com/x", auth=_OAUTH)

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.INVALID_INPUT
    assert captured == []


async def test_network_error_is_transient() -> None:
    """A transport-level failure is returned as a TRANSIENT error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    connector = SharePointConnector(transport=httpx.MockTransport(handler))

    result = await _one(connector, "sharepoint://team", auth=_OAUTH)

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.TRANSIENT


# ---------------------------------------------------------------------------
# fetch() sugar + can_handle + ambient env


async def test_fetch_inherited_collects_single_result() -> None:
    """The inherited fetch() collects the single streamed Success as-is."""
    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await connector.fetch("sharepoint://team", auth=_OAUTH)

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == SITE_KIND


def test_can_handle() -> None:
    """can_handle accepts sharepoint URIs but not personal -my sites."""
    assert SharePointConnector.can_handle("sharepoint://team")
    assert SharePointConnector.can_handle(
        "https://contoso.sharepoint.com/sites/team"
    )
    assert not SharePointConnector.can_handle(
        "https://contoso-my.sharepoint.com/personal/x"
    )
    assert not SharePointConnector.can_handle("https://example.com")


async def test_no_ambient_env_is_read_for_credentials(monkeypatch) -> None:
    """Ambient Azure env vars are never consulted: no auth -> AUTH_FAILED."""
    for var in (
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
        "SHAREPOINT_TOKEN",
    ):
        monkeypatch.setenv(var, "ambient-should-not-be-used")

    captured: list[httpx.Request] = []
    connector = _connector(graph_transport(captured))

    result = await _one(connector, "sharepoint://team")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.AUTH_FAILED
    assert captured == []
