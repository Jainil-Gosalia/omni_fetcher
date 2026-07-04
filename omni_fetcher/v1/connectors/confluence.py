"""The ``confluence`` connector for the OmniFetcher v1 contract.

Reads a Confluence page or space and maps it onto the canonical contract: a
``CompositionNode`` wrapped in a ``Result``. There are no ``Confluence*`` data
types -- the former ``ConfluencePage`` / ``ConfluenceSpace`` /
``ConfluenceAttachment`` / ``ConfluenceComment`` / ``ConfluenceUser`` shapes are
re-expressed as canonical atoms plus typed metadata:

- A **page** becomes a node of advisory ``kind`` ``"page"`` whose body (the
  Confluence ``body.storage`` HTML) is a single ``Text`` atom (format
  ``HTML``). Descriptive fields (author, version, space key, timestamps, url,
  parent id, icon) live in the metadata common core plus the namespaced
  ``source_extra["confluence"]`` mapping -- never inline on the atom.
- A **space** becomes a container node of advisory ``kind`` ``"space"`` whose
  children are the canonical page nodes it contains. The space's own
  descriptive fields (name, description, type, status, homepage id, counts,
  url) live in its metadata.

Authentication is supplied *per call* via the ``auth`` parameter -- a
``BasicAuth`` (Atlassian Cloud: account email + API token) or a ``BearerAuth``
(Server/Data-Center personal access token). The connector resolves the
credential transiently and constructs a fresh Confluence client from it; it
never reads ambient environment variables, never stores credentials on the
instance, and never mutates them. A call with no usable credential is an
``AUTH_FAILED`` error. The Confluence base URL (root) is derived from the URI's
host, preserving the v0.11 fetcher's root-URL support.

Expected failures (unknown URI, missing page/space, bad credentials, denied
access, throttling, server/network blips) are returned as typed ``Error`` /
``Partial`` values, never raised. Read-only and deterministic.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any, AsyncIterator, Optional, Union
from urllib.parse import urlparse

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import (
    AuthCredential,
    BasicAuth,
    BearerAuth,
    NormalizedAuthResolver,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Result,
    error,
    from_exception,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

try:
    from atlassian import Confluence as AtlassianConfluence

    ATLASSIAN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the optional dep
    AtlassianConfluence = None  # type: ignore[assignment, misc]
    ATLASSIAN_AVAILABLE = False

# Source namespace under which all descriptive ``confluence`` fields are filed
# in ``Metadata.source_extra``.
SOURCE_NAMESPACE = "confluence"

# Advisory semantic ``kind`` values for the nodes this connector emits.
PAGE_KIND = "page"
SPACE_KIND = "space"

# Default transport timeout (seconds) for the underlying Confluence client.
DEFAULT_TIMEOUT = 30.0

# Default Atlassian Cloud root used only when the URI carries no usable host.
_DEFAULT_ROOT_URL = "https://api.atlassian.com"

# Cap on the number of pages materialised under a space node, matching the
# v0.11 fetcher's bounded behaviour so a space fetch stays terminating.
_MAX_SPACE_PAGES = 10
_SPACE_PAGE_SEARCH_LIMIT = 25


def parse_confluence_uri(uri: str) -> dict[str, Any]:
    """Classify a Confluence URI into a resource route.

    Mirrors the v0.11 fetcher's routing so behaviour is unchanged: a URI
    resolves to a ``page`` (by numeric id), a ``space`` (by key, including a
    personal ``~`` space), a ``root`` (the wiki home), or ``unknown``.
    """
    parsed = urlparse(uri)
    path = parsed.path

    if not path or path == "/" or path.strip("/") in ("", "wiki"):
        return {"type": "root"}

    page_match = re.search(r"/pages/(\d+)", path)
    if page_match:
        return {"type": "page", "page_id": page_match.group(1)}

    personal_match = re.search(r"/spaces/(~[a-fA-F0-9]+)", path)
    if personal_match:
        return {"type": "space", "space_key": personal_match.group(1)}

    space_match = re.search(r"/spaces/([A-Z0-9]+)", path)
    if space_match:
        return {"type": "space", "space_key": space_match.group(1)}

    display_match = re.search(r"/display/([A-Z0-9]+)/?", path)
    if display_match:
        return {"type": "space", "space_key": display_match.group(1)}

    return {"type": "unknown"}


def _root_url_for(uri: str) -> str:
    """Derive the Confluence root URL from a URI's host (root-URL support).

    Preserves the v0.11 fetcher's behaviour of talking to the host named in the
    URI (e.g. ``https://acme.atlassian.net``) rather than a hard-coded cloud
    gateway, falling back to the Atlassian Cloud root only when the URI carries
    no host.
    """
    parsed = urlparse(uri)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return _DEFAULT_ROOT_URL


def _to_datetime(value: Any) -> Optional[datetime]:
    """Parse a Confluence ISO-8601 timestamp into an aware ``datetime``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _status_to_error_kind(status_code: int) -> ErrorKind:
    """Map an HTTP status code onto a taxonomy ``ErrorKind``."""
    if status_code == 401:
        return ErrorKind.AUTH_FAILED
    if status_code == 403:
        return ErrorKind.PERMISSION_DENIED
    if status_code == 404:
        return ErrorKind.NOT_FOUND
    if status_code == 429:
        return ErrorKind.RATE_LIMITED
    if 500 <= status_code <= 599:
        return ErrorKind.TRANSIENT
    return ErrorKind.TRANSIENT


def _classify_exception(exc: BaseException) -> ErrorKind:
    """Map a Confluence-client exception onto a taxonomy kind.

    The ``atlassian-python-api`` client raises ``requests``-style errors whose
    embedded HTTP status is the load-bearing signal. The status code is read
    from a ``response`` attribute when present and otherwise sniffed from the
    message, so a 404/401/403/429/5xx each surface as the right typed kind
    rather than an opaque transient failure.
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return _status_to_error_kind(status_code)

    message = str(exc)
    for code in (401, 403, 404, 429):
        if str(code) in message:
            return _status_to_error_kind(code)
    if "Not Found" in message or "does not exist" in message:
        return ErrorKind.NOT_FOUND
    if "Unauthorized" in message:
        return ErrorKind.AUTH_FAILED
    if "Forbidden" in message:
        return ErrorKind.PERMISSION_DENIED
    if re.search(r"\b5\d\d\b", message):
        return ErrorKind.TRANSIENT
    return ErrorKind.TRANSIENT


def _page_url(page_data: dict[str, Any], root_url: str) -> Optional[str]:
    """Build a fully-qualified page URL from the API ``_links`` block."""
    links = page_data.get("_links") or {}
    base = links.get("base")
    webui = links.get("webui")
    if webui:
        if base:
            return f"{base.rstrip('/')}{webui}"
        return f"{root_url.rstrip('/')}/wiki{webui}"
    return None


def _page_source_fields(page_data: dict[str, Any]) -> dict[str, Any]:
    """Assemble the namespaced descriptive fields for a page node.

    Every former ``ConfluencePage`` descriptive attribute (id, title, space
    key, version, parent, icon, author display name) is filed here, leaving the
    atom to carry body content only.
    """
    space = page_data.get("space") or {}
    version = page_data.get("version") or {}
    by = version.get("by") or {}
    ancestors = page_data.get("ancestors") or []
    icon = page_data.get("icon") or {}

    fields: dict[str, Any] = {
        "page_id": str(page_data.get("id", "")),
        "title": page_data.get("title", "Untitled"),
        "space_key": space.get("key"),
        "version": version.get("number"),
        "status": page_data.get("status"),
        "parent_id": str(ancestors[-1].get("id")) if ancestors else None,
        "icon": icon.get("emoji") if isinstance(icon, dict) else None,
        "author_account_id": by.get("accountId"),
        "author_display_name": by.get("displayName"),
        "author_email": by.get("email"),
    }
    return {key: value for key, value in fields.items() if value is not None}


def _space_source_fields(space_data: dict[str, Any], page_count: int) -> dict[str, Any]:
    """Assemble the namespaced descriptive fields for a space node."""
    description = None
    raw_description = space_data.get("description")
    if isinstance(raw_description, dict):
        description = (raw_description.get("plain") or {}).get("value")
    homepage = space_data.get("homepage") or {}

    fields: dict[str, Any] = {
        "space_key": space_data.get("key"),
        "name": space_data.get("name"),
        "description": description,
        "type": space_data.get("type"),
        "status": space_data.get("status"),
        "homepage_id": str(homepage.get("id")) if homepage.get("id") else None,
        "page_count": page_count,
    }
    return {key: value for key, value in fields.items() if value is not None}


class ConfluenceConnector(BaseFetcher):
    """
    Canonical v1 connector for Confluence pages and spaces
    ===============================================
    Reads a Confluence page or space and yields a single canonical
    ``CompositionNode`` wrapped in a ``Result``. A page is a ``kind`` ``"page"``
    node whose body is one ``Text`` atom (HTML); a space is a ``kind``
    ``"space"`` container node whose children are its page nodes. Descriptive
    fields (author, version, space, timestamps, url, permissions) live in the
    metadata core plus ``source_extra["confluence"]`` -- never on the atom.
    Read-only and deterministic.
    ===============================================
    NOTE:
        1. Credentials are supplied per call via ``auth`` (a ``BasicAuth`` for
           Atlassian Cloud or a ``BearerAuth`` for a personal access token) and
           used transiently to build a fresh client. This connector never reads
           ambient environment variables and stores nothing on the instance. A
           call with no usable credential is an ``AUTH_FAILED`` error.
        2. The Confluence root URL is derived from the URI's host, preserving
           the v0.11 fetcher's root-URL support.
        3. Expected failures are returned as typed ``Error`` / ``Partial``
           values (404 -> ``NOT_FOUND``, 401 -> ``AUTH_FAILED``, 403 ->
           ``PERMISSION_DENIED``, 429 -> ``RATE_LIMITED``, 5xx -> ``TRANSIENT``),
           never raised.

    Attributes
    ----------
        timeout:
            Per-request transport timeout in seconds for the client.

    Methods
    -------
        can_handle:
        stream:
    """

    name = SOURCE_NAMESPACE

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """
        Create a Confluence connector

        Parameters
        ----------
            timeout:
                Per-request transport timeout in seconds for the underlying
                Confluence client.
        """
        self.timeout = timeout
        self._auth_resolver = NormalizedAuthResolver()

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI names a Confluence resource

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for a Confluence/Atlassian wiki URL or a
                ``confluence://`` URI.
        """
        if not uri:
            return False
        lowered = uri.lower()
        return (
            "confluence" in lowered
            or "atlassian.net" in lowered
            or lowered.startswith("confluence://")
        )

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical node for a Confluence resource (the primitive)

        Routes ``uri`` to a page, a space, or the wiki root; builds a fresh
        Confluence client from the per-call ``auth``; fetches the resource with
        the v0.11 client logic; and yields exactly one ``Result``. A page
        yields a ``kind`` ``"page"`` node; a space (and the root) yields a
        ``kind`` ``"space"`` container node with page children.

        NOTE:
            1. ``auth`` must be a ``BasicAuth`` or ``BearerAuth``; ``None`` (or
               any other credential) yields an ``AUTH_FAILED`` error.
               Credentials are used transiently and never stored.
            2. Exactly one ``Result`` is yielded; expected failures are yielded
               as typed ``Error`` / ``Partial`` values, never raised.
            3. ``zoom`` is accepted for contract conformance; the natural
               granularity (one resource node) is emitted regardless.

        Parameters
        ----------
            uri:
                The Confluence page/space/root URI to read.
            auth:
                The per-call ``BasicAuth`` or ``BearerAuth`` credential.
            zoom:
                Optional per-atom-type zoom spec; accepted but not acted on.

        Return
        ------
            results:
                A bounded async iterator yielding one ``Result``.
        """
        yield await self._fetch_one(uri, auth)

    async def _fetch_one(self, uri: str, auth: Optional[AuthCredential]) -> Result:
        """Route, fetch, and map one Confluence resource to a ``Result``."""
        if not ATLASSIAN_AVAILABLE:
            return error(
                kind=ErrorKind.UNSUPPORTED,
                message=(
                    "atlassian-python-api is not installed; "
                    "install the 'confluence' extra to use this connector"
                ),
                locator=uri,
            )

        if not isinstance(auth, (BasicAuth, BearerAuth)):
            return error(
                kind=ErrorKind.AUTH_FAILED,
                message="confluence requires a per-call BasicAuth or BearerAuth credential",
                locator=uri,
            )

        route = parse_confluence_uri(uri)
        if route["type"] == "unknown":
            return error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"unrecognised Confluence URI: {uri}",
                locator=uri,
            )

        try:
            client = self._build_client(uri, auth)
        except Exception as exc:  # pragma: no cover - defensive client build
            return from_exception(exc, kind=ErrorKind.AUTH_FAILED, locator=uri)

        root_url = _root_url_for(uri)
        try:
            if route["type"] == "page":
                return await self._fetch_page(client, route["page_id"], uri, root_url)
            if route["type"] == "space":
                return await self._fetch_space(client, route["space_key"], uri, root_url)
            return await self._fetch_root(client, uri, root_url)
        except Exception as exc:
            return from_exception(exc, kind=_classify_exception(exc), locator=uri)

    def _build_client(self, uri: str, auth: Union[BasicAuth, BearerAuth]) -> Any:
        """Construct a fresh Confluence client from the per-call credential.

        Reuses the v0.11 fetcher's two client shapes -- username/password for
        Atlassian Cloud (``BasicAuth``) and a bearer token for Server/DC
        (``BearerAuth``) -- but sources every credential value from the
        per-call ``auth`` only, never the ambient environment.
        """
        url = _root_url_for(uri)
        if isinstance(auth, BasicAuth):
            return AtlassianConfluence(
                url=url,
                username=auth.username,
                password=auth.password,
                timeout=self.timeout,
            )
        return AtlassianConfluence(
            url=url,
            token=auth.token,
            timeout=self.timeout,
        )

    async def _fetch_page(self, client: Any, page_id: str, uri: str, root_url: str) -> Result:
        """Fetch one page and map it onto a canonical ``"page"`` node."""
        page_data = await asyncio.to_thread(
            client.get_page_by_id,
            page_id,
            expand="body.storage,version,space,ancestors",
        )
        if not page_data:
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"Confluence page not found: {page_id}",
                locator=uri,
            )
        return success(self._build_page_node(page_data, uri, root_url))

    def _build_page_node(
        self, page_data: dict[str, Any], uri: str, root_url: str
    ) -> CompositionNode:
        """Assemble the canonical ``"page"`` node for a fetched page."""
        body = (page_data.get("body") or {}).get("storage") or {}
        html = body.get("value", "") if isinstance(body, dict) else ""
        atoms = [Text(content=html, format=TextFormat.HTML, language="html")]

        version = page_data.get("version") or {}
        page_url = _page_url(page_data, root_url) or uri
        author = None
        by = version.get("by") or {}
        if isinstance(by, dict):
            author = by.get("displayName") or by.get("accountId")

        return build_node(
            kind=PAGE_KIND,
            atoms=atoms,
            id=str(page_data.get("id")) if page_data.get("id") else None,
            updated=_to_datetime(version.get("when")),
            author=author,
            source_url=page_url,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=_page_source_fields(page_data),
        )

    async def _fetch_space(self, client: Any, space_key: str, uri: str, root_url: str) -> Result:
        """Fetch a space and map it onto a ``"space"`` container node."""
        space_data = await asyncio.to_thread(
            client.get_space, space_key, expand="description.plain,homepage"
        )
        if not space_data:
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"Confluence space not found: {space_key}",
                locator=uri,
            )

        children: list[CompositionNode] = []
        gaps = []
        page_count = 0
        try:
            search = await asyncio.to_thread(
                client.get,
                "rest/api/content",
                params={
                    "cql": f"space={space_key} AND type=page",
                    "limit": _SPACE_PAGE_SEARCH_LIMIT,
                    "expand": "body.storage,version,space,ancestors",
                },
            )
        except Exception as exc:
            gaps.append(
                gap(
                    kind=_classify_exception(exc),
                    locator=uri,
                    detail=f"failed to list space pages: {exc}",
                )
            )
            search = None

        if search:
            results = search.get("results", []) or []
            page_count = search.get("size", len(results))
            for result in results[:_MAX_SPACE_PAGES]:
                node = self._space_child_node(result, uri, root_url)
                if node is not None:
                    children.append(node)

        space_node = build_node(
            kind=SPACE_KIND,
            children=children,
            id=str(space_data.get("id")) if space_data.get("id") else space_key,
            source_url=_page_url(space_data, root_url) or uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=_space_source_fields(space_data, page_count),
        )
        if gaps:
            return partial(space_node, gaps)
        return success(space_node)

    def _space_child_node(
        self, result: dict[str, Any], uri: str, root_url: str
    ) -> Optional[CompositionNode]:
        """Build a page child node from a space-search result entry."""
        if not isinstance(result, dict) or not result.get("id"):
            return None
        return self._build_page_node(result, uri, root_url)

    async def _fetch_root(self, client: Any, uri: str, root_url: str) -> Result:
        """Fetch the wiki root as the first accessible space.

        Preserves the v0.11 fetcher's root behaviour: resolve the caller's
        personal space, else fall back to the first accessible global space.
        """
        try:
            user = await asyncio.to_thread(client.get_myself)
            account_id = (user or {}).get("accountId", "")
        except Exception:
            account_id = ""

        if account_id:
            try:
                return await self._fetch_space(client, f"~{account_id}", uri, root_url)
            except Exception:
                pass

        spaces = await asyncio.to_thread(client.get, "rest/api/space", params={"limit": 10})
        results = (spaces or {}).get("results", []) or []
        if results and results[0].get("key"):
            return await self._fetch_space(client, results[0]["key"], uri, root_url)

        return error(
            kind=ErrorKind.NOT_FOUND,
            message="no accessible Confluence spaces for this credential",
            locator=uri,
        )
