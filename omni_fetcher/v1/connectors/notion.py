"""The canonical ``notion`` connector for the OmniFetcher v1 contract.

Fetches a Notion page or database via the Notion REST API and emits it as a
canonical ``CompositionNode`` tree wrapped in a ``Result``.

A *page* becomes a node of advisory ``kind`` ``"page"`` whose block content is
expressed as content atoms in document order -- ``Text`` atoms for textual
blocks (rendered to markdown), ``Image`` atoms for image blocks, and ``Table``
atoms for table blocks. A *database* becomes a container node of advisory
``kind`` ``"database"`` whose children are the canonical page nodes of its rows.

Everything that *describes* an artifact -- created/last-edited timestamps,
created_by / last_edited_by, the page properties, icon, cover, and canonical
url -- is descriptive metadata. It lives in the ``Metadata`` core (id, created,
updated, author, source_url) plus the namespaced ``source_extra["notion"]``
mapping, never inline on an atom.

Failures are returned as typed ``Result`` values, never raised. Critically,
when a page's block content or a database row cannot be fetched, the connector
returns ``partial(tree, [gap(...)])`` capturing exactly what failed -- it never
returns a ``success`` with silently-missing content (the v0.11 anti-pattern).
HTTP/Notion errors map onto the taxonomy: 404 -> ``NOT_FOUND``, 401 ->
``AUTH_FAILED``, the Notion ``restricted_resource`` code (403) ->
``PERMISSION_DENIED``, 429 / ``rate_limited`` -> ``RATE_LIMITED``, 5xx/network
-> ``TRANSIENT``, other 4xx -> ``INVALID_INPUT``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from omni_fetcher.v1.atoms import AnyAtom, Image, Table, Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential, NormalizedAuthResolver
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Gap,
    Result,
    error,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# The source namespace under which this connector files descriptive fields.
SOURCE_NAMESPACE = "notion"

# Advisory semantic labels for the nodes this connector emits.
PAGE_KIND = "page"
DATABASE_KIND = "database"

# Notion REST API.
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Default transport timeout (seconds) for a single request.
DEFAULT_TIMEOUT = 30.0

# Database query page size cap (Notion maximum is 100).
QUERY_PAGE_SIZE = 100


def _status_to_error_kind(status_code: int, code: Optional[str] = None) -> ErrorKind:
    """Map an HTTP status (and optional Notion error ``code``) onto a kind."""
    if code == "restricted_resource":
        return ErrorKind.PERMISSION_DENIED
    if code == "rate_limited":
        return ErrorKind.RATE_LIMITED
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
    return ErrorKind.INVALID_INPUT


class _NotionAPIError(Exception):
    """Internal: a typed Notion API failure carrying a taxonomy ``kind``."""

    def __init__(self, kind: ErrorKind, message: str, locator: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.locator = locator


def _normalize_id(raw: str) -> str:
    """Strip dashes from a Notion id and keep the 32-char hex core."""
    return raw.replace("-", "")[:32]


def _extract_notion_id(uri: str) -> Optional[str]:
    """Extract a 32-char Notion page/database id from a URI, if present."""
    dashed = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
        r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        uri,
    )
    if dashed:
        return _normalize_id(dashed.group(1))
    plain = re.search(r"([0-9a-fA-F]{32})", uri)
    if plain:
        return plain.group(1)
    # Fall back: a slug whose dash-stripped form ends in a 32-hex id.
    tail = re.search(r"([0-9a-fA-F]{32})$", uri.replace("-", ""))
    if tail:
        return tail.group(1)
    return None


class _Route:
    """Parsed Notion routing decision (page vs database + the object id)."""

    def __init__(self, kind: str, object_id: str) -> None:
        self.kind = kind
        self.object_id = object_id


def _parse_uri(uri: str) -> Optional[_Route]:
    """Decide whether a URI addresses a Notion page or a database.

    A database is recognised by an explicit ``notion://database/`` scheme or a
    ``?v=<view>`` query parameter (a Notion database-view URL); everything else
    addressing a Notion object is treated as a page. Returns ``None`` when no
    Notion object id can be extracted.
    """
    lowered = uri.lower()
    parsed = urlparse(uri)

    if lowered.startswith("notion://database/") or lowered.startswith("notion://database:"):
        object_id = _extract_notion_id(uri)
        return _Route(DATABASE_KIND, object_id) if object_id else None

    is_database = False
    if parsed.query and "v" in parse_qs(parsed.query):
        is_database = True

    object_id = _extract_notion_id(parsed.path or uri) or _extract_notion_id(uri)
    if not object_id:
        return None
    return _Route(DATABASE_KIND if is_database else PAGE_KIND, object_id)


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse a Notion ISO-8601 timestamp into a timezone-aware datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _extract_icon(data: dict[str, Any]) -> Optional[str]:
    """Extract an icon emoji or url from a page/database payload."""
    icon = data.get("icon") or {}
    if icon.get("emoji"):
        return icon["emoji"]
    if icon.get("external"):
        return icon["external"].get("url")
    if icon.get("file"):
        return icon["file"].get("url")
    return None


def _extract_cover(data: dict[str, Any]) -> Optional[str]:
    """Extract a cover image url from a page/database payload."""
    cover = data.get("cover") or {}
    if cover.get("external"):
        return cover["external"].get("url")
    if cover.get("file"):
        return cover["file"].get("url")
    return None


def _user_label(user: Optional[dict[str, Any]]) -> Optional[str]:
    """Render a Notion user object as a stable author label."""
    if not user:
        return None
    return user.get("name") or user.get("id") or None


def _rich_text_to_markdown(rich_text: list[dict[str, Any]]) -> str:
    """Render a Notion rich-text array to markdown content."""
    out: list[str] = []
    for span in rich_text or []:
        text = span.get("plain_text", "") or ""
        href = span.get("href")
        if href:
            out.append(f"[{text}]({href})")
            continue
        annotations = span.get("annotations", {}) or {}
        if annotations.get("code"):
            text = f"`{text}`"
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"
        out.append(text)
    return "".join(out)


def _extract_title(data: dict[str, Any]) -> str:
    """Extract a human title from a page or database payload."""
    properties = data.get("properties") or {}
    for prop in properties.values():
        if prop.get("type") == "title":
            return _rich_text_to_markdown(prop.get("title", [])) or "Untitled"
    title = data.get("title")
    if isinstance(title, list) and title:
        return _rich_text_to_markdown(title) or "Untitled"
    return "Untitled"


def _property_value(prop: dict[str, Any]) -> Any:
    """Reduce a Notion property object to a JSON-friendly descriptive value."""
    prop_type = prop.get("type")
    raw = prop.get(prop_type) if prop_type else None
    if prop_type in ("title", "rich_text") and isinstance(raw, list):
        return _rich_text_to_markdown(raw)
    if prop_type == "select" and isinstance(raw, dict):
        return raw.get("name")
    if prop_type == "multi_select" and isinstance(raw, list):
        return [item.get("name") for item in raw]
    if prop_type == "people" and isinstance(raw, list):
        return [_user_label(item) for item in raw]
    return raw


def _properties_summary(properties: dict[str, Any]) -> dict[str, Any]:
    """Reduce all page properties to a descriptive name->value mapping."""
    return {name: _property_value(prop) for name, prop in properties.items()}


class NotionConnector(BaseFetcher):
    """
    Canonical Notion connector
    ===============================================
    Fetches a Notion page or database via the Notion REST API and streams it
    as a canonical ``CompositionNode`` tree. A page's blocks become ``Text`` /
    ``Image`` / ``Table`` content atoms (kind ``"page"``); a database becomes a
    container node (kind ``"database"``) whose children are its rows' page
    nodes. Descriptive fields (timestamps, authors, properties, icon, cover,
    url) live in the metadata core and ``source_extra["notion"]``, never on an
    atom.
    ===============================================
    NOTE:
        1. Implements only ``stream()``; ``fetch()`` is inherited base sugar.
        2. Credentials are passed per call via ``auth`` (a Notion integration
           token as ``BearerAuth``) and resolved transiently into request
           headers; nothing is stored on the instance and no ambient
           environment is read.
        3. When a sub-block or database-row fetch fails, the connector yields
           ``partial(tree, [gap(...)])`` -- never a ``success`` with silently
           missing content.

    Attributes
    ----------
        timeout:
            Per-request transport timeout in seconds.

    Methods
    -------
        can_handle:
        stream:
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """
        Create a Notion connector

        Parameters
        ----------
            timeout:
                Per-request transport timeout in seconds.
        """
        self.timeout = timeout
        self._auth_resolver = NormalizedAuthResolver()

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI addresses a Notion page or database

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` when ``uri`` is a ``notion.so`` URL or a ``notion://``
                URI.
        """
        if not uri:
            return False
        lowered = uri.lower()
        return "notion.so" in lowered or lowered.startswith("notion://")

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream a Notion page or database as one canonical result

        Resolves the URI to a page or database, fetches it, and yields exactly
        one ``Result``. A fully-fetched page or database yields ``Success``;
        when block content or a database row could not be fetched, yields
        ``Partial`` carrying the tree built so far plus typed gaps; an
        unresolvable URI, a missing object, or an auth/permission failure
        yields a typed ``Error``.

        NOTE:
            1. Expected failures are yielded as ``Result`` values, never
               raised.
            2. ``zoom`` is accepted for contract conformance; this connector
               emits page blocks at natural (top-level) granularity.

        Parameters
        ----------
            uri:
                A Notion page/database URL (``notion.so/...``) or ``notion://``
                URI.
            auth:
                The per-call Notion integration token (``BearerAuth``), or
                ``None``. Resolved transiently into request headers.
            zoom:
                Optional per-atom-type zoom spec; accepted but not acted on.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        route = _parse_uri(uri)
        if route is None:
            yield error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"could not extract a Notion id from URI: {uri}",
                locator=uri,
            )
            return

        headers = {
            **self._auth_resolver.resolve_headers(auth),
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                base_url=NOTION_API_BASE,
                timeout=self.timeout,
                headers=headers,
            ) as client:
                if route.kind == DATABASE_KIND:
                    yield await self._stream_database(client, route.object_id, uri)
                else:
                    yield await self._stream_page(client, route.object_id, uri)
        except _NotionAPIError as exc:
            yield error(kind=exc.kind, message=exc.message, locator=exc.locator)
        except httpx.HTTPError as exc:
            yield error(
                kind=ErrorKind.TRANSIENT,
                message=f"request failed: {exc}",
                locator=uri,
            )

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Issue one Notion API request, raising ``_NotionAPIError`` on fail."""
        response = await client.request(method, path, json=json)
        if response.status_code >= 400:
            code: Optional[str] = None
            detail = f"HTTP {response.status_code}"
            try:
                body = response.json()
                code = body.get("code")
                detail = body.get("message", detail)
            except (ValueError, httpx.HTTPError):
                pass
            raise _NotionAPIError(
                kind=_status_to_error_kind(response.status_code, code),
                message=detail,
                locator=path,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise _NotionAPIError(
                kind=ErrorKind.PARSE_ERROR,
                message=f"response body is not valid JSON: {exc}",
                locator=path,
            ) from exc

    async def _stream_page(
        self,
        client: httpx.AsyncClient,
        page_id: str,
        uri: str,
    ) -> Result:
        """Fetch a page and build its canonical node (success or partial)."""
        page_data = await self._request(client, "GET", f"/pages/{page_id}")
        node, gaps = await self._build_page_node(client, page_id, page_data)
        if gaps:
            return partial(node, gaps)
        return success(node)

    async def _build_page_node(
        self,
        client: httpx.AsyncClient,
        page_id: str,
        page_data: dict[str, Any],
    ) -> tuple[CompositionNode, list[Gap]]:
        """Build a page node plus any gaps from a failed block fetch.

        The block-children fetch is wrapped so that a failure yields a typed
        ``Gap`` rather than dropping content silently -- the caller turns a
        non-empty gap list into a ``partial`` result.
        """
        gaps: list[Gap] = []
        atoms: list[AnyAtom] = []
        try:
            blocks = await self._fetch_block_children(client, page_id)
            atoms = self._blocks_to_atoms(blocks)
        except _NotionAPIError as exc:
            gaps.append(gap(kind=exc.kind, locator=exc.locator, detail=exc.message))

        node = self._page_node_from_data(page_id, page_data, atoms)
        return node, gaps

    def _page_node_from_data(
        self,
        page_id: str,
        page_data: dict[str, Any],
        atoms: list[AnyAtom],
    ) -> CompositionNode:
        """Assemble a page ``CompositionNode`` from page data and atoms."""
        title = _extract_title(page_data)
        properties = _properties_summary(page_data.get("properties", {}) or {})
        created = _parse_ts(page_data.get("created_time"))
        updated = _parse_ts(page_data.get("last_edited_time"))
        page_url = page_data.get("url") or f"https://notion.so/{page_id}"

        source_fields: dict[str, Any] = {
            "page_id": page_id,
            "title": title,
            "url": page_url,
            "object": page_data.get("object", "page"),
            "icon": _extract_icon(page_data),
            "cover": _extract_cover(page_data),
            "created_time": page_data.get("created_time"),
            "last_edited_time": page_data.get("last_edited_time"),
            "created_by": _user_label(page_data.get("created_by")),
            "last_edited_by": _user_label(page_data.get("last_edited_by")),
            "properties": properties,
        }
        return build_node(
            kind=PAGE_KIND,
            atoms=atoms,
            id=page_id,
            created=created,
            updated=updated,
            author=_user_label(page_data.get("created_by")),
            source_url=page_url,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )

    async def _stream_database(
        self,
        client: httpx.AsyncClient,
        database_id: str,
        uri: str,
    ) -> Result:
        """Fetch a database and its rows into a container node (partial-safe).

        The database object and each row page are fetched individually; any
        row that fails contributes a typed ``Gap`` instead of being silently
        dropped, so a database with an unreadable row yields a ``partial``.
        """
        db_data = await self._request(client, "GET", f"/databases/{database_id}")
        gaps: list[Gap] = []

        rows: list[dict[str, Any]] = []
        try:
            query = await self._request(
                client,
                "POST",
                f"/databases/{database_id}/query",
                json={"page_size": QUERY_PAGE_SIZE},
            )
            rows = query.get("results", []) or []
        except _NotionAPIError as exc:
            gaps.append(gap(kind=exc.kind, locator=exc.locator, detail=exc.message))

        child_nodes: list[CompositionNode] = []
        for row in rows:
            row_id = row.get("id", "")
            try:
                child, row_gaps = await self._build_page_node(client, row_id, row)
                child_nodes.append(child)
                gaps.extend(row_gaps)
            except _NotionAPIError as exc:
                gaps.append(gap(kind=exc.kind, locator=exc.locator, detail=exc.message))

        node = self._database_node_from_data(database_id, db_data, child_nodes)
        if gaps:
            return partial(node, gaps)
        return success(node)

    def _database_node_from_data(
        self,
        database_id: str,
        db_data: dict[str, Any],
        children: list[CompositionNode],
    ) -> CompositionNode:
        """Assemble a database container node from its data and row nodes."""
        title = _extract_title(db_data)
        created = _parse_ts(db_data.get("created_time"))
        updated = _parse_ts(db_data.get("last_edited_time"))
        db_url = db_data.get("url") or f"https://notion.so/{database_id}"

        source_fields: dict[str, Any] = {
            "database_id": database_id,
            "title": title,
            "url": db_url,
            "object": db_data.get("object", "database"),
            "icon": _extract_icon(db_data),
            "cover": _extract_cover(db_data),
            "created_time": db_data.get("created_time"),
            "last_edited_time": db_data.get("last_edited_time"),
            "created_by": _user_label(db_data.get("created_by")),
            "last_edited_by": _user_label(db_data.get("last_edited_by")),
            "properties_schema": list((db_data.get("properties") or {}).keys()),
        }
        return build_node(
            kind=DATABASE_KIND,
            children=children,
            id=database_id,
            created=created,
            updated=updated,
            author=_user_label(db_data.get("created_by")),
            source_url=db_url,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )

    async def _fetch_block_children(
        self,
        client: httpx.AsyncClient,
        block_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch the direct block children of a page/block (one page worth)."""
        data = await self._request(client, "GET", f"/blocks/{block_id}/children")
        return data.get("results", []) or []

    def _blocks_to_atoms(self, blocks: list[dict[str, Any]]) -> list[AnyAtom]:
        """Map Notion blocks to content atoms in document order.

        Image blocks become ``Image`` atoms, table blocks become ``Table``
        atoms, and every other textual/structural block is rendered to a
        markdown ``Text`` atom. Blocks that render to empty content are
        skipped.
        """
        atoms: list[AnyAtom] = []
        for block in blocks:
            block_type = block.get("type", "")
            if block_type == "image":
                image = self._image_atom(block.get("image", {}) or {})
                if image is not None:
                    atoms.append(image)
                continue
            if block_type == "table":
                table = self._table_atom(block)
                if table is not None:
                    atoms.append(table)
                continue
            markdown = self._block_to_markdown(block)
            if markdown:
                atoms.append(Text(content=markdown, format=TextFormat.MARKDOWN))
        return atoms

    def _image_atom(self, image_data: dict[str, Any]) -> Optional[Image]:
        """Build an ``Image`` atom (by reference) from an image block."""
        image_type = image_data.get("type")
        url: Optional[str] = None
        if image_type == "external":
            url = (image_data.get("external") or {}).get("url")
        elif image_type == "file":
            url = (image_data.get("file") or {}).get("url")
        if not url:
            return None
        fmt = "png"
        match = re.search(r"\.(png|jpe?g|gif|webp|svg|bmp|tiff?)", url.lower())
        if match:
            fmt = match.group(1)
        return Image(format=fmt, uri=url)

    def _table_atom(self, block: dict[str, Any]) -> Optional[Table]:
        """Build a ``Table`` atom from a table block and its row children.

        Notion delivers table rows as child blocks; ``_fetch_block_children``
        does not recurse, so rows arrive only when the caller has attached
        them under ``children``. When no rows are present the table renders as
        an empty grid, which is still a faithful ``Table`` atom.
        """
        children = block.get("children", []) or []
        rows: list[list[Any]] = []
        for child in children:
            if child.get("type") != "table_row":
                continue
            cells = (child.get("table_row") or {}).get("cells", []) or []
            rows.append([_rich_text_to_markdown(cell) for cell in cells])

        has_header = (block.get("table") or {}).get("has_column_header", False)
        if has_header and rows:
            headers = [str(cell) for cell in rows[0]]
            return Table(headers=headers, rows=rows[1:])
        return Table(rows=rows)

    def _block_to_markdown(self, block: dict[str, Any]) -> str:
        """Render a single non-image, non-table block to markdown text."""
        block_type = block.get("type", "")
        body = block.get(block_type, {}) or {}
        rich = body.get("rich_text", [])
        text = _rich_text_to_markdown(rich)

        if block_type == "paragraph":
            return text
        if block_type == "heading_1":
            return f"# {text}"
        if block_type == "heading_2":
            return f"## {text}"
        if block_type == "heading_3":
            return f"### {text}"
        if block_type == "bulleted_list_item":
            return f"- {text}"
        if block_type == "numbered_list_item":
            return f"1. {text}"
        if block_type == "to_do":
            checkbox = "[x]" if body.get("checked") else "[ ]"
            return f"{checkbox} {text}"
        if block_type == "toggle":
            return text
        if block_type == "quote":
            return f"> {text}"
        if block_type == "callout":
            return text
        if block_type == "code":
            language = body.get("language", "")
            return f"```{language}\n{text}\n```"
        if block_type == "divider":
            return "---"
        if block_type == "bookmark":
            return f"[Bookmark]({body.get('url', '')})"
        if block_type == "embed":
            return f"[Embed]({body.get('url', '')})"
        return text
