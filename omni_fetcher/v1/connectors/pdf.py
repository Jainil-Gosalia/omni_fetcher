"""The ``pdf`` connector for the OmniFetcher v1 canonical contract.

Emits a single ``document`` :class:`~omni_fetcher.v1.node.CompositionNode`
per PDF: one ``Text`` atom per page carrying the page's extracted text, with
all descriptive fields (title, author, page count, dates, ...) namespaced
under ``source_extra["pdf"]`` -- never inline on an atom.

Extraction boundary (CRITICAL): text extraction is **deterministic and
read-only** -- the ``pypdf`` text layer only. There is **no OCR**. A page with
no text layer (e.g. a scanned image) is *reported*, not OCR'd: it contributes
an ``UNSUPPORTED`` gap and the result is a ``partial`` tree. A PDF whose every
page lacks a text layer yields a ``partial`` (or, with no readable structure
at all, an ``error``) -- never a blank ``success``. OCR is a Phase 2 concern.
"""

from __future__ import annotations

import asyncio
import io
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import httpx
from pypdf import PdfReader

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Result,
    from_exception,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which all PDF descriptive fields are stored.
SOURCE_NAMESPACE = "pdf"

# Advisory semantic ``kind`` for the node a PDF maps onto.
DOCUMENT_KIND = "document"

# Detail recorded on the gap raised for a page with no text layer. The "OCR
# is Phase 2" wording documents the extraction boundary at the point of
# failure so a consumer can see exactly why the page is empty.
NO_TEXT_LAYER_DETAIL = "scanned page, no text layer; OCR is Phase 2"


class PDFConnector(BaseFetcher):
    """
    Deterministic PDF connector for the v1 canonical contract
    ===============================================
    Streams a single ``document`` composition node for a PDF URI: one
    ``Text`` atom per page (plain text from the ``pypdf`` text layer), with
    descriptive metadata namespaced under ``source_extra["pdf"]``. Text
    extraction is deterministic and read-only -- no OCR. Pages without a text
    layer are reported as ``UNSUPPORTED`` gaps in a ``partial`` result, never
    silently dropped or OCR'd.
    ===============================================
    NOTE:
        1. Expected failures (missing file, unreadable PDF) are returned as
           ``Error`` results, never raised.
        2. A scanned PDF with zero extractable text yields a ``partial`` (or
           an ``error`` when the file is structurally unreadable), never a
           blank ``success``.
        3. ``fetch()`` is inherited from :class:`BaseFetcher`; only
           ``stream()`` is overridden.

    Attributes
    ----------
        timeout:
            Per-request timeout, in seconds, for remote PDF retrieval.

    Methods
    -------
        stream:
        can_handle:
    """

    name = "pdf"

    def __init__(self, timeout: float = 60.0) -> None:
        """
        Create a PDF connector

        Parameters
        ----------
            timeout:
                Per-request timeout, in seconds, used when fetching a remote
                PDF over HTTP (default ``60.0``).
        """
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether this connector claims a URI

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` if ``uri`` names a PDF (``.pdf`` suffix).
        """
        return uri.lower().endswith(".pdf")

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical result for a PDF URI

        Retrieves the PDF bytes (local ``file://`` path or remote HTTP URL),
        extracts the deterministic text layer page by page, and yields a
        single ``Result``: a ``success`` document node when every page has
        text, or a ``partial`` node carrying an ``UNSUPPORTED`` gap per page
        with no text layer (no OCR). Retrieval/parse failures are yielded as
        a typed ``error`` rather than raised.

        Parameters
        ----------
            uri:
                The PDF source URI (``file://...`` or an HTTP(S) URL).
            auth:
                The per-call credential, or ``None`` for an unauthenticated
                source. Unused for plain PDF retrieval.
            zoom:
                Optional per-atom-type zoom spec; ``None`` means natural
                granularity (one text atom per page). Accepted but not
                consulted: a PDF's natural decomposition is one node.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result`` for the
                bounded PDF source.
        """
        try:
            data = await self._read_bytes(uri)
        except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
            yield from_exception(exc, locator=uri)
            return

        try:
            extraction = await asyncio.to_thread(self._extract, data)
        except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
            yield from_exception(
                exc,
                kind=ErrorKind.PARSE_ERROR,
                message="failed to parse PDF",
                locator=uri,
            )
            return

        # Zoom is applied centrally (BaseFetcher.fetch / orchestrator.stream).
        yield self._build_result(uri, extraction)

    async def _read_bytes(self, uri: str) -> bytes:
        """Read raw PDF bytes from a local path or a remote URL."""
        if uri.startswith("file://"):
            return await asyncio.to_thread(_read_local, uri)
        return await self._read_remote(uri)

    async def _read_remote(self, uri: str) -> bytes:
        """Fetch remote PDF bytes over HTTP, raising on a non-2xx status."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(uri)
            response.raise_for_status()
            return response.content

    @staticmethod
    def _extract(data: bytes) -> dict[str, Any]:
        """Extract per-page text and descriptive metadata from PDF bytes.

        Returns a dict with ``pages`` (a list of per-page text strings, one
        per PDF page) and ``meta`` (the descriptive field mapping). Text
        comes solely from the ``pypdf`` text layer -- no OCR is attempted.
        """
        reader = PdfReader(io.BytesIO(data))
        pages: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)
        return {"pages": pages, "meta": _read_metadata(reader, len(pages))}

    def _build_result(
        self,
        uri: str,
        extraction: dict[str, Any],
    ) -> Result:
        """Assemble the canonical document node and wrap it in a Result."""
        pages: list[str] = extraction["pages"]
        meta: dict[str, Any] = extraction["meta"]

        atoms: list[Text] = []
        gaps = []
        for index, text in enumerate(pages):
            if text.strip():
                atoms.append(Text(content=text, format=TextFormat.PLAIN))
            else:
                # No text layer on this page: report it, never OCR.
                gaps.append(
                    gap(
                        kind=ErrorKind.UNSUPPORTED,
                        locator=f"{uri}#page={index + 1}",
                        detail=NO_TEXT_LAYER_DETAIL,
                    )
                )

        node = self._build_node(uri, atoms, meta)
        if gaps:
            return partial(node, gaps)
        return success(node)

    @staticmethod
    def _build_node(
        uri: str,
        atoms: list[Text],
        meta: dict[str, Any],
    ) -> CompositionNode:
        """Build the ``document`` node with PDF metadata namespaced."""
        source_fields = {key: value for key, value in meta.items() if value is not None}
        return build_node(
            kind=DOCUMENT_KIND,
            atoms=atoms,
            author=meta.get("author"),
            created=meta.get("creation_date"),
            updated=meta.get("modification_date"),
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )


def _read_local(uri: str) -> bytes:
    """Read PDF bytes from a ``file://`` URI as a local path.

    Decodes the URI percent-encoding and maps it to a native filesystem
    path (``file:///C:/a%20b.pdf`` -> ``C:\\a b.pdf`` on Windows,
    ``/a b.pdf`` on POSIX), so a path produced by ``Path.as_uri()`` round
    trips on either platform.
    """
    parsed = urlparse(uri)
    path = url2pathname(unquote(parsed.path))
    return Path(path).read_bytes()


def _read_metadata(reader: PdfReader, page_count: int) -> dict[str, Any]:
    """Read descriptive PDF metadata into a flat field mapping."""
    meta: dict[str, Any] = {"page_count": page_count}
    info = reader.metadata
    if not info:
        return meta

    meta["title"] = _clean(info.get("/Title"))
    meta["author"] = _clean(info.get("/Author"))
    meta["subject"] = _clean(info.get("/Subject"))
    meta["creator"] = _clean(info.get("/Creator"))
    meta["producer"] = _clean(info.get("/Producer"))
    meta["language"] = _clean(info.get("/Lang"))
    meta["creation_date"] = _parse_pdf_date(info.get("/CreationDate"))
    meta["modification_date"] = _parse_pdf_date(info.get("/ModDate"))
    return {key: value for key, value in meta.items() if value is not None}


def _clean(value: Any) -> Optional[str]:
    """Normalise a PDF metadata string to a non-empty value or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_pdf_date(value: Any) -> Optional[datetime]:
    """Parse a PDF ``D:YYYYMMDD...`` date string to a naive datetime.

    Deterministic and lenient: returns ``None`` for an absent or
    unparseable value rather than raising.
    """
    if not value:
        return None
    text = str(value)
    if text.startswith("D:"):
        text = text[2:]
    if len(text) < 8:
        return None
    try:
        year = int(text[0:4])
        month = int(text[4:6])
        day = int(text[6:8])
        return datetime(year, month, day)
    except ValueError:
        return None
