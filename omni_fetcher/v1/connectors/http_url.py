"""HTTP/HTTPS URL connector for the OmniFetcher v1 canonical contract.

Fetches a web resource over HTTP/HTTPS and emits it as a canonical
``CompositionNode`` tree wrapped in a ``Result``. The node carries an
advisory ``kind`` of ``"webpage"``; the page's extracted text (and title,
when present) is carried as ``Text`` atoms, while every descriptive HTTP
field (final URL, status code, content type, headers) lives in the metadata
core and the namespaced ``source_extra["http_url"]`` mapping -- never inline
on an atom.

Expected failures are returned as typed ``Result`` values, never raised:

- An HTTP non-2xx response maps onto the error taxonomy (``404`` ->
  ``NOT_FOUND``, ``401`` -> ``AUTH_FAILED``, ``403`` ->
  ``PERMISSION_DENIED``, ``429`` -> ``RATE_LIMITED``, ``5xx`` ->
  ``TRANSIENT``, other 4xx -> ``INVALID_INPUT``).
- A network/transport failure (timeout, connection error) maps onto
  ``TRANSIENT``.
- Content that cannot be decoded maps onto ``PARSE_ERROR``.

The connector is read-only and deterministic given a fixed response; the
only non-determinism is the wall-clock timestamp stamped into the streamed
node's temporal position. No model-based extraction is performed.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

import httpx
from bs4 import BeautifulSoup

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential, NormalizedAuthResolver
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import (
    SequenceCounter,
    build_node,
    now_utc,
    stamp_temporal,
)
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Result, error, from_exception, success
from omni_fetcher.v1.zoom import ZoomSpec

# Advisory semantic ``kind`` for the node a fetched web resource maps onto.
WEBPAGE_KIND = "webpage"

# Source namespace under which descriptive HTTP fields are recorded.
SOURCE_NAMESPACE = "http_url"

# Maps an HTTP status code onto the closed v1 error taxonomy.
_STATUS_ERROR_KINDS: dict[int, ErrorKind] = {
    401: ErrorKind.AUTH_FAILED,
    403: ErrorKind.PERMISSION_DENIED,
    404: ErrorKind.NOT_FOUND,
    429: ErrorKind.RATE_LIMITED,
}


def _classify_status(status_code: int) -> ErrorKind:
    """Map a non-2xx HTTP status code onto the error taxonomy."""
    if status_code in _STATUS_ERROR_KINDS:
        return _STATUS_ERROR_KINDS[status_code]
    if 500 <= status_code <= 599:
        return ErrorKind.TRANSIENT
    if 400 <= status_code <= 499:
        return ErrorKind.INVALID_INPUT
    # Any other non-2xx (e.g. an unexpected 3xx after redirects) is treated
    # as a transient/retryable anomaly rather than a terminal failure.
    return ErrorKind.TRANSIENT


def _mime_type(content_type: Optional[str]) -> str:
    """Extract the bare MIME type from a Content-Type header value."""
    if not content_type:
        return "application/octet-stream"
    return content_type.split(";")[0].strip().lower()


def _extract_title(soup: BeautifulSoup) -> Optional[str]:
    """Extract a page title from og:title, <title>, or the first <h1>."""
    og_title = soup.find("meta", property="og:title")
    if og_title:
        # Attribute values may be multi-valued lists in bs4; only a plain
        # string is a usable title.
        content = og_title.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text().strip():
        return title_tag.get_text().strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text().strip():
        return h1.get_text().strip()
    return None


def _extract_text(soup: BeautifulSoup) -> str:
    """Extract visible page text, dropping script/style content."""
    for element in soup(["script", "style"]):
        element.decompose()
    raw = soup.get_text(separator="\n")
    lines = [line.strip() for line in raw.split("\n")]
    return "\n".join(line for line in lines if line)


class HTTPURLConnector(BaseFetcher):
    """
    HTTP/HTTPS URL connector
    ===============================================
    Fetches a web resource over HTTP/HTTPS and maps it onto the v1 canonical
    contract: a single ``CompositionNode`` of advisory ``kind`` ``"webpage"``
    whose content is carried as ``Text`` atoms, with every descriptive HTTP
    field placed in the metadata core and the namespaced
    ``source_extra["http_url"]`` mapping. Only ``stream()`` is implemented;
    ``fetch()`` is inherited from ``BaseFetcher`` and collects the bounded
    single-item stream into one ``Result``.
    ===============================================
    NOTE:
        1. Expected failures (non-2xx status, transport errors, decode
           failures) are returned as typed ``Result`` values, never raised.
        2. Credentials are passed per call via ``auth`` and resolved
           transiently into request headers; nothing is stored on the
           instance.
        3. The connector is read-only: it issues a single ``GET`` and never
           mutates the source.

    Attributes
    ----------
        timeout:
            Per-request timeout in seconds for the HTTP client.

    Methods
    -------
        can_handle:
        stream:
    """

    def __init__(self, timeout: float = 30.0) -> None:
        """
        Create an HTTP/HTTPS URL connector

        Parameters
        ----------
            timeout:
                Per-request timeout in seconds applied to the HTTP client
                (default ``30.0``).
        """
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether this connector handles a URI

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` if ``uri`` is an HTTP or HTTPS URL.
        """
        return uri.startswith("http://") or uri.startswith("https://")

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical result for a single HTTP/HTTPS URL

        Issues one ``GET`` to ``uri`` (following redirects) and yields exactly
        one ``Result``: a ``Success`` carrying a ``"webpage"`` node when the
        response is 2xx and decodable, or a typed ``Error`` when an expected
        failure occurs. This is a bounded stream of a single item, so
        ``fetch()`` returns that same item unchanged.

        NOTE:
            1. ``zoom`` is accepted for interface conformance; the connector
               emits the page at its natural granularity (a single node) and
               does not decompose further.
            2. The streamed node is stamped with a monotonic ``sequence`` and
               a wall-clock ``timestamp`` in its temporal position.

        Parameters
        ----------
            uri:
                The HTTP/HTTPS URL to fetch.
            auth:
                The per-call credential, or ``None`` for unauthenticated
                access. When provided, it is resolved into request headers.
            zoom:
                Optional per-atom-type zoom spec; unused (natural
                granularity).

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        headers = NormalizedAuthResolver().resolve_headers(auth)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(uri, headers=headers or None)
        except httpx.HTTPError as exc:
            yield from_exception(exc, kind=ErrorKind.TRANSIENT, locator=uri)
            return

        if response.status_code < 200 or response.status_code >= 300:
            yield error(
                kind=_classify_status(response.status_code),
                message=f"HTTP {response.status_code} for {uri}",
                locator=uri,
            )
            return

        try:
            body = response.text
        except (UnicodeError, ValueError) as exc:
            yield from_exception(exc, kind=ErrorKind.PARSE_ERROR, locator=uri)
            return

        node = self._build_webpage_node(uri, response, body)
        seq = SequenceCounter()
        stamp_temporal(node, sequence=seq.next(), timestamp=now_utc())
        yield success(node)

    def _build_webpage_node(
        self,
        requested_url: str,
        response: httpx.Response,
        body: str,
    ) -> CompositionNode:
        """Build the canonical ``"webpage"`` node from a 2xx response."""
        final_url = str(response.url)
        content_type = response.headers.get("content-type")
        mime = _mime_type(content_type)

        atoms: list[Text] = []
        if mime == "text/html":
            soup = BeautifulSoup(body, "html.parser")
            title = _extract_title(soup)
            if title:
                atoms.append(Text(content=title, format=TextFormat.PLAIN))
            atoms.append(Text(content=_extract_text(soup), format=TextFormat.PLAIN))
        else:
            text_format = TextFormat.MARKDOWN if mime == "text/markdown" else TextFormat.PLAIN
            atoms.append(Text(content=body, format=text_format))

        source_fields: dict[str, Any] = {
            "requested_url": requested_url,
            "final_url": final_url,
            "status_code": response.status_code,
            "content_type": content_type,
            "mime_type": mime,
            "headers": dict(response.headers),
        }
        return build_node(
            kind=WEBPAGE_KIND,
            atoms=atoms,
            source_url=final_url,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
