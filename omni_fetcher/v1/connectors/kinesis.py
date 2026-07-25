"""The canonical ``kinesis`` connector -- unbounded AWS Kinesis stream for v1.

Consumes an Amazon Kinesis data stream through the v1 contract: each record is
one ``Result`` whose tree is a single ``kind="message"`` node carrying one
``Text`` atom (the decoded payload) plus the resume position in
``source_extra["kinesis"]`` (sequence_number, partition_key, shard, stream).
``sequence_number`` is the record's own sequence; resuming seeks *after* it via
``?after=<sequence_number>`` (``stream_with_restart`` derives this).

Configuration travels in the URI: ``kinesis://<stream>`` with ``?shard=<shardId>``
(default: the stream's first shard), ``?region=<aws-region>``, and one starting
position -- ``?after=<seq>`` (resume after a sequence), ``?at=<seq>`` (at a
sequence), or neither (LATEST, new records only).

Credentials are supplied per call as an ``AwsAuth`` (as ``s3``); a call with no
``AwsAuth`` is an ``AUTH_FAILED`` error. Uses the core ``boto3`` client on a
worker thread (Kinesis polling is blocking), so there is no optional extra.

Stream-only: ``fetch()`` returns a typed ``UNSUPPORTED``. A transport failure
mid-stream yields one terminal ``TRANSIENT``; a closed shard ends the stream
cleanly (no error). All broker access flows through the ``_consume`` seam so
tests script a fake and never touch AWS.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, AsyncGenerator, AsyncIterator, Optional
from urllib.parse import parse_qs

from omni_fetcher.v1.auth import AuthCredential, AwsAuth, NormalizedAuthResolver
from omni_fetcher.v1.connectors._messaging import build_message_result
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import SequenceCounter
from omni_fetcher.v1.result import Result, error, from_exception
from omni_fetcher.v1.zoom import ZoomSpec

SOURCE_NAMESPACE = "kinesis"
_SCHEME = "kinesis://"
_DEFAULT_REGION = "us-east-1"


class _KinesisSpec:
    """Parsed ``kinesis://`` routing decision."""

    def __init__(
        self,
        stream: str,
        shard: Optional[str],
        region: str,
        after: Optional[str],
        at: Optional[str],
    ) -> None:
        self.stream = stream
        self.shard = shard
        self.region = region
        self.after = after
        self.at = at


def _parse_uri(uri: str) -> _KinesisSpec:
    """Parse a ``kinesis://stream?...`` URI into a spec, raising ``ValueError``."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a kinesis:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    location, _, query = remainder.partition("?")
    stream = location.strip("/")
    if not stream or "/" in stream:
        raise ValueError(f"kinesis:// URI must be kinesis://stream[?...]: {uri}")
    params = parse_qs(query)
    return _KinesisSpec(
        stream=stream,
        shard=params.get("shard", [None])[0],
        region=params.get("region", [_DEFAULT_REGION])[0],
        after=params.get("after", [None])[0],
        at=params.get("at", [None])[0],
    )


class KinesisConnector(BaseFetcher):
    """
    Unbounded AWS Kinesis stream connector for the v1 contract
    ===============================================
    Streams records as canonical per-item ``Result``s, each a ``kind="message"``
    node with a ``Text`` atom and the resume ``sequence_number`` in
    ``source_extra["kinesis"]``. ``fetch()`` is a typed ``UNSUPPORTED``. All
    broker access goes through the ``_consume`` seam.
    ===============================================
    NOTE:
        1. Credentials are a per-call ``AwsAuth``; a call without one is
           ``AUTH_FAILED``.
        2. A transport failure yields one terminal ``TRANSIENT``; a closed shard
           (``NextShardIterator`` is null) ends the stream with no error.

    Attributes
    ----------
        poll_interval:
            Seconds to wait between empty ``GetRecords`` polls.

    Methods
    -------
        stream:
        fetch:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    def __init__(self, poll_interval: float = 1.0) -> None:
        self.poll_interval = poll_interval

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Report whether ``uri`` names a Kinesis stream."""
        return uri.startswith(_SCHEME)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Consume a Kinesis stream and yield one ``Result`` per record, forever

        Parameters
        ----------
            uri:
                The ``kinesis://stream?...`` source URI.
            auth:
                The per-call ``AwsAuth`` credential.
            zoom:
                Accepted for protocol conformance.

        Return
        ------
            results:
                An unbounded async iterator of ``Result`` items.
        """
        del zoom

        try:
            spec = _parse_uri(uri)
        except ValueError as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return
        if not isinstance(auth, AwsAuth):
            yield error(
                ErrorKind.AUTH_FAILED,
                message="kinesis requires a per-call AwsAuth credential",
                locator=uri,
            )
            return

        counter = SequenceCounter()
        consumer = self._consume(spec, auth)
        try:
            async for message in consumer:
                yield build_message_result(
                    uri=uri,
                    namespace=SOURCE_NAMESPACE,
                    content=message["content"],
                    fields=message["fields"],
                    counter=counter,
                )
        except Exception as exc:  # noqa: BLE001 - boundary: returned as a typed Error
            yield from_exception(exc, kind=ErrorKind.TRANSIENT, locator=uri)
        finally:
            await consumer.aclose()

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
                "kinesis:// is an unbounded source and cannot be collected; "
                "iterate stream() instead of calling fetch()"
            ),
            locator=uri,
        )

    async def _consume(
        self, spec: _KinesisSpec, auth: AwsAuth
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Poll Kinesis and yield raw message dicts (the broker seam).

        Production builds a boto3 client from the per-call credential and polls
        ``GetRecords`` on a worker thread; tests replace this with a scripted
        async generator. Each yielded dict is
        ``{"content": str, "fields": {...}}`` where ``fields`` includes the
        resume ``sequence_number``.
        """
        import boto3

        parts = NormalizedAuthResolver().resolve_aws(auth)
        client = boto3.client(
            "kinesis",
            aws_access_key_id=parts.get("access_key_id"),
            aws_secret_access_key=parts.get("secret_access_key"),
            aws_session_token=parts.get("session_token"),
            region_name=parts.get("region") or spec.region,
        )
        shard_id = spec.shard
        if shard_id is None:
            shard_id = await asyncio.to_thread(self._first_shard, client, spec.stream)
        shard_iterator = await asyncio.to_thread(self._shard_iterator, client, spec, shard_id)

        while shard_iterator is not None:
            response = await asyncio.to_thread(
                client.get_records, ShardIterator=shard_iterator, Limit=100
            )
            records = response.get("Records", [])
            for record in records:
                yield self._record_to_message(record, spec.stream, shard_id)
            shard_iterator = response.get("NextShardIterator")
            if not records:
                await asyncio.sleep(self.poll_interval)

    @staticmethod
    def _first_shard(client: Any, stream: str) -> str:
        """Return the first shard id of a stream (used when ``?shard=`` is absent)."""
        description = client.describe_stream(StreamName=stream)
        shards = description["StreamDescription"]["Shards"]
        return str(shards[0]["ShardId"])

    @staticmethod
    def _shard_iterator(client: Any, spec: _KinesisSpec, shard_id: str) -> str:
        """Build the starting shard iterator for the spec's position."""
        if spec.after:
            kwargs = {
                "ShardIteratorType": "AFTER_SEQUENCE_NUMBER",
                "StartingSequenceNumber": spec.after,
            }
        elif spec.at:
            kwargs = {"ShardIteratorType": "AT_SEQUENCE_NUMBER", "StartingSequenceNumber": spec.at}
        else:
            kwargs = {"ShardIteratorType": "LATEST"}
        response = client.get_shard_iterator(StreamName=spec.stream, ShardId=shard_id, **kwargs)
        return str(response["ShardIterator"])

    @staticmethod
    def _record_to_message(record: dict[str, Any], stream: str, shard_id: str) -> dict[str, Any]:
        """Map one Kinesis record onto a raw message dict."""
        data = record.get("Data", b"")
        content = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        arrival = record.get("ApproximateArrivalTimestamp")
        timestamp = arrival.isoformat() if isinstance(arrival, datetime) else None
        return {
            "content": content,
            "fields": {
                "sequence_number": record.get("SequenceNumber"),
                "partition_key": record.get("PartitionKey"),
                "shard": shard_id,
                "stream": stream,
                "timestamp": timestamp,
            },
        }
