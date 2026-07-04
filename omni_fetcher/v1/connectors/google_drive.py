"""The ``google_drive`` connector for the OmniFetcher v1 contract.

Reads a Google Drive file, folder, Doc, Sheet, or Slides deck and maps it onto
the canonical contract: a ``CompositionNode`` tree wrapped in a ``Result``.

Shape of the emitted tree:

- A single file maps onto one node of advisory ``kind`` ``"file"`` carrying one
  content atom -- a ``Text`` atom for a Google Doc / Slides deck (extracted
  text), a ``Table`` atom for a Google Sheet (the first sheet's grid), and an
  empty-content ``Text`` placeholder paired with an ``UNSUPPORTED`` gap for an
  opaque binary file that has no canonical text/table representation.
- A folder maps onto a container node of advisory ``kind`` ``"folder"`` whose
  children are ``kind`` ``"file"`` sub-trees, one per file the folder lists.

Descriptive fields (drive id, name, mime type, size, web links, sheet titles,
slide count, file/folder counts, ...) live in the namespaced
``source_extra["google_drive"]`` mapping -- never inline on an atom.

Auth is supplied *per call* via the ``auth`` parameter. ``google_service_account``
is intentionally NOT a canonical auth type: the host exchanges a service-account
key for a short-lived OAuth2 access token and injects it here as an
``OAuth2Auth``; this connector resolves it through
``NormalizedAuthResolver().resolve_headers`` (the OAuth2 path -> a
``Bearer`` header). It never reads ambient environment, never loads a key file,
and a call with no usable credential is an ``AUTH_FAILED`` error.

Drive/Docs/Sheets/Slides API HTTP errors map onto the closed taxonomy
(``404`` -> ``NOT_FOUND``, ``401`` -> ``AUTH_FAILED``, ``403`` ->
``PERMISSION_DENIED``, ``429`` -> ``RATE_LIMITED``, ``5xx`` -> ``TRANSIENT``,
other 4xx -> ``INVALID_INPUT``). Expected failures are returned as typed
``Error`` / ``Partial`` values, never raised. The connector is read-only and
deterministic given fixed responses (the only non-determinism is the wall-clock
timestamp stamped into the streamed node's temporal position).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from omni_fetcher.v1.atoms import Table, Text, TextFormat
from omni_fetcher.v1.auth import (
    AuthCredential,
    NormalizedAuthResolver,
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

# Source namespace under which all descriptive ``google_drive`` fields are
# stored in ``Metadata.source_extra``.
SOURCE_NAMESPACE = "google_drive"

# Advisory semantic ``kind``s for the nodes this connector emits.
FILE_KIND = "file"
FOLDER_KIND = "folder"

# Google API bases (mirror the v0.11 fetcher so behaviour is unchanged).
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DOCS_API_BASE = "https://docs.googleapis.com/v1"
SHEETS_API_BASE = "https://sheets.googleapis.com/v4"
SLIDES_API_BASE = "https://slides.googleapis.com/v1"

# Default per-request timeout (seconds); matches the v0.11 fetcher.
_DEFAULT_TIMEOUT = 60.0

# Default sheet to export when a Sheets URI names no specific tab.
_DEFAULT_SHEET = "Sheet1"

# Cap on how many files a single folder fetch lists, keeping the bounded
# ``fetch()`` contract honest for very large folders.
_MAX_FOLDER_FILES = 100

# Maps an HTTP status code onto the closed v1 error taxonomy.
_STATUS_ERROR_KINDS: dict[int, ErrorKind] = {
    401: ErrorKind.AUTH_FAILED,
    403: ErrorKind.PERMISSION_DENIED,
    404: ErrorKind.NOT_FOUND,
    429: ErrorKind.RATE_LIMITED,
}


def parse_file_id(uri: str) -> Optional[str]:
    """Extract a Drive file/folder id from a Google Drive/Docs URI.

    Reuses the v0.11 fetcher's parsing so the set of accepted URI shapes is
    unchanged. Returns ``None`` for a URI no pattern matches; the caller maps
    that to an ``INVALID_INPUT`` error.
    """
    if not uri:
        return None

    uri = uri.strip()

    patterns = [
        r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/uc\?id=([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)",
        r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)",
        r"spreadsheets/d/([a-zA-Z0-9_-]+)",
        r"presentation/d/([a-zA-Z0-9_-]+)",
        r"^[a-zA-Z0-9_-]{20,}$",
    ]

    for pattern in patterns:
        match = re.search(pattern, uri)
        if match:
            return match.group(1)

    parsed = urlparse(uri)
    if "drive.google.com" in uri or "docs.google.com" in uri:
        params = parse_qs(parsed.query)
        if "id" in params:
            return params["id"][0]

    return None


def _status_error_kind(status: int) -> ErrorKind:
    """Map an HTTP status code onto the closed v1 error taxonomy."""
    if status in _STATUS_ERROR_KINDS:
        return _STATUS_ERROR_KINDS[status]
    if 500 <= status < 600:
        return ErrorKind.TRANSIENT
    return ErrorKind.INVALID_INPUT


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse a Google RFC-3339 timestamp into a ``datetime`` (or ``None``)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class _ApiError(Exception):
    """A non-2xx Google API response, carrying its status for classification.

    Internal control-flow only; it never escapes the connector boundary --
    ``stream`` catches it and returns a typed ``Error`` value.
    """

    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(detail or f"google api error: {status}")
        self.status = status


def _extract_document_text(doc_data: dict[str, Any]) -> str:
    """Extract plain text from a Google Docs document structure (v0.11 logic)."""
    text_parts: list[str] = []
    for element in doc_data.get("body", {}).get("content", []):
        if "paragraph" in element:
            para = element["paragraph"]
            for text_elem in para.get("elements", []):
                if "textRun" in text_elem:
                    text_parts.append(text_elem["textRun"].get("content", ""))
        elif "table" in element:
            table = element["table"]
            for row in table.get("tableRows", []):
                row_text: list[str] = []
                for cell in row.get("tableCells", []):
                    cell_text: list[str] = []
                    for cell_elem in cell.get("content", []):
                        if "paragraph" in cell_elem:
                            for text_elem in cell_elem["paragraph"].get("elements", []):
                                if "textRun" in text_elem:
                                    cell_text.append(text_elem["textRun"].get("content", ""))
                    row_text.append("".join(cell_text).strip())
                if any(row_text):
                    text_parts.append(" | ".join(row_text))
    return "".join(text_parts)


def _extract_slides_text(pres_data: dict[str, Any]) -> list[str]:
    """Extract each slide's text from a Slides presentation (v0.11 logic)."""
    slides: list[str] = []
    for slide in pres_data.get("slides", []):
        parts: list[str] = []
        for element in slide.get("pageElements", []):
            shape = element.get("shape")
            if shape and shape.get("shapeType") == "TEXT_BOX":
                for text_elem in shape.get("text", {}).get("textElements", []):
                    if "textRun" in text_elem:
                        parts.append(text_elem["textRun"].get("content", ""))
        slides.append("".join(parts).strip())
    return slides


class GoogleDriveFetcher(BaseFetcher):
    """
    Canonical v1 connector for Google Drive / Docs / Sheets / Slides
    ===============================================
    Reads a Drive file or folder and yields a canonical ``CompositionNode``
    tree. A single file becomes a ``kind`` ``"file"`` node carrying one content
    atom (``Text`` for a Doc/Slides deck, ``Table`` for the first sheet of a
    Sheet); a folder becomes a ``kind`` ``"folder"`` container whose children
    are ``kind`` ``"file"`` sub-trees. Descriptive fields live in the
    namespaced ``source_extra["google_drive"]`` mapping. Read-only.
    ===============================================
    NOTE:
        1. Credentials are supplied per call via ``auth``. The canonical type
           is ``OAuth2Auth`` -- the host exchanges a service-account key for a
           short-lived OAuth2 token and injects it here. The connector never
           reads ambient environment or loads a key file; a call with no usable
           credential is an ``AUTH_FAILED`` error.
        2. Expected failures are returned as typed ``Result`` values
           (``NOT_FOUND`` for a missing id, ``PERMISSION_DENIED`` for denied
           access, ``AUTH_FAILED`` for bad credentials, ``RATE_LIMITED`` for
           throttling, ``TRANSIENT`` for 5xx/network blips), never raised.
        3. A binary file with no canonical text/table representation yields a
           ``partial`` node (empty ``Text`` atom + an ``UNSUPPORTED`` gap)
           rather than a silently-empty success.

    Methods
    -------
        stream:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        """
        Create the connector

        Parameters
        ----------
            timeout:
                Per-request HTTP timeout in seconds.
        """
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether ``uri`` names a Google Drive resource

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for a Drive / Docs / Sheets / Slides URL.
        """
        if not uri:
            return False
        lower = uri.lower()
        return any(
            domain in lower
            for domain in (
                "drive.google.com",
                "docs.google.com",
                "spreadsheets.google.com",
                "slides.google.com",
            )
        )

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical node(s) for a Drive resource (the primitive)

        Resolves ``uri`` to a Drive id, resolves the per-call credential into a
        ``Bearer`` header, fetches the resource with the v0.11 Google API
        logic, and yields exactly one ``Result``. A folder yields a container
        node with file children; a single file yields a file node. The root
        node is stamped with a per-stream sequence and a wall-clock timestamp.

        NOTE:
            1. ``auth`` must resolve to an ``Authorization`` header (an
               ``OAuth2Auth`` / ``BearerAuth``); a missing or non-header
               credential yields an ``AUTH_FAILED`` error. Credentials are used
               transiently and never stored on the instance.
            2. Exactly one ``Result`` is yielded; expected failures are yielded
               as typed ``Error`` / ``Partial`` values, never raised.

        Parameters
        ----------
            uri:
                The Drive / Docs / Sheets / Slides URI to read.
            auth:
                The per-call credential (an ``OAuth2Auth``).
            zoom:
                Ignored at v1; natural granularity is used.

        Return
        ------
            results:
                A bounded async iterator yielding one ``Result``.
        """
        counter = SequenceCounter()
        result = await self._fetch_one(uri, auth)
        if not isinstance(result, Error):
            stamp_temporal(result.tree, sequence=counter.next(), timestamp=now_utc())
        yield result

    async def _fetch_one(self, uri: str, auth: Optional[AuthCredential]) -> Result:
        """Resolve, fetch, and map one Drive resource to a single ``Result``."""
        headers = NormalizedAuthResolver().resolve_headers(auth)
        if "Authorization" not in headers:
            return error(
                kind=ErrorKind.AUTH_FAILED,
                message=(
                    "google_drive requires a per-call OAuth2 credential "
                    "(the host exchanges a service account for an OAuth2 token)"
                ),
                locator=uri,
            )

        file_id = parse_file_id(uri)
        if not file_id:
            return error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"could not parse a Google Drive id from: {uri}",
                locator=uri,
            )

        try:
            mime_type = await self._get_mime_type(file_id, headers)
            if "folder" in mime_type.lower():
                return await self._build_folder_node(file_id, uri, headers)
            return await self._build_file_node(file_id, uri, mime_type, headers)
        except _ApiError as exc:
            return error(
                kind=_status_error_kind(exc.status),
                message=str(exc),
                locator=uri,
            )
        except httpx.HTTPError as exc:
            return from_exception(exc, kind=ErrorKind.TRANSIENT, locator=uri)

    async def _api_get(
        self,
        url: str,
        headers: dict[str, str],
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Perform one authenticated GET against a Google API, returning JSON.

        Raises ``_ApiError`` (carrying the HTTP status) for any non-2xx
        response so the caller maps it onto the error taxonomy; transport
        failures surface as ``httpx.HTTPError``.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, params=params, headers=headers)
        if response.status_code >= 400:
            raise _ApiError(response.status_code, response.text)
        return response.json()

    async def _get_mime_type(self, file_id: str, headers: dict[str, str]) -> str:
        """Fetch a resource's MIME type to route it to the right builder."""
        data = await self._api_get(
            f"{DRIVE_API_BASE}/files/{file_id}",
            headers,
            params={"fields": "mimeType"},
        )
        return data.get("mimeType", "")

    async def _build_file_node(
        self,
        file_id: str,
        uri: str,
        mime_type: str,
        headers: dict[str, str],
    ) -> Result:
        """Build the ``kind`` ``"file"`` node for a single (non-folder) file."""
        lowered = mime_type.lower()
        if "spreadsheet" in lowered:
            return await self._build_spreadsheet_node(file_id, uri, headers)
        if "document" in lowered:
            return await self._build_document_node(file_id, uri, headers)
        if "presentation" in lowered:
            return await self._build_presentation_node(file_id, uri, headers)
        return await self._build_opaque_file_node(file_id, uri, mime_type, headers)

    async def _file_metadata(self, file_id: str, headers: dict[str, str]) -> dict[str, Any]:
        """Fetch the common Drive metadata fields for a file or folder."""
        fields = (
            "id,name,mimeType,size,createdTime,modifiedTime,parents,"
            "webViewLink,webContentLink,iconLink,thumbnailLink"
        )
        return await self._api_get(
            f"{DRIVE_API_BASE}/files/{file_id}",
            headers,
            params={"fields": fields},
        )

    @staticmethod
    def _file_source_fields(meta: dict[str, Any]) -> dict[str, Any]:
        """Assemble the namespaced descriptive fields for a file node."""
        size = meta.get("size")
        return {
            "file_id": meta.get("id"),
            "name": meta.get("name"),
            "mime_type": meta.get("mimeType"),
            "size": int(size) if size is not None and size != "" else None,
            "parents": meta.get("parents", []),
            "web_view_link": meta.get("webViewLink"),
            "web_content_link": meta.get("webContentLink"),
            "icon_link": meta.get("iconLink"),
            "thumbnail_link": meta.get("thumbnailLink"),
        }

    async def _build_opaque_file_node(
        self,
        file_id: str,
        uri: str,
        mime_type: str,
        headers: dict[str, str],
    ) -> Result:
        """Build a file node for a generic Drive file with no canonical body.

        The connector does not download arbitrary binary content here; the
        descriptive metadata is captured and the missing content body is made
        explicit via an ``UNSUPPORTED`` gap rather than a silent empty success.
        """
        meta = await self._file_metadata(file_id, headers)
        node = build_node(
            kind=FILE_KIND,
            atoms=[Text(content="", format=TextFormat.PLAIN)],
            id=meta.get("id", file_id),
            created=_parse_timestamp(meta.get("createdTime")),
            updated=_parse_timestamp(meta.get("modifiedTime")),
            source_url=meta.get("webViewLink") or uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=self._file_source_fields(meta),
        )
        return partial(
            node,
            [
                gap(
                    kind=ErrorKind.UNSUPPORTED,
                    locator=uri,
                    detail=f"file content not represented ({mime_type})",
                )
            ],
        )

    async def _build_document_node(self, file_id: str, uri: str, headers: dict[str, str]) -> Result:
        """Build a ``"file"`` node carrying a Google Doc's extracted text."""
        meta = await self._file_metadata(file_id, headers)
        doc = await self._api_get(f"{DOCS_API_BASE}/documents/{file_id}", headers)
        text = _extract_document_text(doc)
        source_fields = self._file_source_fields(meta)
        source_fields["title"] = doc.get("title", meta.get("name"))
        source_fields["document_url"] = f"https://docs.google.com/document/d/{file_id}"
        node = build_node(
            kind=FILE_KIND,
            atoms=[Text(content=text, format=TextFormat.MARKDOWN)],
            id=meta.get("id", file_id),
            created=_parse_timestamp(meta.get("createdTime")),
            updated=_parse_timestamp(meta.get("modifiedTime")),
            source_url=meta.get("webViewLink") or uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        return success(node)

    async def _build_presentation_node(
        self, file_id: str, uri: str, headers: dict[str, str]
    ) -> Result:
        """Build a ``"file"`` node carrying a Slides deck's extracted text."""
        meta = await self._file_metadata(file_id, headers)
        pres = await self._api_get(f"{SLIDES_API_BASE}/presentations/{file_id}", headers)
        slides = _extract_slides_text(pres)
        text = "\n\n".join(slide for slide in slides if slide)
        source_fields = self._file_source_fields(meta)
        source_fields["title"] = pres.get("title", meta.get("name"))
        source_fields["slide_count"] = len(slides)
        source_fields["presentation_url"] = f"https://docs.google.com/presentation/d/{file_id}"
        node = build_node(
            kind=FILE_KIND,
            atoms=[Text(content=text, format=TextFormat.PLAIN)],
            id=meta.get("id", file_id),
            created=_parse_timestamp(meta.get("createdTime")),
            updated=_parse_timestamp(meta.get("modifiedTime")),
            source_url=meta.get("webViewLink") or uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        return success(node)

    async def _build_spreadsheet_node(
        self, file_id: str, uri: str, headers: dict[str, str]
    ) -> Result:
        """Build a ``"file"`` node carrying a Sheet's first tab as a ``Table``."""
        meta = await self._file_metadata(file_id, headers)
        info = await self._api_get(
            f"{SHEETS_API_BASE}/spreadsheets/{file_id}",
            headers,
            params={"fields": "properties.title,sheets(properties(title))"},
        )
        sheet_titles = [
            sheet.get("properties", {}).get("title", _DEFAULT_SHEET)
            for sheet in info.get("sheets", [])
        ]
        sheet_name = sheet_titles[0] if sheet_titles else _DEFAULT_SHEET
        values = await self._api_get(
            f"{SHEETS_API_BASE}/spreadsheets/{file_id}/values/{sheet_name}",
            headers,
        )
        grid = values.get("values", [])
        table = self._grid_to_table(grid)

        source_fields = self._file_source_fields(meta)
        source_fields["title"] = info.get("properties", {}).get("title", meta.get("name"))
        source_fields["sheet_names"] = sheet_titles
        source_fields["sheet_count"] = len(sheet_titles)
        source_fields["exported_sheet"] = sheet_name
        source_fields["spreadsheet_url"] = f"https://docs.google.com/spreadsheets/d/{file_id}"
        node = build_node(
            kind=FILE_KIND,
            atoms=[table],
            id=meta.get("id", file_id),
            created=_parse_timestamp(meta.get("createdTime")),
            updated=_parse_timestamp(meta.get("modifiedTime")),
            source_url=meta.get("webViewLink") or uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        return success(node)

    @staticmethod
    def _grid_to_table(grid: list[list[Any]]) -> Table:
        """Map a Sheets values grid onto a canonical ``Table`` atom.

        The first row is treated as headers only when every body row matches
        its width; otherwise the grid is emitted header-less so the ``Table``
        width-invariant is never violated.
        """
        if not grid:
            return Table(headers=None, rows=[])
        headers = [str(cell) for cell in grid[0]]
        body = grid[1:]
        if body and all(len(row) == len(headers) for row in body):
            return Table(headers=headers, rows=body)
        return Table(headers=None, rows=grid)

    async def _build_folder_node(self, folder_id: str, uri: str, headers: dict[str, str]) -> Result:
        """Build a ``kind`` ``"folder"`` container with file-node children."""
        meta = await self._file_metadata(folder_id, headers)
        listing = await self._list_folder(folder_id, headers)

        children: list[CompositionNode] = []
        for entry in listing:
            child_uri = entry.get("webViewLink") or f"{uri}#{entry.get('id')}"
            child = build_node(
                kind=FILE_KIND,
                atoms=[Text(content="", format=TextFormat.PLAIN)],
                id=entry.get("id"),
                created=_parse_timestamp(entry.get("createdTime")),
                updated=_parse_timestamp(entry.get("modifiedTime")),
                source_url=child_uri,
                source_namespace=SOURCE_NAMESPACE,
                source_fields=self._file_source_fields(entry),
            )
            children.append(child)

        folder_fields = {
            "folder_id": meta.get("id", folder_id),
            "name": meta.get("name"),
            "parents": meta.get("parents", []),
            "web_view_link": meta.get("webViewLink"),
            "file_count": len(children),
        }
        node = build_node(
            kind=FOLDER_KIND,
            children=children,
            id=meta.get("id", folder_id),
            created=_parse_timestamp(meta.get("createdTime")),
            updated=_parse_timestamp(meta.get("modifiedTime")),
            source_url=meta.get("webViewLink") or uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=folder_fields,
        )
        return success(node)

    async def _list_folder(self, folder_id: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        """List up to ``_MAX_FOLDER_FILES`` non-trashed entries in a folder."""
        entries: list[dict[str, Any]] = []
        page_token: Optional[str] = None
        fields = (
            "nextPageToken,files(id,name,mimeType,size,createdTime,"
            "modifiedTime,parents,webViewLink)"
        )
        while len(entries) < _MAX_FOLDER_FILES:
            params: dict[str, Any] = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": fields,
                "pageSize": min(100, _MAX_FOLDER_FILES - len(entries)),
            }
            if page_token:
                params["pageToken"] = page_token
            data = await self._api_get(f"{DRIVE_API_BASE}/files", headers, params=params)
            entries.extend(data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return entries[:_MAX_FOLDER_FILES]
