"""The canonical ``mongodb`` connectors for the v1 contract (v1.16).

Two connectors over the shared document-store spec (``_document_store``), one on
each seam:

- **``mongodb://<host>/<db>.<collection>?query=&limit=&projection=``** (bounded):
  runs a ``find`` and returns one ``kind="documents"`` container whose children
  are ``kind="json_document"`` nodes (each document serialised as a ``Text`` atom,
  ``format=CODE``). Mirrors the ``elasticsearch`` shape.
- **``mongodb+changestream://<host>/<db>.<collection>``** (unbounded): watches the
  collection's change stream and emits one ``kind="change"`` node per
  insert/update/delete/replace, following the ``postgres-cdc`` pattern.

Credentials are a per-call ``BasicAuth`` (MCP injects
``OMNI_FETCHER_MONGODB_USERNAME`` / ``_PASSWORD``) overriding any URI userinfo;
absent both, the connection is anonymous. ``motor`` is optional (the ``mongodb``
extra): these modules import without it, ``builtin_registry()`` skips the sources
when it is missing, and direct use yields a typed ``UNSUPPORTED``. Expected
failures map onto the taxonomy by the driver's error code/type and are never
raised. All database access flows through the ``_query`` / ``_watch`` seams so
tests script fakes and never touch a live MongoDB.
"""

from __future__ import annotations

import importlib.util
import json
from typing import Any, AsyncGenerator, AsyncIterator, Optional
from urllib.parse import parse_qs, urlsplit

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential, BasicAuth
from omni_fetcher.v1.connectors._document_store import (
    build_documents_result,
    resolve_doc_cap,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import SequenceCounter, build_node, now_utc, stamp_temporal
from omni_fetcher.v1.result import Error, Result, error, from_exception, success
from omni_fetcher.v1.zoom import ZoomSpec

SOURCE_NAMESPACE = "mongodb"
CHANGE_KIND = "change"

# Whether the optional motor client is importable (the ``mongodb`` extra).
MONGODB_AVAILABLE = importlib.util.find_spec("motor") is not None

_QUERY_SCHEME = "mongodb"
_CHANGESTREAM_SCHEME = "mongodb+changestream"
_DEFAULT_PORT = 27017


class _MongoSpec:
    """Parsed MongoDB routing decision (connection + namespace + options)."""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        collection: str,
        uri_user: Optional[str],
        uri_password: Optional[str],
        query: Optional[str],
        projection: Optional[str],
        limit: Optional[str],
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.collection = collection
        self.uri_user = uri_user
        self.uri_password = uri_password
        self.query = query
        self.projection = projection
        self.limit = limit


def _parse_uri(uri: str, scheme: str) -> _MongoSpec:
    """Parse a ``<scheme>://[user:pass@]host[:port]/db.collection?...`` URI."""
    parts = urlsplit(uri)
    if parts.scheme != scheme:
        raise ValueError(f"not a {scheme}:// URI: {uri}")
    host = parts.hostname
    namespace = parts.path.lstrip("/")
    database, _, collection = namespace.partition(".")
    if not host or not database or not collection:
        raise ValueError(f"{scheme}:// URI must be {scheme}://host[:port]/db.collection: {uri}")
    params = parse_qs(parts.query)
    return _MongoSpec(
        host=host,
        port=parts.port or _DEFAULT_PORT,
        database=database,
        collection=collection,
        uri_user=parts.username,
        uri_password=parts.password,
        query=params.get("query", [None])[0],
        projection=params.get("projection", [None])[0],
        limit=params.get("limit", [None])[0],
    )


def _resolve_credentials(
    spec: _MongoSpec, auth: Optional[AuthCredential]
) -> tuple[Optional[str], Optional[str]]:
    """Resolve ``(user, password)``: BasicAuth wins, then URI userinfo, then anon."""
    if isinstance(auth, BasicAuth):
        return auth.username, auth.password
    return spec.uri_user, spec.uri_password


def _map_mongo_error(exc: BaseException) -> ErrorKind:
    """Map a pymongo/motor exception onto the v1 error taxonomy."""
    code = getattr(exc, "code", None)
    if code == 13:  # Unauthorized
        return ErrorKind.PERMISSION_DENIED
    if code == 18:  # AuthenticationFailed
        return ErrorKind.AUTH_FAILED
    name = type(exc).__name__
    if any(
        token in name
        for token in ("ServerSelection", "ConnectionFailure", "NetworkTimeout", "AutoReconnect")
    ):
        return ErrorKind.TRANSIENT
    if "OperationFailure" in name:
        return ErrorKind.INVALID_INPUT
    return ErrorKind.TRANSIENT


def _make_client(spec: _MongoSpec, user: Optional[str], password: Optional[str]) -> Any:
    """Build a motor client from the spec + resolved credentials (the seam)."""
    from motor.motor_asyncio import AsyncIOMotorClient

    kwargs: dict[str, Any] = {"host": spec.host, "port": spec.port}
    if user is not None:
        kwargs["username"] = user
        kwargs["password"] = password
    return AsyncIOMotorClient(**kwargs)


class MongoQueryConnector(BaseFetcher):
    """
    Canonical v1 connector for a MongoDB ``find`` query (bounded)
    ===============================================
    Runs a ``find`` and emits one ``kind="documents"`` container of
    ``json_document`` children. Descriptive fields live in
    ``source_extra["mongodb"]``.
    ===============================================
    NOTE:
        1. Implements only ``stream()`` (yields one container); ``fetch()`` is
           inherited.
        2. ``motor`` is optional (the ``mongodb`` extra); without it the
           connector yields a typed ``UNSUPPORTED``.

    Methods
    -------
        stream:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Report whether ``uri`` is a (non-changestream) ``mongodb://`` URI."""
        return uri.startswith("mongodb://")

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """Run a ``find`` and yield exactly one ``documents`` container ``Result``."""
        del zoom

        if not MONGODB_AVAILABLE:
            yield _unsupported(uri)
            return

        try:
            spec = _parse_uri(uri, _QUERY_SCHEME)
            doc_cap = resolve_doc_cap(spec.limit)
            filter_doc = json.loads(spec.query) if spec.query else {}
            projection = json.loads(spec.projection) if spec.projection else None
        except (ValueError, json.JSONDecodeError) as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return

        user, password = _resolve_credentials(spec, auth)
        try:
            documents = await self._query(spec, user, password, filter_doc, projection, doc_cap)
        except Exception as exc:  # noqa: BLE001 - mapped onto the typed taxonomy
            yield from_exception(exc, kind=_map_mongo_error(exc), locator=uri)
            return

        pairs = [
            (doc, {"id": str(doc.get("_id")) if isinstance(doc, dict) else None})
            for doc in documents
        ]
        result = build_documents_result(
            uri,
            SOURCE_NAMESPACE,
            pairs,
            doc_cap=doc_cap,
            container_fields={
                "database": spec.database,
                "collection": spec.collection,
                "filter": filter_doc,
            },
        )
        if not isinstance(result, Error):
            stamp_temporal(result.tree, sequence=SequenceCounter().next(), timestamp=now_utc())
        yield result

    async def _query(
        self,
        spec: _MongoSpec,
        user: Optional[str],
        password: Optional[str],
        filter_doc: dict[str, Any],
        projection: Optional[dict[str, Any]],
        doc_cap: int,
    ) -> list[dict[str, Any]]:
        """Run the ``find`` and return up to ``doc_cap + 1`` documents (the seam)."""
        client = _make_client(spec, user, password)
        try:
            collection = client[spec.database][spec.collection]
            cursor = collection.find(filter_doc, projection).limit(doc_cap + 1)
            return [document async for document in cursor]
        finally:
            client.close()


class MongoChangeStreamConnector(BaseFetcher):
    """
    Canonical v1 connector for a MongoDB change stream (unbounded)
    ===============================================
    Watches a collection and emits one ``kind="change"`` node per change, with
    the operation type and resume token in ``source_extra["mongodb"]``.
    ``fetch()`` is a typed ``UNSUPPORTED``.
    ===============================================

    Methods
    -------
        stream:
        fetch:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Report whether ``uri`` is a ``mongodb+changestream://`` URI."""
        return uri.startswith("mongodb+changestream://")

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """Watch a collection and yield one ``Result`` per change, forever."""
        del zoom

        if not MONGODB_AVAILABLE:
            yield _unsupported(uri)
            return

        try:
            spec = _parse_uri(uri, _CHANGESTREAM_SCHEME)
        except ValueError as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return

        user, password = _resolve_credentials(spec, auth)
        counter = SequenceCounter()
        watcher = self._watch(spec, user, password)
        try:
            async for change in watcher:
                yield self._change_result(uri, spec, change, counter)
        except Exception as exc:  # noqa: BLE001 - boundary: returned as a typed Error
            yield from_exception(exc, kind=_map_mongo_error(exc), locator=uri)
        finally:
            await watcher.aclose()

    async def fetch(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> Result:
        """Refuse collection of an unbounded source (typed, immediate)."""
        del auth, zoom
        return error(
            kind=ErrorKind.UNSUPPORTED,
            message=(
                "mongodb+changestream:// is an unbounded source and cannot be collected; "
                "iterate stream() instead of calling fetch()"
            ),
            locator=uri,
        )

    async def _watch(
        self, spec: _MongoSpec, user: Optional[str], password: Optional[str]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Watch the collection and yield raw change documents (the seam)."""
        client = _make_client(spec, user, password)
        try:
            collection = client[spec.database][spec.collection]
            async with collection.watch() as stream:
                async for change in stream:
                    yield change
        finally:
            client.close()

    def _change_result(
        self, uri: str, spec: _MongoSpec, change: dict[str, Any], counter: SequenceCounter
    ) -> Result:
        """Map one change document onto a canonical ``change`` node."""
        content = json.dumps(change, ensure_ascii=False, indent=2, default=str)
        resume_token = change.get("_id")
        node = build_node(
            kind=CHANGE_KIND,
            atoms=[Text(content=content, format=TextFormat.CODE)],
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "operation_type": change.get("operationType"),
                "database": spec.database,
                "collection": spec.collection,
                "resume_token": resume_token,
            },
        )
        stamp_temporal(node, sequence=counter.next(), timestamp=now_utc())
        return success(node)


def _unsupported(uri: str) -> Error:
    """Build the typed ``UNSUPPORTED`` error for a missing ``mongodb`` extra."""
    return error(
        kind=ErrorKind.UNSUPPORTED,
        message=(
            "motor is not installed; install the 'mongodb' extra "
            '(pip install "omni-fetcher[mongodb]") to use mongodb:// sources'
        ),
        locator=uri,
    )
