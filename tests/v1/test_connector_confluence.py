"""External-behaviour tests for the v1 ``confluence`` connector (issue 005).

No real network: the atlassian client is replaced either by a fake object
injected through the ``_build_client`` seam (route/mapping tests) or by a
recording stub patched over ``AtlassianConfluence`` (client-shape tests).
Only the public surface is exercised via ``fetch()``:

- a page URI yields a ``"page"`` node whose storage-format body is one HTML
  ``Text`` atom, with descriptive fields in ``source_extra["confluence"]``
  and the metadata core;
- a space URI yields a ``"space"`` container with page children;
- ``BasicAuth`` (Cloud) and ``BearerAuth`` (Server/DC) each construct the
  matching client shape against the URI's own host;
- anything else -- including no credential -- is a typed AUTH_FAILED;
- missing resources and client exceptions surface as typed errors.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import ApiKeyAuth, BasicAuth, BearerAuth
from omni_fetcher.v1.connectors import confluence as confl_module
from omni_fetcher.v1.connectors.confluence import ConfluenceConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

pytestmark = pytest.mark.asyncio

BASIC = BasicAuth(username="dev@acme.io", password="api-token")
BEARER = BearerAuth(token="pat-token")

PAGE_URI = "https://acme.atlassian.net/wiki/spaces/ENG/pages/12345/Design+Doc"
SPACE_URI = "https://acme.atlassian.net/wiki/spaces/ENG"


def _page_payload(page_id: int = 12345, title: str = "Design Doc") -> dict[str, Any]:
    """A representative Confluence page payload (storage body expanded)."""
    return {
        "id": page_id,
        "title": title,
        "status": "current",
        "space": {"key": "ENG"},
        "version": {
            "number": 4,
            "when": "2026-03-01T10:00:00.000Z",
            "by": {"displayName": "Ada Lovelace", "accountId": "acc-1"},
        },
        "ancestors": [{"id": 111}],
        "body": {"storage": {"value": "<p>Hello <b>world</b></p>"}},
        "_links": {
            "base": "https://acme.atlassian.net/wiki",
            "webui": f"/spaces/ENG/pages/{page_id}",
        },
    }


class _FakeConfluence:
    """A stand-in atlassian client with scripted responses."""

    def __init__(
        self,
        *,
        page: Optional[dict[str, Any]] = None,
        space: Optional[dict[str, Any]] = None,
        search: Optional[dict[str, Any]] = None,
        page_exc: Optional[Exception] = None,
    ) -> None:
        self._page = page
        self._space = space
        self._search = search
        self._page_exc = page_exc

    def get_page_by_id(self, page_id: str, expand: str = "") -> Any:
        if self._page_exc is not None:
            raise self._page_exc
        return self._page

    def get_space(self, space_key: str, expand: str = "") -> Any:
        return self._space

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        return self._search


def _connector_with(client: _FakeConfluence) -> ConfluenceConnector:
    """A connector whose ``_build_client`` returns the supplied fake."""
    connector = ConfluenceConnector()
    connector._build_client = lambda uri, auth: client  # type: ignore[method-assign]
    return connector


# ---------------------------------------------------------------------------
# Pages


async def test_page_yields_page_node_with_html_text_atom() -> None:
    """A page URI maps onto a ``"page"`` node with one HTML Text atom."""
    connector = _connector_with(_FakeConfluence(page=_page_payload()))

    result = await connector.fetch(PAGE_URI, auth=BASIC)

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "page"
    texts = node.find_atoms(AtomKind.TEXT)
    assert len(texts) == 1
    assert texts[0].format == TextFormat.HTML
    assert "Hello <b>world</b>" in texts[0].content


async def test_page_descriptive_fields_in_source_extra_and_core() -> None:
    """Descriptive fields live in ``source_extra["confluence"]`` + the core."""
    connector = _connector_with(_FakeConfluence(page=_page_payload()))

    result = await connector.fetch(PAGE_URI, auth=BASIC)

    assert isinstance(result, Success)
    metadata = result.tree.metadata
    assert metadata.id == "12345"
    assert metadata.author == "Ada Lovelace"
    assert metadata.updated is not None and metadata.updated.year == 2026
    assert metadata.source_url == (
        "https://acme.atlassian.net/wiki/spaces/ENG/pages/12345"
    )

    extra = metadata.source_extra["confluence"]
    assert extra["page_id"] == "12345"
    assert extra["title"] == "Design Doc"
    assert extra["space_key"] == "ENG"
    assert extra["version"] == 4
    assert extra["parent_id"] == "111"
    assert extra["author_display_name"] == "Ada Lovelace"


# ---------------------------------------------------------------------------
# Client shapes per credential


class _RecordingClient:
    """Records construction kwargs and serves a fixed page."""

    instances: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        _RecordingClient.instances.append(kwargs)

    def get_page_by_id(self, page_id: str, expand: str = "") -> Any:
        return _page_payload()


@pytest.fixture(autouse=False)
def recording_client(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingClient]:
    _RecordingClient.instances = []
    monkeypatch.setattr(confl_module, "AtlassianConfluence", _RecordingClient)
    return _RecordingClient


async def test_basic_auth_builds_cloud_client_shape(recording_client) -> None:
    """BasicAuth constructs a username/password client on the URI's host."""
    result = await ConfluenceConnector().fetch(PAGE_URI, auth=BASIC)

    assert isinstance(result, Success)
    (kwargs,) = recording_client.instances
    assert kwargs["url"] == "https://acme.atlassian.net"
    assert kwargs["username"] == "dev@acme.io"
    assert kwargs["password"] == "api-token"
    assert "token" not in kwargs


async def test_bearer_auth_builds_token_client_shape(recording_client) -> None:
    """BearerAuth constructs a token client (Server/DC personal token)."""
    result = await ConfluenceConnector().fetch(PAGE_URI, auth=BEARER)

    assert isinstance(result, Success)
    (kwargs,) = recording_client.instances
    assert kwargs["url"] == "https://acme.atlassian.net"
    assert kwargs["token"] == "pat-token"
    assert "username" not in kwargs


@pytest.mark.parametrize(
    "credential", [None, ApiKeyAuth(api_key="k", header="X-Key")]
)
async def test_unsupported_credentials_are_auth_failed(credential) -> None:
    """No credential -- or a non-Basic/Bearer one -- is a typed AUTH_FAILED."""
    connector = _connector_with(_FakeConfluence(page=_page_payload()))

    result = await connector.fetch(PAGE_URI, auth=credential)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


# ---------------------------------------------------------------------------
# Spaces


async def test_space_yields_container_with_page_children() -> None:
    """A space URI maps onto a ``"space"`` container of page nodes."""
    space_payload = {
        "id": 99,
        "key": "ENG",
        "name": "Engineering",
        "type": "global",
        "status": "current",
        "description": {"plain": {"value": "Team docs"}},
        "homepage": {"id": 12345},
    }
    search = {"results": [_page_payload(), _page_payload(67890, "Second")], "size": 2}
    connector = _connector_with(
        _FakeConfluence(space=space_payload, search=search)
    )

    result = await connector.fetch(SPACE_URI, auth=BASIC)

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "space"
    extra = node.metadata.source_extra["confluence"]
    assert extra["name"] == "Engineering"
    assert extra["page_count"] == 2

    children = node.find_by_kind("page")
    assert [child.metadata.id for child in children] == ["12345", "67890"]


# ---------------------------------------------------------------------------
# Typed failures


async def test_missing_page_is_not_found() -> None:
    """An empty client response is ``Error(NOT_FOUND)``, never a raise."""
    connector = _connector_with(_FakeConfluence(page=None))

    result = await connector.fetch(PAGE_URI, auth=BASIC)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_client_exception_is_classified_by_status() -> None:
    """A client error carrying a 404 response maps onto NOT_FOUND."""

    class _Response:
        status_code = 404

    exc = Exception("Not Found")
    exc.response = _Response()  # type: ignore[attr-defined]
    connector = _connector_with(_FakeConfluence(page_exc=exc))

    result = await connector.fetch(PAGE_URI, auth=BASIC)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_unrecognised_confluence_uri_is_invalid_input() -> None:
    """An Atlassian URI that routes nowhere is a typed INVALID_INPUT."""
    connector = _connector_with(_FakeConfluence(page=_page_payload()))

    result = await connector.fetch(
        "https://acme.atlassian.net/wiki/x/shortlink", auth=BASIC
    )

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT
