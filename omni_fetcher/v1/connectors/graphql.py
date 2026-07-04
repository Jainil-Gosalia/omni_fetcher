"""GraphQL connector for the OmniFetcher v1 canonical contract.

A GraphQL endpoint is queried with a single POST and its JSON response is
emitted as one canonical ``CompositionNode`` whose advisory ``kind`` is
``"graphql_response"``. The connector overrides only ``stream()``;
``fetch()`` sugar is inherited from :class:`BaseFetcher`.

GraphQL is unusual in that a *successful* HTTP 200 response may still carry
an ``errors`` array (per the GraphQL spec). The contract here never hides
those:

- ``errors`` present with no ``data`` -> a typed ``error`` result.
- ``data`` present *and* ``errors`` present -> a ``partial`` result whose
  tree carries the partial data and whose ``gaps`` record every GraphQL
  error. Partial data is never surfaced as a silent ``success``.
- ``data`` present with no ``errors`` -> a ``success``.

The query is carried on the URI's query string (``query``, ``variables`` --
a JSON object -- and ``operationName``); the endpoint is the URI with those
parameters stripped. This keeps ``stream(uri, *, auth, zoom)`` faithful to
the contract's signature: everything the call needs lives in the URI plus
the per-call ``auth`` credential.

Source-specific descriptive fields (HTTP status, the query text, operation
name, variables, GraphQL ``extensions``) live in the namespaced
``source_extra["graphql"]`` mapping, never inline on a content atom.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx

from omni_fetcher.v1.atoms import Table, Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential, NormalizedAuthResolver
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Error,
    Gap,
    Result,
    error,
    from_exception,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# Advisory semantic ``kind`` for the node a GraphQL response maps onto.
GRAPHQL_KIND = "graphql_response"

# Source namespace for descriptive fields placed in ``source_extra``.
GRAPHQL_NAMESPACE = "graphql"

# Default operation when the URI carries no ``query`` parameter; a harmless
# introspection that confirms the endpoint is reachable.
_DEFAULT_QUERY = "{ __typename }"

# Query-string parameter names recognised on the URI.
_PARAM_QUERY = "query"
_PARAM_VARIABLES = "variables"
_PARAM_OPERATION = "operationName"


class GraphQLConnector(BaseFetcher):
    """
    GraphQL endpoint connector for the v1 contract
    ===============================================
    Executes one GraphQL operation against an endpoint and emits the JSON
    response as a single canonical ``CompositionNode`` (advisory ``kind``
    ``"graphql_response"``). The operation is read from the URI's query
    string; optional credentials are injected per call via ``auth``.
    ===============================================
    NOTE:
        1. A GraphQL ``errors`` array on an HTTP 200 response is never
           hidden: errors with no data become an ``error`` result, and data
           alongside errors becomes a ``partial`` with one gap per error.
        2. HTTP transport/status failures map onto the typed error taxonomy
           (auth, permission, not-found, rate-limit, transient).
        3. Credentials are used transiently and never stored on the
           instance.

    Methods
    -------
        can_handle:
        stream:
    """

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
                ``True`` if the URI looks like a GraphQL endpoint.
        """
        lowered = uri.lower()
        return "graphql" in lowered or "gql" in lowered

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical result of one GraphQL operation

        Parses the operation from the URI's query string, executes a single
        POST against the endpoint, and yields exactly one ``Result``: a
        ``success`` for clean data, a ``partial`` when data coexists with a
        GraphQL ``errors`` array, or a typed ``error`` for GraphQL errors
        without data or for an HTTP/transport failure.

        NOTE:
            1. The stream is bounded: it yields one item and terminates, so
               the inherited ``fetch()`` returns that item directly.

        Parameters
        ----------
            uri:
                The GraphQL endpoint URI, optionally carrying ``query``,
                ``variables`` (a JSON object) and ``operationName`` query
                parameters.
            auth:
                The per-call credential, or ``None`` for an unauthenticated
                endpoint.
            zoom:
                Optional zoom spec; the natural granularity (one response
                node) is used and ``zoom`` is accepted but not subdivided.

        Return
        ------
            results:
                An async iterator yielding a single ``Result`` for the
                operation.
        """
        try:
            endpoint, payload = self._parse_uri(uri)
        except ValueError as exc:
            yield from_exception(
                exc,
                kind=ErrorKind.INVALID_INPUT,
                message="invalid GraphQL request URI",
                locator=uri,
            )
            return

        headers = {"Content-Type": "application/json"}
        headers.update(NormalizedAuthResolver().resolve_headers(auth))

        try:
            response = await self._post(endpoint, payload, headers)
        except httpx.HTTPError as exc:
            yield from_exception(
                exc,
                kind=ErrorKind.TRANSIENT,
                message="GraphQL request failed",
                locator=endpoint,
            )
            return

        status_error = self._status_error(response, endpoint)
        if status_error is not None:
            yield status_error
            return

        yield self._build_result(uri, endpoint, payload, response)

    def _parse_uri(self, uri: str) -> tuple[str, dict[str, Any]]:
        """Split a URI into (endpoint, GraphQL POST payload).

        The ``query``, ``variables`` and ``operationName`` query-string
        parameters define the operation; everything else stays on the
        endpoint. ``variables`` must be a JSON object when present.
        """
        parts = urlsplit(uri)
        params = parse_qs(parts.query, keep_blank_values=True)

        recognised = {_PARAM_QUERY, _PARAM_VARIABLES, _PARAM_OPERATION}
        passthrough = {
            key: values
            for key, values in params.items()
            if key not in recognised
        }
        endpoint = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(passthrough, doseq=True),
                parts.fragment,
            )
        )

        query = self._single(params, _PARAM_QUERY) or _DEFAULT_QUERY
        operation = self._single(params, _PARAM_OPERATION)
        variables = self._parse_variables(params)

        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        if operation:
            payload["operationName"] = operation
        return endpoint, payload

    @staticmethod
    def _single(params: dict[str, list[str]], key: str) -> Optional[str]:
        """Return the first value for a query-string key, if any."""
        values = params.get(key)
        if not values:
            return None
        return values[0]

    @classmethod
    def _parse_variables(
        cls, params: dict[str, list[str]]
    ) -> Optional[dict[str, Any]]:
        """Decode the ``variables`` JSON object from the query string."""
        raw = cls._single(params, _PARAM_VARIABLES)
        if raw is None:
            return None
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"'variables' is not valid JSON: {exc}"
            ) from exc
        if not isinstance(decoded, dict):
            raise ValueError("'variables' must be a JSON object")
        return decoded

    async def _post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        """Execute the single GraphQL POST and return the raw response."""
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True
        ) as client:
            return await client.post(
                endpoint, json=payload, headers=headers
            )

    @staticmethod
    def _status_error(
        response: httpx.Response, endpoint: str
    ) -> Optional[Error]:
        """Map a non-2xx HTTP status onto a typed error, else ``None``.

        GraphQL conventionally returns 200 even for query-level errors, so a
        non-2xx status is a transport-level failure mapped onto the taxonomy
        the same way any HTTP source would map it.
        """
        status = response.status_code
        if 200 <= status < 300:
            return None
        if status == 401:
            kind = ErrorKind.AUTH_FAILED
        elif status == 403:
            kind = ErrorKind.PERMISSION_DENIED
        elif status == 404:
            kind = ErrorKind.NOT_FOUND
        elif status == 429:
            kind = ErrorKind.RATE_LIMITED
        elif 500 <= status < 600:
            kind = ErrorKind.TRANSIENT
        else:
            kind = ErrorKind.TRANSIENT
        return error(
            kind=kind,
            message=f"GraphQL endpoint returned HTTP {status}",
            locator=endpoint,
        )

    def _build_result(
        self,
        uri: str,
        endpoint: str,
        payload: dict[str, Any],
        response: httpx.Response,
    ) -> Result:
        """Map a 2xx GraphQL response body onto a canonical ``Result``.

        Decodes the JSON body and routes on the presence of ``data`` and
        ``errors``: clean data is a ``success``, data-with-errors is a
        ``partial`` with one gap per GraphQL error, and errors-without-data
        is a typed ``error``. An undecodable body is a parse error.
        """
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            return from_exception(
                exc,
                kind=ErrorKind.PARSE_ERROR,
                message="GraphQL response was not valid JSON",
                locator=endpoint,
            )

        if not isinstance(body, dict):
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message="GraphQL response was not a JSON object",
                locator=endpoint,
            )

        data = body.get("data")
        errors = body.get("errors")
        extensions = body.get("extensions")

        has_errors = bool(errors)
        has_data = data is not None

        if has_errors and not has_data:
            # GraphQL errors with no data: a failed operation.
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=self._summarise_errors(errors),
                locator=endpoint,
            )

        node = self._build_node(
            uri=uri,
            endpoint=endpoint,
            payload=payload,
            response=response,
            data=data,
            errors=errors,
            extensions=extensions,
        )

        if has_errors:
            # Partial data alongside GraphQL errors: surface both.
            return partial(node, self._error_gaps(errors, endpoint))
        return success(node)

    def _build_node(
        self,
        *,
        uri: str,
        endpoint: str,
        payload: dict[str, Any],
        response: httpx.Response,
        data: Any,
        errors: Any,
        extensions: Any,
    ) -> CompositionNode:
        """Assemble the canonical node for a GraphQL response body."""
        atoms: list[Any] = [
            Text(
                content=self._dump_json(data),
                format=TextFormat.CODE,
                language="json",
            )
        ]
        table = self._table_for(data)
        if table is not None:
            atoms.append(table)

        source_fields: dict[str, Any] = {
            "status_code": response.status_code,
            "query": payload.get("query"),
        }
        if "operationName" in payload:
            source_fields["operation_name"] = payload["operationName"]
        if "variables" in payload:
            source_fields["variables"] = payload["variables"]
        if extensions is not None:
            source_fields["extensions"] = extensions
        if errors:
            source_fields["errors"] = errors

        return build_node(
            kind=GRAPHQL_KIND,
            atoms=atoms,
            source_url=endpoint,
            source_namespace=GRAPHQL_NAMESPACE,
            source_fields=source_fields,
        )

    @staticmethod
    def _dump_json(data: Any) -> str:
        """Render GraphQL data as deterministic, readable JSON text."""
        return json.dumps(
            data, indent=2, sort_keys=True, ensure_ascii=False
        )

    @classmethod
    def _table_for(cls, data: Any) -> Optional[Table]:
        """Build a ``Table`` atom when ``data`` is a clean list of records.

        GraphQL ``data`` is an object keyed by the queried fields. When a
        top-level field holds a non-empty list of flat (scalar-valued)
        objects sharing a key set, that list is genuinely tabular and is
        offered as a ``Table`` atom in addition to the JSON text. Anything
        else (nested objects, ragged rows) is left to the JSON atom.
        """
        if not isinstance(data, dict):
            return None
        for value in data.values():
            table = cls._rows_to_table(value)
            if table is not None:
                return table
        return None

    @staticmethod
    def _rows_to_table(value: Any) -> Optional[Table]:
        """Convert a list of flat scalar dicts into a ``Table``, else None."""
        if not isinstance(value, list) or not value:
            return None
        if not all(isinstance(row, dict) for row in value):
            return None

        headers = list(value[0].keys())
        if not headers:
            return None
        header_set = set(headers)

        rows: list[list[Any]] = []
        for row in value:
            if set(row.keys()) != header_set:
                return None
            cells = [row[key] for key in headers]
            if any(isinstance(cell, (dict, list)) for cell in cells):
                return None
            rows.append(cells)
        return Table(headers=headers, rows=rows)

    @staticmethod
    def _summarise_errors(errors: Any) -> str:
        """Render a GraphQL ``errors`` array into one human-readable line."""
        if not isinstance(errors, list):
            return "GraphQL response carried errors"
        messages: list[str] = []
        for item in errors:
            if isinstance(item, dict):
                message = item.get("message")
                messages.append(str(message) if message else "unknown error")
            else:
                messages.append(str(item))
        joined = "; ".join(messages) if messages else "unknown error"
        return f"GraphQL errors: {joined}"

    @classmethod
    def _error_gaps(cls, errors: Any, endpoint: str) -> list[Gap]:
        """Map each GraphQL error into a typed ``Gap`` for a partial tree."""
        if not isinstance(errors, list) or not errors:
            return [
                gap(
                    kind=ErrorKind.PARSE_ERROR,
                    locator=endpoint,
                    detail=cls._summarise_errors(errors),
                )
            ]
        gaps: list[Gap] = []
        for item in errors:
            if isinstance(item, dict):
                message = item.get("message")
                detail = str(message) if message else "unknown error"
            else:
                detail = str(item)
            gaps.append(
                gap(
                    kind=ErrorKind.PARSE_ERROR,
                    locator=endpoint,
                    detail=detail,
                )
            )
        return gaps
