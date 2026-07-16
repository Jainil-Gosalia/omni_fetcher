"""The canonical ``elasticsearch`` fetcher -- bounded search connector for v1.

Queries an Elasticsearch index through the v1 contract: a query yields one
``Result`` whose tree is a single ``kind="search_results"`` container node
whose children are the matching documents. Each document child is a
``kind="json_document"`` node carrying one ``Text`` atom (``format=CODE``,
the document's ``_source`` serialised as JSON -- no field loss, mirroring
``http_json``'s "core doesn't transform" rule; there is no ``JSONData`` atom
in v1's closed atom vocabulary). Query-level facts (index, query, doc count,
took_ms, total_hits) live on the container's ``source_extra["elasticsearch"]``;
per-document facts (doc id, index, score) live on each document node's own
``source_extra["elasticsearch"]`` -- mirrors the ``confluence`` connector's
space/page split.

Configuration travels in the URI: ``es://<host>[:<port>]/<index>`` (default
port 9200) with ``?q=<lucene-query>`` (default: match all), ``?size=<n>``
bounding the total documents returned (default 100), ``?scroll=<timeout>``
(default ``1m``) for the scroll cursor TTL, and ``?user=&password=`` or
``?api_key=`` for auth -- all URI-first per D8, the same channel that
survives orchestrator routing and the CLI.

Internally the fetcher drives Elasticsearch's scroll API to page through
large result sets without loading everything into memory at once, stopping
as soon as ``?size=`` documents have been collected. The scroll cursor is
cleared in a ``finally`` regardless of how the fetch ends.

``elasticsearch-py`` is optional (the ``elasticsearch`` extra): this module
imports without it, ``builtin_registry()`` skips the source when it is
missing, and direct use yields a typed ``UNSUPPORTED`` naming the extra. All
client access flows through a narrow ``_Client`` protocol built by the
``_make_client`` seam, so tests script a fake and never touch a cluster.

This is a **bounded** fetcher (D1): it implements only ``stream()``, which
yields exactly one ``Result`` (the container), matching the ``confluence``
connector's page/space pattern -- not the per-message streaming pattern used
by Kafka/Redis/WebSocket/SSE. Expected failures (missing index, malformed
query, connection/scroll failure) are returned as typed ``Error`` values,
never raised; a query that produced no documents is an honest
``Error(NOT_FOUND)``, never a silent empty success. If any documents were
collected before a scroll failure, the fetcher returns a ``Partial``
carrying what was built plus a typed gap, rather than discarding progress.
"""

from __future__ import annotations

import importlib.util
import json
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol
from urllib.parse import parse_qs

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Error, Result, error, from_exception, gap, partial, success
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which all descriptive ``elasticsearch`` fields are stored.
SOURCE_NAMESPACE = "elasticsearch"

# Advisory semantic ``kind`` values for the nodes this connector emits.
RESULTS_KIND = "search_results"
DOCUMENT_KIND = "json_document"

# Whether the optional elasticsearch client is importable (the ``elasticsearch`` extra).
ELASTICSEARCH_AVAILABLE = importlib.util.find_spec("elasticsearch") is not None

_SCHEME = "es://"
_DEFAULT_PORT = 9200
_DEFAULT_SIZE = 100
_DEFAULT_SCROLL_TIMEOUT = "1m"

# Per-request page size cap for scroll pages -- independent of the overall
# ``?size=`` result limit, which only bounds how many documents are kept.
_SCROLL_PAGE_SIZE = 1000


class _ElasticsearchNotFoundError(Exception):
    """Raised by the client seam when the target index does not exist."""


class _ElasticsearchQueryError(Exception):
    """Raised by the client seam when the query is malformed (4xx, not 404)."""


class _Client(Protocol):
    """The narrow client protocol the fetcher drives.

    Implemented by the production ``elasticsearch-py`` adapter and by test
    fakes; the fetcher itself never touches ``elasticsearch-py`` directly,
    so unit tests need no cluster.
    """

    async def search(
        self, *, index: str, q: Optional[str], size: int, scroll: str
    ) -> Dict[str, Any]: ...

    async def scroll(self, *, scroll_id: str, scroll: str) -> Dict[str, Any]: ...

    async def clear_scroll(self, *, scroll_id: str) -> None: ...

    async def close(self) -> None: ...


class _ElasticsearchSpec:
    """Parsed ``es://`` routing decision."""

    def __init__(
        self,
        host: str,
        port: int,
        index: str,
        query: Optional[str],
        size: int,
        scroll_timeout: str,
        user: Optional[str],
        password: Optional[str],
        api_key: Optional[str],
    ) -> None:
        self.host = host
        self.port = port
        self.index = index
        self.query = query
        self.size = size
        self.scroll_timeout = scroll_timeout
        self.user = user
        self.password = password
        self.api_key = api_key


def _parse_uri(uri: str) -> _ElasticsearchSpec:
    """Parse an ``es://`` URI into a spec, raising ``ValueError`` when bad."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not an es:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    location, _, query_string = remainder.partition("?")
    host_part, _, index = location.partition("/")
    if not host_part or not index or "/" in index:
        raise ValueError(f"es:// URI must be es://host[:port]/index: {uri}")

    if ":" in host_part:
        host, port_str = host_part.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"es:// port must be numeric: {port_str}")
    else:
        host = host_part
        port = _DEFAULT_PORT

    params = parse_qs(query_string)
    query = params.get("q", [None])[0]

    raw_size = params.get("size", [str(_DEFAULT_SIZE)])[0]
    try:
        size = int(raw_size)
    except ValueError:
        raise ValueError(f"size= must be an integer: {raw_size}")
    if size <= 0:
        raise ValueError(f"size= must be > 0: {raw_size}")

    scroll_timeout = params.get("scroll", [_DEFAULT_SCROLL_TIMEOUT])[0]
    user = params.get("user", [None])[0]
    password = params.get("password", [None])[0]
    api_key = params.get("api_key", [None])[0]

    return _ElasticsearchSpec(
        host=host,
        port=port,
        index=index,
        query=query,
        size=size,
        scroll_timeout=scroll_timeout,
        user=user,
        password=password,
        api_key=api_key,
    )


def _extract_total(value: Any) -> Optional[int]:
    """Extract the integer hit count from ES's ``hits.total`` (object or legacy int)."""
    if isinstance(value, dict):
        raw = value.get("value")
        return raw if isinstance(raw, int) else None
    if isinstance(value, int):
        return value
    return None


class ElasticsearchFetcher(BaseFetcher):
    """
    Bounded Elasticsearch search fetcher for the v1 contract
    ===============================================
    Queries an index via Elasticsearch's scroll API and yields one
    ``Result`` whose tree is a ``"search_results"`` container node with one
    ``"json_document"`` child per matching document (up to ``?size=``).
    ``fetch()`` (base sugar) equals this single yielded item. All client
    access goes through the ``_make_client`` seam.
    ===============================================
    NOTE:
        1. Without the ``elasticsearch`` extra the stream yields one typed
           ``UNSUPPORTED`` naming the extra; nothing raises.
        2. A query matching zero documents is a typed ``Error(NOT_FOUND)``
           -- never a silent empty success.
        3. A scroll failure after some documents were already collected
           returns a ``Partial`` (built documents + a typed gap), not a bare
           error -- progress is never discarded.

    Attributes
    ----------
        timeout:
            Per-request transport timeout in seconds for the production
            client.

    Methods
    -------
        can_handle:
        stream:
    """

    name = SOURCE_NAMESPACE

    def __init__(self, timeout: float = 30.0) -> None:
        """
        Create an Elasticsearch fetcher

        Parameters
        ----------
            timeout:
                Per-request transport timeout in seconds for the underlying
                client.
        """
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI names an Elasticsearch source

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for ``es://`` URIs.
        """
        return uri.startswith(_SCHEME)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Query an index and yield exactly one ``Result`` (the container)

        NOTE:
            1. ``zoom`` is accepted for contract conformance; central
               pruning in ``fetch()`` still applies to the yielded tree.

        Parameters
        ----------
            uri:
                The ``es://host[:port]/index?...`` source URI.
            auth:
                Ignored; auth travels in the URI query string (D8).
            zoom:
                Accepted; not acted on directly.

        Return
        ------
            results:
                A bounded async iterator yielding exactly one ``Result``.
        """
        del zoom
        yield await self._fetch_all(uri, auth)

    async def _fetch_all(self, uri: str, auth: Optional[AuthCredential]) -> Result:
        """Query the index end to end and build the single ``Result``."""
        if not ELASTICSEARCH_AVAILABLE:
            return error(
                kind=ErrorKind.UNSUPPORTED,
                message=(
                    "elasticsearch-py is not installed; install the 'elasticsearch' "
                    "extra (pip install 'omni_fetcher[elasticsearch]') to use es:// sources"
                ),
                locator=uri,
            )

        try:
            spec = _parse_uri(uri)
        except ValueError as exc:
            return from_exception(
                exc,
                kind=ErrorKind.INVALID_INPUT,
                message="invalid es:// URI",
                locator=uri,
            )

        try:
            client = await self._make_client(spec, auth)
        except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
            return from_exception(
                exc,
                kind=ErrorKind.TRANSIENT,
                message="could not connect to Elasticsearch",
                locator=uri,
            )

        scroll_id: Optional[str] = None
        try:
            batch_size = min(spec.size, _SCROLL_PAGE_SIZE)
            try:
                page = await client.search(
                    index=spec.index, q=spec.query, size=batch_size, scroll=spec.scroll_timeout
                )
            except _ElasticsearchNotFoundError as exc:
                return from_exception(
                    exc,
                    kind=ErrorKind.NOT_FOUND,
                    message="elasticsearch index not found",
                    locator=uri,
                )
            except _ElasticsearchQueryError as exc:
                return from_exception(
                    exc,
                    kind=ErrorKind.INVALID_INPUT,
                    message="malformed elasticsearch query",
                    locator=uri,
                )
            except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
                return from_exception(
                    exc,
                    kind=ErrorKind.TRANSIENT,
                    message="elasticsearch search failed",
                    locator=uri,
                )

            documents: List[CompositionNode] = []
            total_hits: Optional[int] = None
            took_ms: Optional[int] = None
            scroll_error: Optional[Error] = None

            while True:
                scroll_id = page.get("_scroll_id")
                hits_block = page.get("hits") or {}
                hits = hits_block.get("hits") or []
                total_hits = _extract_total(hits_block.get("total"))
                took_ms = page.get("took")
                if not hits:
                    break
                for hit in hits:
                    if len(documents) >= spec.size:
                        break
                    documents.append(self._document_node(uri, hit))
                if len(documents) >= spec.size or scroll_id is None:
                    break
                try:
                    page = await client.scroll(scroll_id=scroll_id, scroll=spec.scroll_timeout)
                except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
                    scroll_error = from_exception(
                        exc,
                        kind=ErrorKind.TRANSIENT,
                        message="elasticsearch scroll failed",
                        locator=uri,
                    )
                    break

            if not documents:
                if scroll_error is not None:
                    return scroll_error
                return error(
                    kind=ErrorKind.NOT_FOUND,
                    message=f"no documents matched query in index {spec.index!r}",
                    locator=uri,
                )

            container = build_node(
                kind=RESULTS_KIND,
                children=documents,
                source_url=uri,
                source_namespace=SOURCE_NAMESPACE,
                source_fields={
                    "index": spec.index,
                    "query": spec.query,
                    "doc_count": len(documents),
                    "total_hits": total_hits,
                    "took_ms": took_ms,
                },
            )
            if scroll_error is not None:
                return partial(
                    container,
                    [gap(kind=scroll_error.kind, locator=uri, detail=scroll_error.message)],
                )
            return success(container)
        finally:
            if scroll_id is not None:
                try:
                    await client.clear_scroll(scroll_id=scroll_id)
                except Exception:  # noqa: BLE001 - best-effort cursor release
                    pass
            await client.close()

    def _document_node(self, uri: str, hit: Dict[str, Any]) -> CompositionNode:
        """Map one ES hit onto a canonical ``"json_document"`` node."""
        source_doc = hit.get("_source", {})
        content = json.dumps(source_doc, ensure_ascii=False, indent=2, default=str)
        return build_node(
            kind=DOCUMENT_KIND,
            atoms=[Text(content=content, format=TextFormat.CODE)],
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "doc_id": hit.get("_id"),
                "index": hit.get("_index"),
                "score": hit.get("_score"),
            },
        )

    async def _make_client(
        self,
        spec: _ElasticsearchSpec,
        auth: Optional[AuthCredential],
    ) -> _Client:
        """Build a live client for the spec (the cluster seam).

        Production wraps ``elasticsearch-py`` in the narrow ``_Client``
        protocol; tests replace this method with a scripted fake. Only ever
        called when ``ELASTICSEARCH_AVAILABLE`` (or under a test seam).
        """
        return await _ElasticsearchAdapter.create(spec, auth, self.timeout)


class _ElasticsearchAdapter:
    """Production ``_Client`` built on ``elasticsearch-py`` (integration-tested only).

    Unit suites never construct this; it exists so the fetcher's protocol
    has exactly one production implementation. Translates the client's
    typed HTTP exceptions into the fetcher's own ``_ElasticsearchNotFoundError``
    / ``_ElasticsearchQueryError`` so ``_fetch_all`` never imports
    ``elasticsearch`` directly.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    async def create(
        cls,
        spec: _ElasticsearchSpec,
        auth: Optional[AuthCredential],
        timeout: float,
    ) -> "_ElasticsearchAdapter":
        """Build an ``AsyncElasticsearch`` client for the spec."""
        from elasticsearch import AsyncElasticsearch  # imported only with the extra

        del auth  # auth travels in the URI query string (D8); nothing extra to inject.
        client_kwargs: Dict[str, Any] = {}
        if spec.api_key:
            client_kwargs["api_key"] = spec.api_key
        elif spec.user:
            client_kwargs["basic_auth"] = (spec.user, spec.password or "")

        client = AsyncElasticsearch(
            f"http://{spec.host}:{spec.port}",
            request_timeout=timeout,
            **client_kwargs,
        )
        return cls(client)

    async def search(
        self, *, index: str, q: Optional[str], size: int, scroll: str
    ) -> Dict[str, Any]:
        from elasticsearch import BadRequestError, NotFoundError  # imported only with the extra

        try:
            response = await self._client.search(index=index, q=q, size=size, scroll=scroll)
        except NotFoundError as exc:
            raise _ElasticsearchNotFoundError(str(exc)) from exc
        except BadRequestError as exc:
            raise _ElasticsearchQueryError(str(exc)) from exc
        return dict(response)

    async def scroll(self, *, scroll_id: str, scroll: str) -> Dict[str, Any]:
        response = await self._client.scroll(scroll_id=scroll_id, scroll=scroll)
        return dict(response)

    async def clear_scroll(self, *, scroll_id: str) -> None:
        await self._client.clear_scroll(scroll_id=scroll_id)

    async def close(self) -> None:
        await self._client.close()
