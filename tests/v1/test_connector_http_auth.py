"""External-behaviour tests for the v1 ``http_auth`` connector.

These tests exercise only the public contract of ``HTTPAuthConnector`` via a
fake ``httpx`` transport -- no real network access. They assert that:

- per-call credentials are resolved into request headers and applied to the
  actual outgoing request (Bearer / API-key / Basic);
- a successful response becomes one canonical ``api_response`` node with the
  body in a content-only ``Text`` atom and descriptive fields namespaced
  under ``source_extra["http_auth"]``;
- HTTP status codes map onto the canonical error taxonomy (401 ->
  AUTH_FAILED, 403 -> PERMISSION_DENIED, 404 -> NOT_FOUND, 429 ->
  RATE_LIMITED, 5xx -> TRANSIENT) and are returned as ``Error`` values, never
  raised;
- a missing credential on a 401 is reported as AUTH_FAILED;
- a bad URI scheme is INVALID_INPUT;
- no ambient environment is consulted for credentials.
"""

from __future__ import annotations

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import ApiKeyAuth, BasicAuth, BearerAuth
from omni_fetcher.v1.connectors.http_auth import (
    API_RESPONSE_KIND,
    SOURCE_NAMESPACE,
    HTTPAuthConnector,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success


def _record_transport(
    captured: list[httpx.Request],
    *,
    status_code: int = 200,
    body: str = "ok",
    headers: dict[str, str] | None = None,
) -> httpx.MockTransport:
    """Build a MockTransport that records each request and replies fixedly."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            status_code,
            headers=headers or {"content-type": "text/plain"},
            text=body,
        )

    return httpx.MockTransport(handler)


async def _one(connector: HTTPAuthConnector, uri: str, **kwargs):
    """Drain the single-item stream and return its one result."""
    items = [item async for item in connector.stream(uri, **kwargs)]
    assert len(items) == 1
    return items[0]


# ---------------------------------------------------------------------------
# Auth headers are applied per call


async def test_bearer_token_applied_to_request() -> None:
    """A per-call BearerAuth is resolved onto the outgoing Authorization."""
    captured: list[httpx.Request] = []
    connector = HTTPAuthConnector(transport=_record_transport(captured))

    await _one(
        connector,
        "https://api.example.com/x",
        auth=BearerAuth(token="secret-abc"),
    )

    assert len(captured) == 1
    assert captured[0].headers["Authorization"] == "Bearer secret-abc"


async def test_api_key_applied_with_custom_header() -> None:
    """An ApiKeyAuth is carried in its (custom) header on the request."""
    captured: list[httpx.Request] = []
    connector = HTTPAuthConnector(transport=_record_transport(captured))

    await _one(
        connector,
        "https://api.example.com/x",
        auth=ApiKeyAuth(api_key="k-99", header="X-Custom-Key"),
    )

    assert captured[0].headers["X-Custom-Key"] == "k-99"


async def test_basic_auth_applied_as_base64() -> None:
    """A BasicAuth is base64-encoded into the Authorization header."""
    captured: list[httpx.Request] = []
    connector = HTTPAuthConnector(transport=_record_transport(captured))

    await _one(
        connector,
        "https://api.example.com/x",
        auth=BasicAuth(username="alice", password="s3cret"),
    )

    auth_header = captured[0].headers["Authorization"]
    assert auth_header.startswith("Basic ")


async def test_no_auth_sends_no_authorization_header() -> None:
    """Without a credential, no Authorization header is added."""
    captured: list[httpx.Request] = []
    connector = HTTPAuthConnector(transport=_record_transport(captured))

    await _one(connector, "https://api.example.com/x")

    assert "Authorization" not in captured[0].headers


async def test_different_calls_use_different_credentials() -> None:
    """Credentials are per call: a second call uses its own token only."""
    captured: list[httpx.Request] = []
    connector = HTTPAuthConnector(transport=_record_transport(captured))

    await _one(connector, "https://a.example.com", auth=BearerAuth(token="one"))
    await _one(connector, "https://b.example.com", auth=BearerAuth(token="two"))

    assert captured[0].headers["Authorization"] == "Bearer one"
    assert captured[1].headers["Authorization"] == "Bearer two"


# ---------------------------------------------------------------------------
# Successful response maps to a canonical node


async def test_success_builds_api_response_node() -> None:
    """A 2xx yields a Success with an api_response node and a Text atom."""
    captured: list[httpx.Request] = []
    transport = _record_transport(
        captured,
        status_code=200,
        body="hello body",
        headers={"content-type": "text/plain; charset=utf-8"},
    )
    connector = HTTPAuthConnector(transport=transport)

    result = await _one(
        connector,
        "https://api.example.com/x",
        auth=BearerAuth(token="t"),
    )

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == API_RESPONSE_KIND
    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind is AtomKind.TEXT
    assert atoms[0].content == "hello body"


async def test_success_puts_descriptive_fields_in_source_extra() -> None:
    """Status/headers/urls live namespaced in source_extra, not on atoms."""
    captured: list[httpx.Request] = []
    transport = _record_transport(
        captured,
        status_code=200,
        body="{}",
        headers={"content-type": "application/json"},
    )
    connector = HTTPAuthConnector(transport=transport)

    result = await _one(connector, "https://api.example.com/x")

    assert isinstance(result, Success)
    extra = result.tree.metadata.source_extra[SOURCE_NAMESPACE]
    assert extra["status_code"] == 200
    assert extra["mime_type"] == "application/json"
    assert extra["requested_url"] == "https://api.example.com/x"
    assert "response_headers" in extra
    # Descriptive fields are NOT inlined on the content atom.
    atom = next(result.tree.iter_atoms())
    assert set(atom.model_dump().keys()) == {
        "kind",
        "content",
        "format",
        "language",
        "encoding",
    }


async def test_html_response_gets_html_text_format() -> None:
    """An HTML body is emitted as an HTML-format Text atom."""
    captured: list[httpx.Request] = []
    transport = _record_transport(
        captured,
        body="<html><title>T</title></html>",
        headers={"content-type": "text/html"},
    )
    connector = HTTPAuthConnector(transport=transport)

    result = await _one(connector, "https://api.example.com/x")

    assert isinstance(result, Success)
    atom = next(result.tree.iter_atoms())
    assert atom.format is TextFormat.HTML


async def test_success_node_carries_temporal_sequence() -> None:
    """The single emitted node is stamped with sequence 0 + a timestamp."""
    captured: list[httpx.Request] = []
    connector = HTTPAuthConnector(transport=_record_transport(captured))

    result = await _one(connector, "https://api.example.com/x")

    assert isinstance(result, Success)
    temporal = result.tree.metadata.temporal
    assert temporal.sequence == 0
    assert temporal.timestamp is not None


# ---------------------------------------------------------------------------
# HTTP status codes map onto the canonical error taxonomy


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
async def test_status_maps_to_error_kind(
    status_code: int,
    expected_kind: ErrorKind,
) -> None:
    """Each HTTP error status maps to its canonical ErrorKind (no raise)."""
    captured: list[httpx.Request] = []
    transport = _record_transport(captured, status_code=status_code)
    connector = HTTPAuthConnector(transport=transport)

    result = await _one(
        connector,
        "https://api.example.com/x",
        auth=BearerAuth(token="t"),
    )

    assert isinstance(result, Error)
    assert result.kind is expected_kind


async def test_401_returns_auth_failed() -> None:
    """A 401 with a (rejected) credential surfaces AUTH_FAILED."""
    captured: list[httpx.Request] = []
    transport = _record_transport(captured, status_code=401)
    connector = HTTPAuthConnector(transport=transport)

    result = await _one(
        connector,
        "https://api.example.com/x",
        auth=BearerAuth(token="bad"),
    )

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.AUTH_FAILED


async def test_403_returns_permission_denied() -> None:
    """A 403 surfaces PERMISSION_DENIED."""
    captured: list[httpx.Request] = []
    transport = _record_transport(captured, status_code=403)
    connector = HTTPAuthConnector(transport=transport)

    result = await _one(
        connector,
        "https://api.example.com/x",
        auth=BearerAuth(token="t"),
    )

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.PERMISSION_DENIED


async def test_missing_credential_on_401_is_auth_failed() -> None:
    """A 401 with no supplied credential is reported as AUTH_FAILED."""
    captured: list[httpx.Request] = []
    transport = _record_transport(captured, status_code=401)
    connector = HTTPAuthConnector(transport=transport)

    result = await _one(connector, "https://api.example.com/x")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.AUTH_FAILED
    assert "no credential" in (result.message or "")


# ---------------------------------------------------------------------------
# Invalid input and transport failures


async def test_non_http_uri_is_invalid_input() -> None:
    """A non-http(s) URI is rejected as INVALID_INPUT, not fetched."""
    captured: list[httpx.Request] = []
    connector = HTTPAuthConnector(transport=_record_transport(captured))

    result = await _one(connector, "ftp://example.com/x")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.INVALID_INPUT
    # No request was attempted.
    assert captured == []


async def test_network_error_is_transient() -> None:
    """A transport-level failure is returned as a TRANSIENT error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    connector = HTTPAuthConnector(transport=httpx.MockTransport(handler))

    result = await _one(connector, "https://api.example.com/x")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.TRANSIENT


async def test_timeout_is_transient() -> None:
    """A request timeout is returned as a TRANSIENT error."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    connector = HTTPAuthConnector(transport=httpx.MockTransport(handler))

    result = await _one(connector, "https://api.example.com/x")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.TRANSIENT


# ---------------------------------------------------------------------------
# No ambient environment is read for credentials


async def test_no_ambient_env_is_read_for_credentials(monkeypatch) -> None:
    """Ambient env vars are never consulted: no auth -> no Authorization."""
    # Set every plausible ambient credential variable; none must leak in.
    for var in (
        "HTTP_AUTH_TOKEN",
        "BEARER_TOKEN",
        "API_KEY",
        "AUTHORIZATION",
        "OMNI_FETCHER_TOKEN",
    ):
        monkeypatch.setenv(var, "ambient-should-not-be-used")

    captured: list[httpx.Request] = []
    connector = HTTPAuthConnector(transport=_record_transport(captured))

    await _one(connector, "https://api.example.com/x")

    request = captured[0]
    assert "Authorization" not in request.headers
    assert "X-API-Key" not in request.headers
    # And no ambient secret value appears on any header.
    for value in request.headers.values():
        assert "ambient-should-not-be-used" not in value


async def test_fetch_inherited_collects_single_result() -> None:
    """The inherited fetch() collects the single streamed Success as-is."""
    captured: list[httpx.Request] = []
    connector = HTTPAuthConnector(transport=_record_transport(captured))

    result = await connector.fetch(
        "https://api.example.com/x",
        auth=BearerAuth(token="t"),
    )

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == API_RESPONSE_KIND
