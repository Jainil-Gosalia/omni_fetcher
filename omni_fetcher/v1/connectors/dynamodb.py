"""The canonical ``dynamodb`` connector for the v1 contract (v1.16).

Reads items from an Amazon DynamoDB table through the shared document-store spec
(``_document_store``): one ``kind="documents"`` container whose children are
``kind="json_document"`` nodes (each item serialised as a ``Text`` atom,
``format=CODE``). Two read modes:

- **``dynamodb://<table>?key=<json>``** -- a ``GetItem`` by primary key (one
  document; a missing item is ``NOT_FOUND``).
- **``dynamodb://<table>``** (or ``?scan=true``) -- a bounded ``Scan`` returning
  up to ``?limit=`` items (default 1000; over the cap degrades to a ``Partial``).

``?region=<aws-region>`` selects the region. Credentials are supplied per call as
an ``AwsAuth`` (as ``s3`` / ``kinesis``); a call with no ``AwsAuth`` is an
``AUTH_FAILED`` error. Uses the core ``boto3`` resource on a worker thread
(DynamoDB is blocking), so there is no optional extra. Expected failures map onto
the taxonomy by the AWS error code and are never raised. All table access flows
through the ``_read`` seam so tests script a fake and never touch AWS.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qs

from omni_fetcher.v1.auth import AuthCredential, AwsAuth, NormalizedAuthResolver
from omni_fetcher.v1.connectors._document_store import (
    build_documents_result,
    resolve_doc_cap,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import SequenceCounter, now_utc, stamp_temporal
from omni_fetcher.v1.result import Error, Result, error, from_exception
from omni_fetcher.v1.zoom import ZoomSpec

SOURCE_NAMESPACE = "dynamodb"
_SCHEME = "dynamodb://"
_DEFAULT_REGION = "us-east-1"

# AWS error codes -> v1 error taxonomy (mirrors the s3 connector's classifier).
_NOT_FOUND_CODES = frozenset({"ResourceNotFoundException", "404"})
_PERMISSION_CODES = frozenset({"AccessDeniedException", "AccessDenied", "403"})
_AUTH_CODES = frozenset(
    {"UnrecognizedClientException", "InvalidSignatureException", "InvalidAccessKeyId", "401"}
)
_RATE_LIMITED_CODES = frozenset(
    {"ProvisionedThroughputExceededException", "ThrottlingException", "RequestLimitExceeded", "429"}
)
_INVALID_CODES = frozenset({"ValidationException"})


class _DynamoSpec:
    """Parsed ``dynamodb://`` routing decision."""

    def __init__(
        self,
        table: str,
        key: Optional[str],
        region: str,
        limit: Optional[str],
    ) -> None:
        self.table = table
        self.key = key
        self.region = region
        self.limit = limit


def _parse_uri(uri: str) -> _DynamoSpec:
    """Parse a ``dynamodb://table?...`` URI into a spec, raising ``ValueError``."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a dynamodb:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    location, _, query = remainder.partition("?")
    table = location.strip("/")
    if not table or "/" in table:
        raise ValueError(f"dynamodb:// URI must be dynamodb://table[?...]: {uri}")
    params = parse_qs(query)
    return _DynamoSpec(
        table=table,
        key=params.get("key", [None])[0],
        region=params.get("region", [_DEFAULT_REGION])[0],
        limit=params.get("limit", [None])[0],
    )


def _classify_client_error(exc: BaseException) -> ErrorKind:
    """Map a botocore ``ClientError`` onto the taxonomy by its AWS error code."""
    response = getattr(exc, "response", None) or {}
    error_block = response.get("Error", {}) if isinstance(response, dict) else {}
    code = str(error_block.get("Code", "") or "")
    if code in _NOT_FOUND_CODES:
        return ErrorKind.NOT_FOUND
    if code in _PERMISSION_CODES:
        return ErrorKind.PERMISSION_DENIED
    if code in _AUTH_CODES:
        return ErrorKind.AUTH_FAILED
    if code in _RATE_LIMITED_CODES:
        return ErrorKind.RATE_LIMITED
    if code in _INVALID_CODES:
        return ErrorKind.INVALID_INPUT
    return ErrorKind.TRANSIENT


class DynamoDBConnector(BaseFetcher):
    """
    Canonical v1 connector for Amazon DynamoDB items (bounded)
    ===============================================
    Reads one item (``GetItem`` by ``?key=``) or up to ``?limit=`` items
    (``Scan``) and emits one ``kind="documents"`` container of ``json_document``
    children. Descriptive fields live in ``source_extra["dynamodb"]``.
    ===============================================
    NOTE:
        1. Implements only ``stream()`` (yields one container); ``fetch()`` is
           inherited.
        2. Credentials are a per-call ``AwsAuth``; a call without one is
           ``AUTH_FAILED``.
        3. A ``GetItem`` that finds nothing is ``NOT_FOUND``; a ``Scan`` that
           matches nothing is an empty container (a valid empty result).

    Methods
    -------
        stream:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Report whether ``uri`` names a DynamoDB table."""
        return uri.startswith(_SCHEME)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """Read items and yield exactly one ``documents`` container ``Result``."""
        del zoom

        try:
            spec = _parse_uri(uri)
            doc_cap = resolve_doc_cap(spec.limit)
            key = json.loads(spec.key) if spec.key else None
        except (ValueError, json.JSONDecodeError) as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return
        if not isinstance(auth, AwsAuth):
            yield error(
                ErrorKind.AUTH_FAILED,
                message="dynamodb requires a per-call AwsAuth credential",
                locator=uri,
            )
            return

        try:
            items = await self._read(spec, auth, key, doc_cap)
        except Exception as exc:  # noqa: BLE001 - mapped onto the typed taxonomy
            yield from_exception(exc, kind=_classify_client_error(exc), locator=uri)
            return

        if key is not None and not items:
            yield error(
                ErrorKind.NOT_FOUND,
                message=f"no item for key in table {spec.table!r}",
                locator=uri,
            )
            return

        pairs = [(item, {"table": spec.table}) for item in items]
        result = build_documents_result(
            uri,
            SOURCE_NAMESPACE,
            pairs,
            doc_cap=doc_cap,
            container_fields={"table": spec.table, "mode": "get" if key is not None else "scan"},
        )
        if not isinstance(result, Error):
            stamp_temporal(result.tree, sequence=SequenceCounter().next(), timestamp=now_utc())
        yield result

    async def _read(
        self,
        spec: _DynamoSpec,
        auth: AwsAuth,
        key: Optional[dict[str, Any]],
        doc_cap: int,
    ) -> list[dict[str, Any]]:
        """Read items via GetItem (by key) or Scan (the table seam)."""
        return await asyncio.to_thread(self._read_sync, spec, auth, key, doc_cap)

    @staticmethod
    def _read_sync(
        spec: _DynamoSpec,
        auth: AwsAuth,
        key: Optional[dict[str, Any]],
        doc_cap: int,
    ) -> list[dict[str, Any]]:
        """Blocking boto3 read; runs on a worker thread."""
        import boto3

        parts = NormalizedAuthResolver().resolve_aws(auth)
        resource = boto3.resource(
            "dynamodb",
            aws_access_key_id=parts.get("access_key_id"),
            aws_secret_access_key=parts.get("secret_access_key"),
            aws_session_token=parts.get("session_token"),
            region_name=parts.get("region") or spec.region,
        )
        table = resource.Table(spec.table)
        if key is not None:
            response = table.get_item(Key=key)
            item = response.get("Item")
            return [item] if item else []
        response = table.scan(Limit=doc_cap + 1)
        return list(response.get("Items", []))
