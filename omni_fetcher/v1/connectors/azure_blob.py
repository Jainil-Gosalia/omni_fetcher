"""The ``azure`` connector for the OmniFetcher v1 contract.

Reads a single blob from Azure Blob Storage and maps it onto the canonical
contract via the shared object-storage spec (``_object_store.build_file_node``):
a ``CompositionNode`` of advisory ``kind`` ``"file"`` carrying one content atom
(``Text`` for text-like blobs, ``Table`` for CSV/TSV, ``Image``/``Audio``/
``Video`` for recognised media, an ``UNSUPPORTED`` gap otherwise), wrapped in a
``Result``. Descriptive fields (account, container, blob, size, etag,
content-type, last-modified) live in the namespaced ``source_extra["azure"]``
mapping -- never inline on the atom. This is the object-storage family's third
member; it reuses S3's byte-to-atom mapping wholesale and differs only in the
URI shape, the credential model, the download call, and the error taxonomy.

The URI is ``az://<container>/<blob-path>`` (alias ``azure://``); the storage
*account* is carried by the credential, not the URI, mirroring how ``s3://``
takes the AWS account from its ``AwsAuth`` rather than the path. Credentials are
supplied *per call* as a ``BasicAuth`` whose ``username`` is the storage account
name and whose ``password`` is an account key -- mapped onto an
``AzureNamedKeyCredential``. This connector never reads a connection string, a
``AZURE_STORAGE_*`` environment variable, or a managed identity: a call with no
``BasicAuth`` is an ``AUTH_FAILED`` error. The ``azure-storage-blob`` client is
optional (the ``azure`` extra): this module imports without it,
``builtin_registry()`` skips ``az://`` when it is missing, and direct use yields
a typed ``UNSUPPORTED`` naming the extra.

Expected failures (missing blob, denied access, bad key, throttling, network
blips) are returned as typed ``Error`` values, never raised. All client
construction flows through the ``_client`` seam so tests script a fake and never
touch Azure.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qs

from omni_fetcher.v1.auth import AuthCredential, BasicAuth
from omni_fetcher.v1.connectors._object_store import build_file_node
from omni_fetcher.v1.connectors._optional import module_available
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import SequenceCounter, now_utc, stamp_temporal
from omni_fetcher.v1.result import Error, Result, error, from_exception
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which all descriptive ``azure`` fields are stored.
SOURCE_NAMESPACE = "azure"

# Whether the optional azure-storage-blob client is importable (the ``azure`` extra).
AZURE_AVAILABLE = module_available("azure.storage.blob")

_SCHEMES = ("az://", "azure://")

# The blob-endpoint suffix for the public Azure cloud. The account name (from
# the credential) is prepended to form the account URL.
_ENDPOINT_SUFFIX = "blob.core.windows.net"


def _parse_azure_uri(uri: str) -> tuple[str, str, Optional[str]]:
    """Parse ``az://container/blob[?endpoint=]`` into ``(container, blob, endpoint)``.

    The storage account is not in the URI (it is carried by the credential). An
    optional ``?endpoint=`` overrides the account URL for a compatible/local
    service (Azurite, Azure Stack, a sovereign cloud); when absent the public
    ``https://<account>.blob.core.windows.net`` endpoint is used. Raises
    ``ValueError`` for a URI that is not an Azure Blob reference or that omits the
    container or blob; the caller maps that to an ``INVALID_INPUT`` error.
    """
    for scheme in _SCHEMES:
        if uri.startswith(scheme):
            remainder = uri[len(scheme) :]
            break
    else:
        raise ValueError(f"not an Azure Blob URI: {uri}")
    location, _, query = remainder.partition("?")
    container, _, blob = location.partition("/")
    if not container or not blob:
        raise ValueError(f"Azure Blob URI must name a container and blob: {uri}")
    endpoint = parse_qs(query).get("endpoint", [None])[0]
    return container, blob, endpoint


def _classify_azure_error(exc: BaseException) -> ErrorKind:
    """Map an azure-core exception onto a taxonomy kind by its HTTP status.

    azure-core exceptions expose the HTTP status as ``.status_code`` (404 for a
    missing blob, 403 denied, 401 bad key, 429 throttled); classifying on that
    keeps each surfacing as the right typed kind. A transport error carries no
    status and is a retryable ``TRANSIENT``.
    """
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = None
    if status == 404:
        return ErrorKind.NOT_FOUND
    if status == 403:
        return ErrorKind.PERMISSION_DENIED
    if status == 401:
        return ErrorKind.AUTH_FAILED
    if status == 429:
        return ErrorKind.RATE_LIMITED
    return ErrorKind.TRANSIENT


def _content_type(properties: Any) -> Optional[str]:
    """Pull the content type off a BlobProperties' nested content settings."""
    settings = getattr(properties, "content_settings", None)
    return getattr(settings, "content_type", None)


def _source_fields(account: str, container: str, blob: str, properties: Any) -> dict[str, Any]:
    """Assemble the namespaced descriptive fields for an Azure blob node."""
    fields: dict[str, Any] = {
        "account": account,
        "container": container,
        "blob": blob,
        "size": getattr(properties, "size", None),
        "etag": getattr(properties, "etag", None),
        "content_type": _content_type(properties),
    }
    last_modified = getattr(properties, "last_modified", None)
    if isinstance(last_modified, datetime):
        fields["last_modified"] = last_modified.isoformat()
    elif last_modified is not None:
        fields["last_modified"] = str(last_modified)
    return fields


class AzureBlobFetcher(BaseFetcher):
    """
    Canonical v1 connector for Azure Blob Storage objects
    ===============================================
    Reads one Azure blob and yields a single canonical ``CompositionNode`` of
    ``kind`` ``"file"`` carrying one content atom, mapped by the shared
    object-storage spec. Descriptive fields (account, container, blob, size,
    etag, content-type, last-modified) live in the namespaced
    ``source_extra["azure"]`` mapping. Read-only.
    ===============================================
    NOTE:
        1. Credentials are supplied per call via ``auth`` (a ``BasicAuth`` whose
           username is the storage account and password is an account key) --
           this connector never reads a connection string, environment variable,
           or managed identity. A call with no ``BasicAuth`` is an
           ``AUTH_FAILED`` error.
        2. Expected failures are returned as typed ``Result`` values
           (``NOT_FOUND`` for a missing blob, ``PERMISSION_DENIED`` for denied
           access, ``AUTH_FAILED`` for a bad key, ``RATE_LIMITED`` for
           throttling, ``TRANSIENT`` for network blips), never raised.
        3. A binary blob with no canonical media representation yields a
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
        Report whether ``uri`` names an Azure blob

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for an ``az://`` or ``azure://`` URI.
        """
        return uri.startswith(_SCHEMES)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical node for an Azure blob (the primitive)

        Resolves ``uri`` to a container/blob, builds a client from the per-call
        ``BasicAuth`` (account name + key), downloads the blob, and yields
        exactly one ``Result`` carrying a ``kind`` ``"file"`` node stamped with a
        per-stream sequence and a wall-clock timestamp.

        NOTE:
            1. ``auth`` must be a ``BasicAuth``; ``None`` (or any other
               credential) yields an ``AUTH_FAILED`` error. The key is used
               transiently and never stored on the instance.
            2. Exactly one ``Result`` is yielded; expected failures are yielded
               as typed ``Error`` / ``Partial`` values, never raised.

        Parameters
        ----------
            uri:
                The ``az://container/blob`` URI to read.
            auth:
                The per-call ``BasicAuth`` (username=account, password=key).
            zoom:
                Ignored; a single blob is its own natural granularity.

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
        """Resolve, download, and map one Azure blob to a single ``Result``."""
        if not AZURE_AVAILABLE:
            return error(
                kind=ErrorKind.UNSUPPORTED,
                message=(
                    "azure-storage-blob is not installed; install the 'azure' extra "
                    "(pip install 'omni_fetcher[azure]') to use az:// sources"
                ),
                locator=uri,
            )
        if not isinstance(auth, BasicAuth):
            return error(
                kind=ErrorKind.AUTH_FAILED,
                message="azure requires a per-call BasicAuth (username=account, password=key)",
                locator=uri,
            )

        try:
            container, blob, endpoint = _parse_azure_uri(uri)
        except ValueError as exc:
            return from_exception(exc, kind=ErrorKind.INVALID_INPUT, locator=uri)

        try:
            data, properties = await asyncio.to_thread(
                self._download, container, blob, auth.username, auth.password, endpoint
            )
        except Exception as exc:  # mapped to a typed Result; never raised (contract)
            return from_exception(exc, kind=_classify_azure_error(exc), locator=uri)

        last_modified = getattr(properties, "last_modified", None)
        if not isinstance(last_modified, datetime):
            last_modified = None
        return build_file_node(
            uri=uri,
            namespace=SOURCE_NAMESPACE,
            key=blob,
            data=data,
            content_type=_content_type(properties),
            source_fields=_source_fields(auth.username, container, blob, properties),
            updated=last_modified,
        )

    def _download(
        self,
        container: str,
        blob: str,
        account: str,
        account_key: str,
        endpoint: Optional[str] = None,
    ) -> tuple[bytes, Any]:
        """Download one blob's bytes and return them with the blob properties.

        Builds a fresh client from the per-call account name + key only (via the
        ``_client`` seam), downloads the blob, and returns ``(data, properties)``.
        A missing blob raises ``ResourceNotFoundError`` (mapped to ``NOT_FOUND``).
        """
        client = self._client(container, blob, account, account_key, endpoint)
        downloader = client.download_blob()
        data: bytes = downloader.readall()
        return data, downloader.properties

    @staticmethod
    def _client(
        container: str,
        blob: str,
        account: str,
        account_key: str,
        endpoint: Optional[str] = None,
    ) -> Any:
        """Build an azure-storage-blob ``BlobClient`` from a per-call key.

        The heavy import is deferred to here so the module imports on a base
        install; the client is built from the injected account name + key only,
        never a connection string or ambient credential. ``endpoint`` overrides
        the account URL for a compatible/local service (e.g. Azurite).
        """
        from azure.core.credentials import AzureNamedKeyCredential
        from azure.storage.blob import BlobClient

        account_url = endpoint or f"https://{account}.{_ENDPOINT_SUFFIX}"
        credential = AzureNamedKeyCredential(account, account_key)
        return BlobClient(
            account_url=account_url,
            container_name=container,
            blob_name=blob,
            credential=credential,
        )
