"""The ``sharepoint`` connector for the OmniFetcher v1 contract.

Reads Microsoft SharePoint sites, document libraries, and files through the
Microsoft Graph API and maps them onto the canonical contract. The shape of
the emitted tree mirrors the SharePoint hierarchy:

- a *site* URI yields a container ``CompositionNode`` of advisory ``kind``
  ``"site"`` whose children are ``"library"`` container nodes;
- a *library* URI yields a ``"library"`` container node whose children are
  ``"file"`` sub-trees (one per file in the library);
- a *file* URI yields a single ``"file"`` node carrying the file's content as
  a content-only ``Text`` atom (text-like files only; binary files are
  represented by metadata alone, with no content atom).

Descriptive fields (ids, names, sizes, mime types, web urls, ...) live in the
metadata core and the namespaced ``source_extra["sharepoint"]`` mapping --
never inline on an atom.

Credentials are supplied **per call** via ``auth`` (an ``OAuth2Auth`` or
``BearerAuth`` carrying a Graph access token) and resolved transiently into
the ``Authorization`` header through ``NormalizedAuthResolver``. Token
acquisition / refresh (e.g. the Azure client-credentials flow) is the host's
responsibility; this connector never reads the ambient environment for
credentials, never stores them on the instance, and never mutates them. A
call with no usable credential is an ``AUTH_FAILED`` error.

Expected failures (missing site/library/file, bad/expired token, denied
access, throttling, network blips, bad input) are returned as typed
``Error`` / ``Partial`` values, never raised. HTTP status codes map onto the
canonical taxonomy: ``401`` -> ``AUTH_FAILED``, ``403`` ->
``PERMISSION_DENIED``, ``404`` -> ``NOT_FOUND``, ``429`` -> ``RATE_LIMITED``,
``5xx`` -> ``TRANSIENT``.

This reuses the v0.11 SharePoint fetcher's URI parsing and Graph traversal
logic, but feeds it the per-call credential only. Deterministic and
read-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from urllib.parse import unquote, urlparse

import httpx

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import (
    AuthCredential,
    BearerAuth,
    NormalizedAuthResolver,
    OAuth2Auth,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import (
    SequenceCounter,
    build_node,
    now_utc,
    stamp_temporal,
)
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Error,
    Result,
    error,
    from_exception,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which descriptive ``sharepoint`` fields are stored.
SOURCE_NAMESPACE = "sharepoint"

# Advisory semantic ``kind`` for each tier of the emitted tree.
SITE_KIND = "site"
LIBRARY_KIND = "library"
FILE_KIND = "file"

# Microsoft Graph API base URL.
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Default per-request timeout, in seconds.
DEFAULT_TIMEOUT_SECONDS = 60.0

# Default cap on the number of files enumerated under a library.
DEFAULT_MAX_ITEMS = 100

# HTTP status codes mapped directly onto canonical error kinds.
_STATUS_ERROR_KINDS: dict[int, ErrorKind] = {
    401: ErrorKind.AUTH_FAILED,
    403: ErrorKind.PERMISSION_DENIED,
    404: ErrorKind.NOT_FOUND,
    429: ErrorKind.RATE_LIMITED,
}

# MIME types (besides ``text/*``) decoded into a content ``Text`` atom.
_TEXT_MIMES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
    }
)


def _classify_status(status_code: int) -> ErrorKind:
    """Map a Graph HTTP error status onto a canonical ``ErrorKind``."""
    mapped = _STATUS_ERROR_KINDS.get(status_code)
    if mapped is not None:
        return mapped
    if 500 <= status_code <= 599:
        return ErrorKind.TRANSIENT
    # Any other non-2xx is treated as retryable rather than terminal.
    return ErrorKind.TRANSIENT


@dataclass
class SharePointRoute:
    """A parsed SharePoint URI route.

    Attributes
    ----------
        type:
            One of ``"site"`` / ``"library"`` / ``"file"``.
        hostname:
            The SharePoint host (e.g. ``contoso.sharepoint.com``), or ``""``.
        site_name:
            The site name (URL path segment).
        library_name:
            The document-library (drive) name, when addressing a library or
            file.
        file_name:
            The file path within the library, when addressing a file.
    """

    type: str
    hostname: str
    site_name: str
    library_name: Optional[str] = None
    file_name: Optional[str] = None


def parse_sharepoint_uri(uri: str) -> SharePointRoute:
    """Parse a SharePoint URI into its route components.

    Supports both the ``sharepoint://`` protocol form and
    ``https://*.sharepoint.com`` URLs. Mirrors the v0.11 fetcher's parsing so
    behaviour is unchanged. Raises ``ValueError`` for a URI that is not a
    recognised SharePoint reference; the caller maps that to an
    ``INVALID_INPUT`` error.
    """
    uri = uri.strip()
    if uri.startswith("sharepoint://"):
        return _parse_sharepoint_protocol(uri)
    if "sharepoint.com" in uri.lower():
        return _parse_sharepoint_url(uri)
    raise ValueError(f"not a SharePoint URI: {uri}")


def _parse_sharepoint_protocol(uri: str) -> SharePointRoute:
    """Parse a ``sharepoint://`` protocol URI."""
    uri = uri.replace("sharepoint://", "")
    if uri.startswith("sites/"):
        uri = uri[len("sites/"):]

    if "/" not in uri:
        return SharePointRoute(type=SITE_KIND, hostname="", site_name=uri)

    site_name, remainder = uri.split("/", 1)
    if "/" in remainder:
        library_name, file_name = remainder.split("/", 1)
        return SharePointRoute(
            type=FILE_KIND,
            hostname="",
            site_name=site_name,
            library_name=library_name,
            file_name=unquote(file_name),
        )
    return SharePointRoute(
        type=LIBRARY_KIND,
        hostname="",
        site_name=site_name,
        library_name=remainder,
    )


def _parse_sharepoint_url(uri: str) -> SharePointRoute:
    """Parse an ``https://*.sharepoint.com`` URL."""
    parsed = urlparse(uri)
    path = parsed.path.strip("/")
    hostname = parsed.netloc.lower()
    path_parts = path.split("/") if path else []

    if not path_parts or path_parts[0] != "sites":
        return _parse_non_sites_path(path_parts, hostname, uri)

    if len(path_parts) < 2:
        raise ValueError(f"could not parse SharePoint URL: {uri}")

    site_name = path_parts[1]
    if re.search(r"Shared%20Documents|Shared Documents", path):
        return _parse_shared_documents(path, hostname, site_name)

    if len(path_parts) >= 3:
        library_name = path_parts[2]
        if len(path_parts) >= 4:
            file_name = "/".join(path_parts[3:])
            return SharePointRoute(
                type=FILE_KIND,
                hostname=hostname,
                site_name=site_name,
                library_name=library_name,
                file_name=unquote(file_name),
            )
        return SharePointRoute(
            type=LIBRARY_KIND,
            hostname=hostname,
            site_name=site_name,
            library_name=library_name,
        )
    return SharePointRoute(
        type=SITE_KIND, hostname=hostname, site_name=site_name
    )


def _parse_non_sites_path(
    path_parts: list[str],
    hostname: str,
    uri: str,
) -> SharePointRoute:
    """Parse a SharePoint URL whose path does not begin with ``sites``."""
    if not path_parts:
        raise ValueError(f"could not parse SharePoint URL: {uri}")
    site_name = path_parts[0]
    if len(path_parts) >= 2:
        library_name = path_parts[1]
        if len(path_parts) >= 3:
            file_name = "/".join(path_parts[2:])
            return SharePointRoute(
                type=FILE_KIND,
                hostname=hostname,
                site_name=site_name,
                library_name=library_name,
                file_name=unquote(file_name),
            )
        return SharePointRoute(
            type=LIBRARY_KIND,
            hostname=hostname,
            site_name=site_name,
            library_name=library_name,
        )
    return SharePointRoute(type=SITE_KIND, hostname=hostname, site_name=site_name)


def _parse_shared_documents(
    path: str,
    hostname: str,
    site_name: str,
) -> SharePointRoute:
    """Parse a path that references the ``Shared Documents`` library."""
    library_name = "Shared Documents"
    remaining = path.split("Shared Documents", 1)
    if len(remaining) > 1 and remaining[1].strip("/"):
        return SharePointRoute(
            type=FILE_KIND,
            hostname=hostname,
            site_name=site_name,
            library_name=library_name,
            file_name=unquote(remaining[1].strip("/")),
        )
    return SharePointRoute(
        type=LIBRARY_KIND,
        hostname=hostname,
        site_name=site_name,
        library_name=library_name,
    )


def _parse_graph_datetime(value: Any) -> Optional[datetime]:
    """Parse a Graph ISO-8601 timestamp into a ``datetime``, else ``None``."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_text_mime(mime_type: Optional[str]) -> bool:
    """Report whether a file's content should be decoded into a ``Text`` atom."""
    base = (mime_type or "").split(";", 1)[0].strip().lower()
    if base.startswith("text/"):
        return True
    return base in _TEXT_MIMES


def _text_format_for(mime_type: Optional[str]) -> TextFormat:
    """Pick the surface ``TextFormat`` for a text-like file's content."""
    base = (mime_type or "").split(";", 1)[0].strip().lower()
    if base == "text/html":
        return TextFormat.HTML
    if base == "text/markdown":
        return TextFormat.MARKDOWN
    return TextFormat.PLAIN


class SharePointConnector(BaseFetcher):
    """
    Canonical v1 connector for Microsoft SharePoint
    ===============================================
    Reads SharePoint sites, document libraries, and files through the
    Microsoft Graph API and maps them onto the canonical contract. A site is
    a ``"site"`` container whose children are ``"library"`` containers; a
    library is a ``"library"`` container whose children are ``"file"``
    sub-trees; a single file is a ``"file"`` node carrying its content as a
    content-only ``Text`` atom. Descriptive fields live in the metadata core
    and the namespaced ``source_extra["sharepoint"]`` mapping. Read-only.
    ===============================================
    NOTE:
        1. Credentials are supplied per call via ``auth`` (an ``OAuth2Auth``
           or ``BearerAuth`` carrying a Graph access token) and resolved
           transiently into the ``Authorization`` header. Token acquisition
           and refresh are the host's job; this connector never reads ambient
           environment credentials and stores nothing on the instance. A call
           with no usable credential is an ``AUTH_FAILED`` error.
        2. Only ``stream()`` is implemented; ``fetch()`` is inherited from
           ``BaseFetcher``.
        3. Expected failures are returned as typed ``Result`` values
           (``NOT_FOUND`` / ``AUTH_FAILED`` / ``PERMISSION_DENIED`` /
           ``RATE_LIMITED`` / ``TRANSIENT`` / ``INVALID_INPUT``), never
           raised.

    Attributes
    ----------
        timeout:
            Per-request timeout in seconds.
        max_items:
            Cap on the number of files enumerated under a library.

    Methods
    -------
        can_handle:
        stream:
    """

    name = SOURCE_NAMESPACE

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_items: int = DEFAULT_MAX_ITEMS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        """
        Create a SharePoint connector

        Parameters
        ----------
            timeout:
                Per-request timeout in seconds.
            max_items:
                Cap on the number of files enumerated under a library.
            transport:
                Optional ``httpx`` transport. Tests inject a mock transport
                here to avoid real network access; production leaves it
                ``None`` so ``httpx`` uses its default transport.
        """
        self.timeout = timeout
        self.max_items = max_items
        self._transport = transport
        self._resolver = NormalizedAuthResolver()

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether ``uri`` names a SharePoint resource

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for a ``sharepoint://`` URI or a
                ``*.sharepoint.com`` URL (excluding personal ``-my`` sites).
        """
        if not uri:
            return False
        lower = uri.lower()
        if "sharepoint.com" not in lower and not lower.startswith(
            "sharepoint://"
        ):
            return False
        return "-my.sharepoint.com" not in lower

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical node tree for a SharePoint URI (the primitive)

        Resolves the per-call credential into request headers, parses the URI
        into a site / library / file route, traverses the Graph API, and
        yields exactly one ``Result`` carrying the canonical tree. The root
        node is stamped with a per-stream sequence and a wall-clock
        timestamp.

        NOTE:
            1. ``auth`` must be an ``OAuth2Auth`` or ``BearerAuth``; ``None``
               (or any other credential) yields an ``AUTH_FAILED`` error.
               Credentials are used transiently and never stored.
            2. Exactly one ``Result`` is yielded; expected failures are
               yielded as typed ``Error`` / ``Partial`` values, never raised.

        Parameters
        ----------
            uri:
                The SharePoint URI (``sharepoint://`` or
                ``https://*.sharepoint.com/...``) to read.
            auth:
                The per-call OAuth2/Bearer credential.
            zoom:
                Accepted for contract conformance; the tree is emitted at its
                natural hierarchical granularity.

        Return
        ------
            results:
                A bounded async iterator yielding exactly one ``Result``.
        """
        counter = SequenceCounter()
        result = await self._fetch(uri, auth)
        if not isinstance(result, Error):
            stamp_temporal(
                result.tree, sequence=counter.next(), timestamp=now_utc()
            )
        yield result

    async def _fetch(
        self,
        uri: str,
        auth: Optional[AuthCredential],
    ) -> Result:
        """Resolve auth, route the URI, and build one ``Result``."""
        if not isinstance(auth, (OAuth2Auth, BearerAuth)):
            return error(
                kind=ErrorKind.AUTH_FAILED,
                message="sharepoint requires a per-call OAuth2/Bearer "
                "credential carrying a Graph access token",
                locator=uri,
            )

        if not self.can_handle(uri):
            return error(
                kind=ErrorKind.INVALID_INPUT,
                message="not a supported SharePoint URI",
                locator=uri,
            )

        try:
            route = parse_sharepoint_uri(uri)
        except ValueError as exc:
            return from_exception(
                exc, kind=ErrorKind.INVALID_INPUT, locator=uri
            )

        headers = self._resolver.resolve_headers(auth)
        try:
            if route.type == SITE_KIND:
                return await self._fetch_site(uri, route, headers)
            if route.type == LIBRARY_KIND:
                return await self._fetch_library(uri, route, headers)
            return await self._fetch_file(uri, route, headers)
        except _GraphError as exc:
            return error(kind=exc.kind, message=exc.message, locator=uri)
        except httpx.TimeoutException as exc:
            return from_exception(
                exc, kind=ErrorKind.TRANSIENT, locator=uri
            )
        except httpx.HTTPError as exc:
            return from_exception(
                exc, kind=ErrorKind.TRANSIENT, locator=uri
            )

    # ------------------------------------------------------------------
    # Graph traversal

    async def _fetch_site(
        self,
        uri: str,
        route: SharePointRoute,
        headers: dict[str, str],
    ) -> Result:
        """Build a ``"site"`` container whose children are libraries."""
        async with self._client() as client:
            site = await self._get_site(client, route, headers)
            site_id = site.get("id", "")
            drives = await self._graph(
                client, f"/sites/{site_id}/drives", headers
            )

            children: list[CompositionNode] = []
            for drive in drives.get("value", []):
                children.append(self._library_node(drive, files=[]))

        node = build_node(
            kind=SITE_KIND,
            children=children,
            id=site.get("id"),
            source_url=site.get("webUrl") or uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "site_id": site.get("id"),
                "name": site.get("name"),
                "display_name": site.get("displayName"),
                "description": site.get("description"),
                "hostname": route.hostname,
                "web_url": site.get("webUrl"),
            },
        )
        return success(node)

    async def _fetch_library(
        self,
        uri: str,
        route: SharePointRoute,
        headers: dict[str, str],
    ) -> Result:
        """Build a ``"library"`` container whose children are file nodes."""
        async with self._client() as client:
            site = await self._get_site(client, route, headers)
            site_id = site.get("id", "")
            drives = await self._graph(
                client, f"/sites/{site_id}/drives", headers
            )
            drive = self._select_drive(drives, route.library_name)
            if drive is None:
                return error(
                    kind=ErrorKind.NOT_FOUND,
                    message=f"library not found: {route.library_name}",
                    locator=uri,
                )

            drive_id = drive.get("id", "")
            listing = await self._graph(
                client,
                f"/drives/{drive_id}/root/children",
                headers,
                params={"$top": self.max_items},
            )
            file_nodes: list[CompositionNode] = []
            for item in listing.get("value", []):
                if "file" in item:
                    file_nodes.append(
                        self._file_node(item, drive_id, content=None)
                    )

        node = self._library_node(drive, files=file_nodes)
        return success(node)

    async def _fetch_file(
        self,
        uri: str,
        route: SharePointRoute,
        headers: dict[str, str],
    ) -> Result:
        """Build a single ``"file"`` node for the addressed file."""
        if not route.file_name:
            return error(
                kind=ErrorKind.INVALID_INPUT,
                message="file path not specified",
                locator=uri,
            )

        async with self._client() as client:
            site = await self._get_site(client, route, headers)
            site_id = site.get("id", "")
            drives = await self._graph(
                client, f"/sites/{site_id}/drives", headers
            )
            drive = self._select_drive(drives, route.library_name)
            if drive is None:
                return error(
                    kind=ErrorKind.NOT_FOUND,
                    message=f"library not found: {route.library_name}",
                    locator=uri,
                )

            drive_id = drive.get("id", "")
            item = await self._graph(
                client,
                f"/drives/{drive_id}/root:/{route.file_name}",
                headers,
            )
            content, gap_detail = await self._maybe_download(
                client, item, drive_id, headers
            )

        node = self._file_node(item, drive_id, content=content)
        if gap_detail is not None:
            return partial(
                node,
                [
                    gap(
                        kind=ErrorKind.UNSUPPORTED,
                        locator=uri,
                        detail=gap_detail,
                    )
                ],
            )
        return success(node)

    async def _maybe_download(
        self,
        client: httpx.AsyncClient,
        item: dict[str, Any],
        drive_id: str,
        headers: dict[str, str],
    ) -> tuple[Optional[str], Optional[str]]:
        """Download text-like file content; return ``(text, gap_detail)``.

        Returns the decoded text and a ``None`` gap for text-like files; for
        binary (or undecodable) files it returns ``(None, detail)`` so the
        caller can emit an explicit ``UNSUPPORTED`` gap rather than a silent
        empty success.
        """
        mime_type = item.get("file", {}).get("mimeType")
        if not _is_text_mime(mime_type):
            return None, f"binary content not represented ({mime_type})"

        file_id = item.get("id", "")
        data = await self._graph_download(
            client, f"/drives/{drive_id}/items/{file_id}/content", headers
        )
        try:
            return data.decode("utf-8"), None
        except UnicodeDecodeError:
            return None, "file content is not valid UTF-8 text"

    async def _get_site(
        self,
        client: httpx.AsyncClient,
        route: SharePointRoute,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """Resolve a site by name (optionally scoped by hostname)."""
        if not route.site_name:
            raise _GraphError(ErrorKind.INVALID_INPUT, "site name not specified")
        if route.hostname:
            filter_query = (
                f"siteCollection/hostname eq '{route.hostname}' "
                f"and name eq '{route.site_name}'"
            )
        else:
            filter_query = f"name eq '{route.site_name}'"

        result = await self._graph(
            client,
            "/sites",
            headers,
            params={"$filter": filter_query, "$top": 1},
        )
        sites = result.get("value", [])
        if not sites:
            raise _GraphError(
                ErrorKind.NOT_FOUND, f"site not found: {route.site_name}"
            )
        return sites[0]

    @staticmethod
    def _select_drive(
        drives: dict[str, Any],
        library_name: Optional[str],
    ) -> Optional[dict[str, Any]]:
        """Pick the drive matching ``library_name`` (falling back to default)."""
        target = (library_name or "Documents").lower()
        for drive in drives.get("value", []):
            if drive.get("name", "").lower() == target:
                return drive
        # The default library's site-relative URL segment is "Shared
        # Documents" while Graph names its drive "Documents"; honour that
        # alias only. Any other unmatched name means the library does not
        # exist and must surface as NOT_FOUND, never as the default drive.
        if target in {"documents", "shared documents"}:
            for drive in drives.get("value", []):
                if drive.get("name", "").lower() == "documents":
                    return drive
        return None

    # ------------------------------------------------------------------
    # Node mapping

    def _library_node(
        self,
        drive: dict[str, Any],
        *,
        files: list[CompositionNode],
    ) -> CompositionNode:
        """Map a Graph drive (+ optional file children) to a library node."""
        return build_node(
            kind=LIBRARY_KIND,
            children=files,
            id=drive.get("id"),
            source_url=drive.get("webUrl"),
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "library_id": drive.get("id"),
                "name": drive.get("name"),
                "description": drive.get("description"),
                "drive_type": drive.get("driveType"),
                "web_url": drive.get("webUrl"),
                "item_count": len(files),
            },
        )

    def _file_node(
        self,
        item: dict[str, Any],
        drive_id: str,
        *,
        content: Optional[str],
    ) -> CompositionNode:
        """Map a Graph drive-item to a ``"file"`` node (content-only atom)."""
        file_props = item.get("file", {})
        mime_type = file_props.get("mimeType", "application/octet-stream")
        created_by = item.get("createdBy", {}).get("user", {})
        author = created_by.get("displayName") or created_by.get("email")
        modified_by = item.get("lastModifiedBy", {}).get("user", {})
        last_modifier = modified_by.get("displayName") or modified_by.get(
            "email"
        )

        atoms: list[Text] = []
        if content is not None:
            atoms.append(
                Text(content=content, format=_text_format_for(mime_type))
            )

        return build_node(
            kind=FILE_KIND,
            atoms=atoms,
            id=item.get("id"),
            created=_parse_graph_datetime(item.get("createdDateTime")),
            updated=_parse_graph_datetime(item.get("lastModifiedDateTime")),
            author=author,
            source_url=item.get("webUrl"),
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "file_id": item.get("id"),
                "name": item.get("name"),
                "drive_id": drive_id,
                "mime_type": mime_type,
                "size_bytes": item.get("size"),
                "last_modified_by": last_modifier,
                "web_url": item.get("webUrl"),
            },
        )

    # ------------------------------------------------------------------
    # HTTP plumbing

    def _client(self) -> httpx.AsyncClient:
        """Build a fresh ``httpx`` client honouring the injected transport."""
        return httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            transport=self._transport,
        )

    async def _graph(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        headers: dict[str, str],
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """GET a Graph JSON endpoint, raising ``_GraphError`` on a bad status."""
        response = await client.get(
            f"{GRAPH_BASE}{endpoint}", params=params, headers=headers
        )
        if 200 <= response.status_code <= 299:
            return response.json()
        raise _GraphError(
            _classify_status(response.status_code),
            f"graph error {response.status_code} for {endpoint}",
        )

    async def _graph_download(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        headers: dict[str, str],
    ) -> bytes:
        """GET raw file content from Graph, raising ``_GraphError`` on error."""
        response = await client.get(f"{GRAPH_BASE}{endpoint}", headers=headers)
        if 200 <= response.status_code <= 299:
            return response.content
        raise _GraphError(
            _classify_status(response.status_code),
            f"graph download error {response.status_code} for {endpoint}",
        )


class _GraphError(Exception):
    """Internal carrier mapping a Graph failure to a typed ``ErrorKind``.

    Raised inside the traversal helpers and caught at the ``stream``/``_fetch``
    boundary, where it is converted into a returned ``Error`` value. It never
    escapes the connector -- expected failures are returned, not raised.
    """

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
