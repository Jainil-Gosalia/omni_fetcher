"""The ``s3`` connector for the OmniFetcher v1 contract.

Reads a single object from AWS S3 and maps it onto the canonical contract: a
``CompositionNode`` of advisory ``kind`` ``"file"`` carrying one content atom,
wrapped in a ``Result``. Text-like objects become a ``Text`` atom; CSV/TSV
objects become a ``Table`` atom; everything else is represented by its content
type as an ``Image`` / ``Audio`` / ``Video`` atom (bytes carried inline) or, as
a fallback, an opaque ``Text`` placeholder paired with an ``UNSUPPORTED`` gap.
Descriptive fields (bucket, key, size, etag, content-type, last-modified) live
in the namespaced ``source_extra["s3"]`` mapping -- never inline on the atom.

AWS credentials are supplied *per call* via the ``auth`` parameter (an
``AwsAuth`` credential) and surfaced through
``NormalizedAuthResolver().resolve_aws`` -- AWS uses request signing, not a
static header. This connector never reads ambient environment variables,
instance-profile credentials, or a shared credentials file: a request with no
``AwsAuth`` is an ``AUTH_FAILED`` error. It reuses the v0.11 ``s3`` fetcher's
boto3 ``get_object`` logic, but feeds it the per-call credentials only.

Expected failures (missing key, access denied, bad credentials, throttling,
network blips) are returned as typed ``Error`` / ``Partial`` values, never
raised.
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime
from typing import Any, AsyncIterator, Optional

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)

from omni_fetcher.v1.atoms import (
    Audio,
    Image,
    Table,
    Text,
    TextFormat,
    Video,
)
from omni_fetcher.v1.auth import AuthCredential, AwsAuth, NormalizedAuthResolver
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import (
    SequenceCounter,
    build_node,
    now_utc,
    stamp_temporal,
)
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

# Source namespace under which all descriptive ``s3`` fields are stored in
# ``Metadata.source_extra``.
SOURCE_NAMESPACE = "s3"

# Advisory semantic ``kind`` for every node this connector emits.
FILE_KIND = "file"

# Region used when an ``AwsAuth`` carries no explicit region. boto3 requires
# *some* region for the S3 client; ``us-east-1`` matches the v0.11 default.
_DEFAULT_REGION = "us-east-1"

# MIME types parsed into a ``Table`` atom rather than a ``Text`` atom.
_CSV_MIME = "text/csv"
_TSV_MIMES = frozenset({"text/tab-separated-values", "text/tsv"})

# Map a text-ish MIME type onto the canonical ``TextFormat`` for its content.
_TEXT_FORMATS: dict[str, TextFormat] = {
    "text/markdown": TextFormat.MARKDOWN,
    "text/html": TextFormat.HTML,
    "text/x-rst": TextFormat.RST,
    "application/json": TextFormat.PLAIN,
    "application/xml": TextFormat.PLAIN,
}

# botocore ``ClientError`` codes that map onto a non-found taxonomy kind. AWS
# returns these in ``error["Error"]["Code"]`` (and an HTTP status alongside).
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound"})
_PERMISSION_CODES = frozenset({"AccessDenied", "AllAccessDisabled", "403"})
_AUTH_CODES = frozenset(
    {
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "ExpiredToken",
        "InvalidToken",
        "TokenRefreshRequired",
        "AuthFailure",
        "UnrecognizedClientException",
        "401",
    }
)
_RATE_LIMITED_CODES = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "SlowDown",
        "RequestThrottled",
        "TooManyRequests",
        "503",
        "429",
    }
)


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse an ``s3://bucket/key`` (or virtual-host) URI into bucket + key.

    Mirrors the v0.11 fetcher's parsing so behaviour is unchanged. Raises
    ``ValueError`` for a URI that is not an S3 reference; the caller maps that
    to an ``INVALID_INPUT`` error.
    """
    if uri.startswith("s3://"):
        remainder = uri[len("s3://") :]
        parts = remainder.split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
    elif ".s3.amazonaws.com" in uri:
        remainder = uri.replace("https://", "").replace("http://", "")
        parts = remainder.split(".s3.amazonaws.com/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
    else:
        raise ValueError(f"not an S3 URI: {uri}")

    if not bucket or not key:
        raise ValueError(f"S3 URI must name a bucket and key: {uri}")
    return bucket, key


def _classify_client_error(exc: ClientError) -> ErrorKind:
    """Map a botocore ``ClientError`` onto a taxonomy kind by its code.

    Reads the AWS error code (and falls back to the HTTP status) so a missing
    key, denied access, bad credential, and throttle each surface as the right
    typed ``ErrorKind`` rather than an opaque transient failure.
    """
    response = getattr(exc, "response", None) or {}
    error_block = response.get("Error", {}) if isinstance(response, dict) else {}
    code = str(error_block.get("Code", "") or "")
    status = ""
    metadata = response.get("ResponseMetadata", {}) if isinstance(response, dict) else {}
    if isinstance(metadata, dict):
        status = str(metadata.get("HTTPStatusCode", "") or "")

    for token in (code, status):
        if token in _NOT_FOUND_CODES:
            return ErrorKind.NOT_FOUND
        if token in _PERMISSION_CODES:
            return ErrorKind.PERMISSION_DENIED
        if token in _AUTH_CODES:
            return ErrorKind.AUTH_FAILED
        if token in _RATE_LIMITED_CODES:
            return ErrorKind.RATE_LIMITED
    return ErrorKind.TRANSIENT


def _text_format_for(content_type: Optional[str]) -> TextFormat:
    """Pick the canonical ``TextFormat`` for a text-like object."""
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if base in _TEXT_FORMATS:
        return _TEXT_FORMATS[base]
    if base.startswith("text/") and base != "text/plain":
        # An unmapped ``text/*`` subtype is source code as far as the
        # canonical vocabulary is concerned.
        return TextFormat.CODE
    return TextFormat.PLAIN


def _is_text(content_type: Optional[str]) -> bool:
    """Report whether an object should be decoded into a ``Text`` atom."""
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if not base or base == "application/octet-stream":
        # Unknown / generic binary: treat as text and let decode decide.
        return True
    if base.startswith("text/"):
        return True
    return base in {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
    }


def _is_tabular(content_type: Optional[str], key: str) -> bool:
    """Report whether an object should be parsed into a ``Table`` atom."""
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if base == _CSV_MIME or base in _TSV_MIMES:
        return True
    return key.lower().endswith((".csv", ".tsv"))


def _binary_atom_for(content_type: Optional[str], data: bytes) -> Optional[Image | Audio | Video]:
    """Build an image/audio/video atom for a recognised binary content type.

    Returns ``None`` for any content type that is not a known image, audio, or
    video media type, so the caller can fall back to an explicit gap.
    """
    base = (content_type or "").split(";", 1)[0].strip().lower()
    subtype = base.split("/", 1)[1] if "/" in base else base
    if base.startswith("image/"):
        return Image(format=subtype, data=data)
    if base.startswith("audio/"):
        return Audio(format=subtype, data=data)
    if base.startswith("video/"):
        return Video(format=subtype, data=data)
    return None


def _parse_table(text: str, content_type: Optional[str], key: str) -> Table:
    """Parse delimited text into a canonical ``Table`` atom.

    The first row is treated as headers when every subsequent row matches its
    width; otherwise the grid is emitted header-less so the ``Table``
    width-invariant is never violated.
    """
    base = (content_type or "").split(";", 1)[0].strip().lower()
    is_tsv = key.lower().endswith(".tsv") or base in _TSV_MIMES
    delimiter = "\t" if is_tsv else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    grid = [list(row) for row in reader]
    if not grid:
        return Table(headers=None, rows=[])
    headers = grid[0]
    body = grid[1:]
    if body and all(len(row) == len(headers) for row in body):
        return Table(headers=headers, rows=body)
    return Table(headers=None, rows=grid)


def _source_fields(
    bucket: str,
    key: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the namespaced descriptive fields for an S3 object node."""
    fields: dict[str, Any] = {
        "bucket": bucket,
        "key": key,
        "size": response.get("ContentLength"),
        "etag": response.get("ETag"),
        "content_type": response.get("ContentType"),
    }
    last_modified = response.get("LastModified")
    if isinstance(last_modified, datetime):
        fields["last_modified"] = last_modified.isoformat()
    elif last_modified is not None:
        fields["last_modified"] = str(last_modified)
    return fields


class S3Fetcher(BaseFetcher):
    """
    Canonical v1 connector for AWS S3 objects
    ===============================================
    Reads one S3 object and yields a single canonical ``CompositionNode`` of
    ``kind`` ``"file"`` carrying one content atom (``Text`` for text-like
    objects, ``Table`` for CSV/TSV objects, and ``Image`` / ``Audio`` /
    ``Video`` for recognised binary media). Descriptive fields (bucket, key,
    size, etag, content-type, last-modified) live in the namespaced
    ``source_extra["s3"]`` mapping. Read-only.
    ===============================================
    NOTE:
        1. AWS credentials are supplied per call via ``auth`` (an
           ``AwsAuth``) and surfaced through
           ``NormalizedAuthResolver.resolve_aws`` -- this connector never reads
           ambient environment, instance-profile, or shared-file credentials.
           A call with no ``AwsAuth`` is an ``AUTH_FAILED`` error.
        2. Expected failures are returned as typed ``Result`` values
           (``NOT_FOUND`` for a missing key, ``PERMISSION_DENIED`` for denied
           access, ``AUTH_FAILED`` for bad credentials, ``RATE_LIMITED`` for
           throttling, ``TRANSIENT`` for network blips), never raised.
        3. A binary object with no canonical media representation yields a
           ``partial`` node (empty ``Text`` atom + an ``UNSUPPORTED`` gap)
           rather than a silently-empty success.

    Methods
    -------
        stream:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether ``uri`` names an S3 object

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for an ``s3://`` URI or a virtual-host S3 URL.
        """
        return uri.startswith("s3://") or ".s3.amazonaws.com" in uri

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical node for an S3 object (the primitive)

        Resolves ``uri`` to a bucket/key, resolves the per-call ``AwsAuth``
        into signer inputs, fetches the object with the v0.11 boto3
        ``get_object`` logic, and yields exactly one ``Result`` carrying a
        ``kind`` ``"file"`` node. The node is stamped with a per-stream
        sequence and a wall-clock timestamp.

        NOTE:
            1. ``auth`` must be an ``AwsAuth``; ``None`` (or any non-AWS
               credential) yields an ``AUTH_FAILED`` error. Credentials are
               used transiently and never stored on the instance.
            2. Exactly one ``Result`` is yielded; expected failures are
               yielded as typed ``Error`` / ``Partial`` values, never raised.

        Parameters
        ----------
            uri:
                The ``s3://bucket/key`` URI (or virtual-host URL) to read.
            auth:
                The per-call ``AwsAuth`` credential.
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
        """Resolve, fetch, and map one S3 object to a single ``Result``."""
        if not isinstance(auth, AwsAuth):
            return error(
                kind=ErrorKind.AUTH_FAILED,
                message="s3 requires a per-call AwsAuth credential",
                locator=uri,
            )

        try:
            bucket, key = _parse_s3_uri(uri)
        except ValueError as exc:
            return from_exception(exc, kind=ErrorKind.INVALID_INPUT, locator=uri)

        parts = NormalizedAuthResolver().resolve_aws(auth)
        try:
            response = await asyncio.to_thread(self._get_object, bucket, key, parts)
        except ClientError as exc:
            return from_exception(exc, kind=_classify_client_error(exc), locator=uri)
        except (NoCredentialsError,) as exc:
            return from_exception(exc, kind=ErrorKind.AUTH_FAILED, locator=uri)
        except EndpointConnectionError as exc:
            return from_exception(exc, kind=ErrorKind.TRANSIENT, locator=uri)
        except BotoCoreError as exc:
            return from_exception(exc, kind=ErrorKind.TRANSIENT, locator=uri)

        return self._build_object_node(uri, bucket, key, response)

    @staticmethod
    def _get_object(
        bucket: str,
        key: str,
        parts: dict[str, Optional[str]],
    ) -> dict[str, Any]:
        """Fetch one object via boto3 using the per-call credential parts.

        Builds a fresh client from the supplied credential parts only -- never
        from ambient environment or instance credentials -- reads the body
        into memory, and returns the raw bytes alongside the response headers.
        """
        client = boto3.client(
            "s3",
            aws_access_key_id=parts.get("access_key_id"),
            aws_secret_access_key=parts.get("secret_access_key"),
            aws_session_token=parts.get("session_token"),
            region_name=parts.get("region") or _DEFAULT_REGION,
        )
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        return {
            "Body": body,
            "ContentType": response.get("ContentType"),
            "ContentLength": response.get("ContentLength"),
            "ETag": response.get("ETag"),
            "LastModified": response.get("LastModified"),
        }

    def _build_object_node(
        self,
        uri: str,
        bucket: str,
        key: str,
        response: dict[str, Any],
    ) -> Result:
        """Map a fetched object's bytes + headers to the ``"file"`` node."""
        data: bytes = response.get("Body") or b""
        content_type = response.get("ContentType")
        source_fields = _source_fields(bucket, key, response)
        updated = response.get("LastModified")
        if not isinstance(updated, datetime):
            updated = None

        if _is_tabular(content_type, key) or _is_text(content_type):
            return self._build_textual_node(uri, content_type, key, data, source_fields, updated)

        atom = _binary_atom_for(content_type, data)
        if atom is not None:
            node = build_node(
                kind=FILE_KIND,
                atoms=[atom],
                source_url=uri,
                updated=updated,
                source_namespace=SOURCE_NAMESPACE,
                source_fields=source_fields,
            )
            return success(node)

        # Recognised object, but no canonical media representation: be explicit
        # about the gap rather than emit a silent empty success.
        node = build_node(
            kind=FILE_KIND,
            atoms=[Text(content="", format=TextFormat.OPAQUE)],
            source_url=uri,
            updated=updated,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        return partial(
            node,
            [
                gap(
                    kind=ErrorKind.UNSUPPORTED,
                    locator=uri,
                    detail=f"binary content not represented ({content_type})",
                )
            ],
        )

    def _build_textual_node(
        self,
        uri: str,
        content_type: Optional[str],
        key: str,
        data: bytes,
        source_fields: dict[str, Any],
        updated: Optional[datetime],
    ) -> Result:
        """Decode object bytes and assemble a ``Text`` / ``Table`` file node."""
        try:
            text = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            try:
                text = data.decode("latin-1")
                encoding = "latin-1"
            except UnicodeError as exc:
                return from_exception(exc, kind=ErrorKind.PARSE_ERROR, locator=uri)

        if _is_tabular(content_type, key):
            try:
                atom: Text | Table = _parse_table(text, content_type, key)
            except (csv.Error, ValueError) as exc:
                return from_exception(exc, kind=ErrorKind.PARSE_ERROR, locator=uri)
        else:
            atom = Text(
                content=text,
                format=_text_format_for(content_type),
                encoding=encoding,
            )

        node = build_node(
            kind=FILE_KIND,
            atoms=[atom],
            source_url=uri,
            updated=updated,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        return success(node)
