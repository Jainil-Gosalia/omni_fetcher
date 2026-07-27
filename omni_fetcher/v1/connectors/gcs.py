"""The ``gcs`` connector for the OmniFetcher v1 contract.

Reads a single object from Google Cloud Storage and maps it onto the canonical
contract via the shared object-storage spec (``_object_store.build_file_node``):
a ``CompositionNode`` of advisory ``kind`` ``"file"`` carrying one content atom
(``Text`` for text-like objects, ``Table`` for CSV/TSV, ``Image``/``Audio``/
``Video`` for recognised media, an ``UNSUPPORTED`` gap otherwise), wrapped in a
``Result``. Descriptive fields (bucket, object, size, etag, content-type,
updated) live in the namespaced ``source_extra["gcs"]`` mapping -- never inline
on the atom. This is the object-storage family's second member; it reuses S3's
byte-to-atom mapping wholesale and differs only in the URI shape, the credential
model, the download call, and the error taxonomy.

Credentials are supplied *per call* as an ``OAuth2Auth`` -- a short-lived
access token. GCS authenticates with an OAuth2 bearer token, and per
``PHILOSOPHY.md`` section 7 a service-account key is a host-side token-exchange
concern: the host exchanges the key for an access token and injects it here, so
this connector never reads ambient credentials, an ``GOOGLE_APPLICATION_CREDENTIALS``
file, or the metadata server. A call with no ``OAuth2Auth`` is an ``AUTH_FAILED``
error. The ``google-cloud-storage`` client is optional (the ``gcs`` extra):
this module imports without it, ``builtin_registry()`` skips ``gs://`` when it
is missing, and direct use yields a typed ``UNSUPPORTED`` naming the extra.

Expected failures (missing object, denied access, bad token, throttling,
network blips) are returned as typed ``Error`` values, never raised. All client
construction flows through the ``_client`` seam so tests script a fake and never
touch GCS.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from omni_fetcher.v1.auth import AuthCredential, OAuth2Auth
from omni_fetcher.v1.connectors._object_store import build_file_node
from omni_fetcher.v1.connectors._optional import module_available
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import SequenceCounter, now_utc, stamp_temporal
from omni_fetcher.v1.result import Error, Result, error, from_exception
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which all descriptive ``gcs`` fields are stored.
SOURCE_NAMESPACE = "gcs"

# Whether the optional google-cloud-storage client is importable (the ``gcs`` extra).
GCS_AVAILABLE = module_available("google.cloud.storage")

_SCHEME = "gs://"

# GCS object reads address ``storage.googleapis.com/<bucket>/<object>`` and do
# not use a project, but ``storage.Client(...)`` still requires one. We pass an
# explicit placeholder rather than let the client read the ambient environment
# for a default project (the no-ambient-credentials contract, PHILOSOPHY s7).
_READ_PROJECT = "omni-fetcher"


def _parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Parse a ``gs://bucket/object`` URI into ``(bucket, object)``.

    Raises ``ValueError`` for a URI that is not a GCS reference or that omits
    the bucket or object; the caller maps that to an ``INVALID_INPUT`` error.
    """
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a GCS URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    bucket, _, key = remainder.partition("/")
    if not bucket or not key:
        raise ValueError(f"GCS URI must name a bucket and object: {uri}")
    return bucket, key


def _classify_google_error(exc: BaseException) -> ErrorKind:
    """Map a google-cloud exception onto a taxonomy kind by its HTTP status.

    The google-cloud / api-core exceptions expose the HTTP status as ``.code``
    (404, 403, ...); classifying on that keeps a missing object, denied access,
    bad token, and throttle each surfacing as the right typed kind rather than
    an opaque transient failure. Anything without a recognised status is a
    ``TRANSIENT`` (a retryable transport blip).
    """
    code = getattr(exc, "code", None)
    status = code if isinstance(code, int) else None
    if status == 404:
        return ErrorKind.NOT_FOUND
    if status == 403:
        return ErrorKind.PERMISSION_DENIED
    if status == 401:
        return ErrorKind.AUTH_FAILED
    if status == 429:
        return ErrorKind.RATE_LIMITED
    return ErrorKind.TRANSIENT


def _source_fields(bucket: str, key: str, blob: Any) -> dict[str, Any]:
    """Assemble the namespaced descriptive fields for a GCS object node."""
    fields: dict[str, Any] = {
        "bucket": bucket,
        "object": key,
        "size": getattr(blob, "size", None),
        "etag": getattr(blob, "etag", None),
        "content_type": getattr(blob, "content_type", None),
    }
    updated = getattr(blob, "updated", None)
    if isinstance(updated, datetime):
        fields["updated"] = updated.isoformat()
    elif updated is not None:
        fields["updated"] = str(updated)
    return fields


class GCSFetcher(BaseFetcher):
    """
    Canonical v1 connector for Google Cloud Storage objects
    ===============================================
    Reads one GCS object and yields a single canonical ``CompositionNode`` of
    ``kind`` ``"file"`` carrying one content atom, mapped by the shared
    object-storage spec. Descriptive fields (bucket, object, size, etag,
    content-type, updated) live in the namespaced ``source_extra["gcs"]``
    mapping. Read-only.
    ===============================================
    NOTE:
        1. Credentials are supplied per call via ``auth`` (an ``OAuth2Auth``
           access token) -- this connector never reads ambient credentials, a
           credentials file, or the metadata server. A call with no
           ``OAuth2Auth`` is an ``AUTH_FAILED`` error.
        2. Expected failures are returned as typed ``Result`` values
           (``NOT_FOUND`` for a missing object, ``PERMISSION_DENIED`` for denied
           access, ``AUTH_FAILED`` for a bad token, ``RATE_LIMITED`` for
           throttling, ``TRANSIENT`` for network blips), never raised.
        3. A binary object with no canonical media representation yields a
           ``partial`` node (empty ``Text`` atom + an ``UNSUPPORTED`` gap),
           mirroring S3.

    Methods
    -------
        stream:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether ``uri`` names a GCS object

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for a ``gs://`` URI.
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
        Stream the canonical node for a GCS object (the primitive)

        Resolves ``uri`` to a bucket/object, builds a client from the per-call
        ``OAuth2Auth`` token, downloads the object, and yields exactly one
        ``Result`` carrying a ``kind`` ``"file"`` node stamped with a per-stream
        sequence and a wall-clock timestamp.

        NOTE:
            1. ``auth`` must be an ``OAuth2Auth``; ``None`` (or any other
               credential) yields an ``AUTH_FAILED`` error. The token is used
               transiently and never stored on the instance.
            2. Exactly one ``Result`` is yielded; expected failures are yielded
               as typed ``Error`` / ``Partial`` values, never raised.

        Parameters
        ----------
            uri:
                The ``gs://bucket/object`` URI to read.
            auth:
                The per-call ``OAuth2Auth`` credential.
            zoom:
                Ignored; a single object is its own natural granularity.

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
        """Resolve, download, and map one GCS object to a single ``Result``."""
        if not GCS_AVAILABLE:
            return error(
                kind=ErrorKind.UNSUPPORTED,
                message=(
                    "google-cloud-storage is not installed; install the 'gcs' extra "
                    "(pip install 'omni_fetcher[gcs]') to use gs:// sources"
                ),
                locator=uri,
            )
        if not isinstance(auth, OAuth2Auth):
            return error(
                kind=ErrorKind.AUTH_FAILED,
                message="gcs requires a per-call OAuth2Auth access token",
                locator=uri,
            )

        try:
            bucket, key = _parse_gcs_uri(uri)
        except ValueError as exc:
            return from_exception(exc, kind=ErrorKind.INVALID_INPUT, locator=uri)

        try:
            data, blob = await asyncio.to_thread(self._download, bucket, key, auth.access_token)
        except Exception as exc:  # mapped to a typed Result; never raised (contract)
            return from_exception(exc, kind=_classify_google_error(exc), locator=uri)

        updated = getattr(blob, "updated", None)
        if not isinstance(updated, datetime):
            updated = None
        return build_file_node(
            uri=uri,
            namespace=SOURCE_NAMESPACE,
            key=key,
            data=data,
            content_type=getattr(blob, "content_type", None),
            source_fields=_source_fields(bucket, key, blob),
            updated=updated,
        )

    def _download(self, bucket: str, key: str, access_token: str) -> tuple[bytes, Any]:
        """Download one object's bytes and return them with the blob metadata.

        Builds a fresh client from the per-call token only (via the ``_client``
        seam), downloads the object, and returns ``(data, blob)`` -- the blob's
        ``content_type`` / ``size`` / ``etag`` / ``updated`` are populated by the
        download. A missing object raises ``NotFound`` (mapped to ``NOT_FOUND``).
        """
        client = self._client(access_token)
        blob = client.bucket(bucket).blob(key)
        data: bytes = blob.download_as_bytes()
        return data, blob

    @staticmethod
    def _client(access_token: str) -> Any:
        """Build a google-cloud-storage client from a per-call access token.

        The heavy import is deferred to here so the module imports on a base
        install; the client is built from the injected token only, with an
        explicit placeholder project (object reads do not use it) so the client
        never falls back to reading the ambient environment for a default.
        """
        from google.cloud import storage  # type: ignore[attr-defined]
        from google.oauth2.credentials import Credentials

        credentials = Credentials(token=access_token)
        return storage.Client(project=_READ_PROJECT, credentials=credentials)
