"""The canonical ``http_json`` connector for the OmniFetcher v1 contract.

Fetches a JSON document over HTTP(S) and emits it as a single canonical
``CompositionNode`` (advisory ``kind`` ``"json"``) wrapped in a ``Result``.

The JSON payload is content, so it lives in atoms: the body is always
serialised into a ``Text`` atom (``TextFormat.CODE``); when the payload is an
array of flat (scalar-valued) JSON objects it is *additionally* represented
as a ``Table`` atom so tabular consumers can use it directly. Everything that
*describes* the fetch -- HTTP status, content type, resolved URL -- is
descriptive metadata and lives in the ``Metadata`` core plus the namespaced
``source_extra["http_json"]`` mapping, never inline on an atom.

Expected failures are returned as typed ``Error`` results, never raised: HTTP
status codes map onto the error taxonomy the same way ``http_url`` maps them
(401 -> auth, 403 -> permission, 404 -> not-found, 429 -> rate-limited,
5xx/network -> transient, other 4xx -> invalid-input), and a body that is not
valid JSON is a ``PARSE_ERROR``.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

import httpx

from omni_fetcher.v1.atoms import Table, Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential, NormalizedAuthResolver
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.result import Error, Result, error, success
from omni_fetcher.v1.zoom import ZoomSpec

# The source namespace under which this connector files its descriptive
# fields in ``Metadata.source_extra``.
SOURCE_NAMESPACE = "http_json"

# Advisory semantic label for the node this connector emits.
NODE_KIND = "json"

# Default transport timeout (seconds) for a single request.
DEFAULT_TIMEOUT = 30.0


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
    return ErrorKind.INVALID_INPUT


def _is_flat_record(value: Any) -> bool:
    """Report whether ``value`` is a JSON object of scalar-only values."""
    if not isinstance(value, dict):
        return False
    for cell in value.values():
        if isinstance(cell, (dict, list)):
            return False
    return True


def _as_table(data: Any) -> Optional[Table]:
    """Build a ``Table`` atom when ``data`` is an array of flat records.

    Returns ``None`` when the payload is not a non-empty list whose every
    element is a flat (scalar-valued) JSON object -- the only shape that maps
    losslessly onto a single tabular grid. Column order is the union of keys
    in first-seen order; missing cells are ``None``.
    """
    if not isinstance(data, list) or not data:
        return None
    if not all(_is_flat_record(item) for item in data):
        return None

    headers: list[str] = []
    seen: set[str] = set()
    for record in data:
        for key in record:
            if key not in seen:
                seen.add(key)
                headers.append(key)

    rows: list[list[Any]] = [[record.get(header) for header in headers] for record in data]
    return Table(headers=headers, rows=rows)


class HTTPJSONConnector(BaseFetcher):
    """
    Canonical HTTP-JSON connector
    ===============================================
    Fetches a JSON document over HTTP(S) and streams it as one canonical
    ``CompositionNode`` (advisory ``kind`` ``"json"``). The JSON body becomes
    a ``Text`` atom (and a ``Table`` atom when it is an array of flat
    records); HTTP status, content type, and the resolved URL are filed as
    descriptive metadata under ``source_extra["http_json"]``. Expected
    failures (HTTP status, non-JSON body, network errors) are returned as
    typed ``Error`` results, never raised.
    ===============================================
    NOTE:
        1. This connector implements only ``stream()``; ``fetch()`` is the
           inherited base sugar that collects the bounded stream.
        2. Credentials are passed per call via ``auth`` and resolved
           transiently into request headers; nothing is stored on the
           instance.

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
        Create an HTTP-JSON connector

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
        Report whether a URI looks like a JSON HTTP endpoint

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` when ``uri`` is an HTTP(S) URL that looks like a
                JSON/API endpoint.
        """
        if not (uri.startswith("http://") or uri.startswith("https://")):
            return False
        lowered = uri.lower()
        return (
            lowered.endswith(".json")
            or "api" in lowered
            or "json" in lowered
            or "graphql" in lowered
        )

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the JSON document at a URI as one canonical result

        Performs a single bounded GET. On a 2xx response with a JSON body it
        yields exactly one ``Success`` whose tree is a ``CompositionNode``
        (kind ``"json"``) carrying a ``Text`` atom of the serialised JSON
        (and a ``Table`` atom when the body is an array of flat records),
        with HTTP status, content type, and resolved URL in
        ``source_extra["http_json"]``. A non-2xx status yields a typed
        ``Error`` (see the status mapping); a body that cannot be parsed as
        JSON yields ``Error(PARSE_ERROR)``; a network/timeout failure yields
        ``Error(TRANSIENT)``.

        NOTE:
            1. Expected failures are yielded as ``Error`` results, never
               raised.
            2. ``zoom`` is accepted for contract conformance; this connector
               emits a single natural-granularity node and does not further
               decompose the JSON, so ``zoom`` does not change the output.

        Parameters
        ----------
            uri:
                The HTTP(S) URL returning a JSON document.
            auth:
                The per-call credential, or ``None`` for unauthenticated
                access. Resolved transiently into request headers.
            zoom:
                Optional per-atom-type zoom spec; accepted but not acted on.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result`` (a
                ``Success`` or a typed ``Error``).
        """
        headers = self._auth_resolver.resolve_headers(auth)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(uri, headers=headers)
        except httpx.HTTPError as exc:
            yield error(
                kind=ErrorKind.TRANSIENT,
                message=f"request failed: {exc}",
                locator=uri,
            )
            return

        result = self._build_result(response, uri)
        yield result

    def _build_result(self, response: httpx.Response, uri: str) -> Result:
        """Turn an HTTP response into a canonical ``Result``."""
        resolved_url = str(response.url)
        status_code = response.status_code

        if status_code >= 400:
            return Error(
                kind=_status_to_error_kind(status_code),
                message=f"HTTP {status_code} for {resolved_url}",
                locator=resolved_url,
            )

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError, UnicodeError) as exc:
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=f"response body is not valid JSON: {exc}",
                locator=resolved_url,
            )

        content_type = response.headers.get("content-type", "application/json")
        atoms: list[Any] = [
            Text(
                content=json.dumps(data, ensure_ascii=False, indent=2),
                format=TextFormat.CODE,
            )
        ]
        table = _as_table(data)
        if table is not None:
            atoms.append(table)

        node = build_node(
            kind=NODE_KIND,
            atoms=atoms,
            source_url=resolved_url,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "status": status_code,
                "content_type": content_type,
                "url": resolved_url,
            },
        )
        return success(node)
