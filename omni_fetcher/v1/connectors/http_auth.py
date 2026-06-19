"""Authenticated HTTP connector for the OmniFetcher v1 contract.

Fetches a single authenticated HTTP(S) resource and emits it as one
canonical ``CompositionNode`` (advisory ``kind`` ``"api_response"``) wrapped
in a ``Result``. The response body becomes a content-only ``Text`` atom; the
descriptive surface (status, content type, final URL, response headers) lives
in the metadata core and the namespaced ``source_extra["http_auth"]``.

Credentials are supplied **per call** via ``auth`` (an ``AuthCredential``)
and resolved transiently into request headers through
``NormalizedAuthResolver``. This connector never reads the ambient
environment for credentials, never stores them on the instance, and never
mutates them -- each call is self-contained (see ``PHILOSOPHY.md`` section
7).

Expected failures are returned as typed ``Error`` results, never raised.
HTTP status codes map onto the canonical taxonomy:

- ``401`` -> ``AUTH_FAILED``
- ``403`` -> ``PERMISSION_DENIED``
- ``404`` -> ``NOT_FOUND``
- ``429`` -> ``RATE_LIMITED``
- ``5xx`` -> ``TRANSIENT``

Other non-2xx statuses surface as ``TRANSIENT`` (retryable) by default, and a
missing required credential on an unauthorized response is reported as
``AUTH_FAILED``. Network/timeout failures are classified via the shared
exception helpers.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

import httpx

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
from omni_fetcher.v1.node import NodeChild
from omni_fetcher.v1.result import (
    Error,
    Result,
    error,
    from_exception,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# The source namespace this connector stamps its descriptive fields under.
SOURCE_NAMESPACE = "http_auth"

# Advisory semantic kind for the emitted node.
API_RESPONSE_KIND = "api_response"

# Default per-request timeout, in seconds.
DEFAULT_TIMEOUT_SECONDS = 30.0

# Cap on the bytes of body text materialised into the content atom, so an
# unexpectedly large response cannot blow up memory.
MAX_CONTENT_CHARS = 1_000_000

# HTTP status codes mapped directly onto canonical error kinds.
_STATUS_ERROR_KINDS: dict[int, ErrorKind] = {
    401: ErrorKind.AUTH_FAILED,
    403: ErrorKind.PERMISSION_DENIED,
    404: ErrorKind.NOT_FOUND,
    429: ErrorKind.RATE_LIMITED,
}


def _classify_status(status_code: int) -> ErrorKind:
    """Map an HTTP error status onto a canonical ``ErrorKind``."""
    mapped = _STATUS_ERROR_KINDS.get(status_code)
    if mapped is not None:
        return mapped
    if 500 <= status_code <= 599:
        return ErrorKind.TRANSIENT
    # Any other non-2xx (e.g. 408, 4xx without a dedicated mapping) is
    # treated as retryable rather than terminal.
    return ErrorKind.TRANSIENT


class HTTPAuthConnector(BaseFetcher):
    """
    Authenticated HTTP connector emitting canonical nodes
    ===============================================
    A v1 connector that fetches a single authenticated HTTP(S) resource and
    yields it as one ``CompositionNode`` (``kind`` ``"api_response"``) wrapped
    in a ``Result``. Credentials are injected per call via ``auth`` and
    resolved transiently into request headers; nothing is stored on the
    instance and the ambient environment is never read for credentials.
    ===============================================
    NOTE:
        1. Only ``stream()`` is implemented; ``fetch()`` is inherited from
           ``BaseFetcher`` and collects the single yielded result.
        2. The response body is emitted as a content-only ``Text`` atom; all
           descriptive fields live in metadata and
           ``source_extra["http_auth"]``.
        3. Expected failures (bad status, network error) are returned as
           typed ``Error`` results, never raised.

    Attributes
    ----------
        timeout:
            Per-request timeout in seconds.
        extra_headers:
            Non-credential headers applied to every request.

    Methods
    -------
        can_handle:
        stream:
    """

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        headers: Optional[dict[str, str]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        """
        Create an authenticated HTTP connector

        Parameters
        ----------
            timeout:
                Per-request timeout in seconds.
            headers:
                Optional non-credential headers applied to every request
                (e.g. ``Accept``). Credentials must come per call via
                ``auth``, never here.
            transport:
                Optional ``httpx`` transport. Tests inject a mock transport
                here to avoid real network access; production leaves it
                ``None`` so ``httpx`` uses its default transport.
        """
        self.timeout = timeout
        self.extra_headers = dict(headers) if headers else {}
        self._transport = transport
        self._resolver = NormalizedAuthResolver()

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
                ``True`` for ``http://`` and ``https://`` URIs.
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
        Stream the authenticated HTTP resource as one canonical result

        Resolves the per-call credential into request headers, performs a
        single ``GET``, and yields exactly one ``Result``: a ``Success``
        carrying the response as a canonical node, or a typed ``Error`` for
        an expected failure (bad URI, network blip, non-2xx status).

        NOTE:
            1. Credentials are consumed transiently from ``auth``; the
               ambient environment is never read.
            2. ``zoom`` is accepted for contract conformance; the response is
               emitted at its natural granularity (a single content atom).

        Parameters
        ----------
            uri:
                The HTTP(S) URI to fetch.
            auth:
                The per-call credential, or ``None`` for an unauthenticated
                request.
            zoom:
                Optional per-atom-type zoom spec; honoured at natural
                granularity here.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        if not self.can_handle(uri):
            yield error(
                kind=ErrorKind.INVALID_INPUT,
                message="http_auth only handles http:// and https:// URIs",
                locator=uri,
            )
            return

        headers = dict(self.extra_headers)
        headers.update(self._resolver.resolve_headers(auth))

        response = await self._request(uri, headers)
        if isinstance(response, Error):
            yield response
            return

        status_error = self._status_error(response, auth)
        if status_error is not None:
            yield status_error
            return

        yield self._build_success(uri, response)

    async def _request(
        self,
        uri: str,
        headers: dict[str, str],
    ) -> "httpx.Response | Error":
        """Perform the GET, returning a response or a typed transport error."""
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                return await client.get(uri, headers=headers)
        except httpx.TimeoutException as exc:
            return from_exception(
                exc,
                kind=ErrorKind.TRANSIENT,
                message="http request timed out",
                locator=uri,
            )
        except httpx.HTTPError as exc:
            return from_exception(
                exc,
                kind=ErrorKind.TRANSIENT,
                message="http request failed",
                locator=uri,
            )

    def _status_error(
        self,
        response: httpx.Response,
        auth: Optional[AuthCredential],
    ) -> Optional[Error]:
        """Return a typed error for a non-2xx status, else ``None``."""
        status = response.status_code
        if 200 <= status <= 299:
            return None

        kind = _classify_status(status)
        # A 401 without any supplied credential is a missing-credential
        # failure; surface it as AUTH_FAILED with a clearer message.
        if status == 401 and auth is None:
            message = "authentication required but no credential supplied"
        else:
            message = f"http status {status}"
        return error(
            kind=kind,
            message=message,
            locator=str(response.url),
        )

    def _build_success(
        self,
        uri: str,
        response: httpx.Response,
    ) -> Result:
        """Build the success result for a 2xx response."""
        content_type = response.headers.get(
            "content-type", "application/octet-stream"
        )
        mime_type = content_type.split(";")[0].strip()

        atoms = self._content_atoms(response, mime_type)

        source_fields: dict[str, Any] = {
            "status_code": response.status_code,
            "mime_type": mime_type,
            "content_type": content_type,
            "final_url": str(response.url),
            "requested_url": uri,
            "response_headers": dict(response.headers),
        }

        node = build_node(
            kind=API_RESPONSE_KIND,
            atoms=atoms,
            source_url=str(response.url),
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        # Single emitted node: stamp its position on this stream's order line.
        sequence = SequenceCounter()
        stamp_temporal(node, sequence=sequence.next(), timestamp=now_utc())
        return success(node)

    def _content_atoms(
        self,
        response: httpx.Response,
        mime_type: str,
    ) -> list[NodeChild]:
        """Build the content-only atom(s) for a response body."""
        text = response.text[:MAX_CONTENT_CHARS]
        text_format = self._text_format(mime_type)
        return [Text(content=text, format=text_format)]

    @staticmethod
    def _text_format(mime_type: str) -> TextFormat:
        """Pick the surface text format for a response MIME type."""
        if mime_type == "text/html":
            return TextFormat.HTML
        if mime_type == "text/markdown":
            return TextFormat.MARKDOWN
        return TextFormat.PLAIN
