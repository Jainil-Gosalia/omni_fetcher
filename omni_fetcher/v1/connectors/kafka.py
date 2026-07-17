"""The canonical ``kafka`` connector -- unbounded queue source for v1.

Consumes a Kafka topic through the v1 contract: each message is one
``Result`` whose tree is a single ``kind="message"`` node carrying one
plain ``Text`` atom (the decoded value) plus resume positions in
``source_extra["kafka"]`` (topic, partition, offset, key, timestamp).
``offset`` is the message's own offset; resuming seeks ``offset + 1`` per
partition (the restart helper derives ``?offsets=`` from it).

Configuration travels in the URI: ``kafka://<host>[:<port>]/<topic>``
(default port 9092) with ``?offset=latest`` (default) | ``earliest`` |
``<n>``, precise per-partition ``?offsets=p:o[,p:o...]`` (wins over
``offset=``), and ``?group=<id>`` opting into committing consumer-group
semantics -- without it the connector assigns+seeks statelessly and
commits nothing (decision D7).

aiokafka is optional (the ``kafka`` extra): this module imports without
it, ``builtin_registry()`` skips the source when it is missing, and
direct use yields a typed ``UNSUPPORTED`` naming the extra. All behavior
flows through a narrow consumer protocol built by the ``_make_consumer``
seam, so tests script a fake and never touch a broker (D10).

Stream-only: ``fetch()`` returns a typed ``UNSUPPORTED`` immediately.
Transport failures mid-stream yield one terminal ``TRANSIENT``; the
consumer is always stopped when iteration ends or is abandoned.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, Union
from urllib.parse import parse_qs

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import (
    SequenceCounter,
    build_node,
    now_utc,
    stamp_temporal,
)
from omni_fetcher.v1.result import Result, error, from_exception, success
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which all descriptive ``kafka`` fields are stored.
SOURCE_NAMESPACE = "kafka"

# Advisory semantic ``kind`` for every node this connector emits.
MESSAGE_KIND = "message"

# Whether the optional aiokafka client is importable (the ``kafka`` extra).
AIOKAFKA_AVAILABLE = importlib.util.find_spec("aiokafka") is not None

_SCHEME = "kafka://"
_DEFAULT_PORT = 9092

# Start-position sentinel type: a named position or an absolute offset.
StartPosition = Union[str, int]


class _Record(Protocol):
    """The message shape the consumer protocol yields (aiokafka-compatible)."""

    topic: str
    partition: int
    offset: int
    key: Optional[bytes]
    value: Optional[bytes]
    timestamp: int  # epoch milliseconds


class _Consumer(Protocol):
    """
    The narrow consumer protocol the stream drives
    ===============================================
    Implemented by the production aiokafka adapter and by test fakes; the
    stream itself never touches aiokafka directly, so unit tests need no
    broker (D10).
    ===============================================
    NOTE:
        1. ``assign_and_seek`` receives the full per-partition start map
           so fakes can record exactly what stateless mode requested.

    Methods
    -------
        partition_ids:
        assign_and_seek:
        subscribe:
        getone:
        commit:
        stop:
    """

    async def partition_ids(self, topic: str) -> List[int]: ...

    async def assign_and_seek(self, topic: str, starts: Dict[int, StartPosition]) -> None: ...

    async def subscribe(self, topic: str) -> None: ...

    async def getone(self) -> _Record: ...

    async def commit(self) -> None: ...

    async def stop(self) -> None: ...


class _KafkaSpec:
    """Parsed ``kafka://`` routing decision."""

    def __init__(
        self,
        bootstrap: str,
        topic: str,
        start: StartPosition,
        per_partition: Optional[Dict[int, int]],
        group: Optional[str],
    ) -> None:
        self.bootstrap = bootstrap
        self.topic = topic
        self.start = start
        self.per_partition = per_partition
        self.group = group


def _parse_uri(uri: str) -> _KafkaSpec:
    """Parse a ``kafka://`` URI into a spec, raising ``ValueError`` when bad."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a kafka:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    location, _, query = remainder.partition("?")
    host_part, _, topic = location.partition("/")
    if not host_part or not topic or "/" in topic:
        raise ValueError(f"kafka:// URI must be kafka://host[:port]/topic: {uri}")
    bootstrap = host_part if ":" in host_part else f"{host_part}:{_DEFAULT_PORT}"

    params = parse_qs(query)
    raw_offset = params.get("offset", ["latest"])[0]
    start: StartPosition
    if raw_offset in ("latest", "earliest"):
        start = raw_offset
    else:
        start = int(raw_offset)  # ValueError -> INVALID_INPUT at the boundary
        if start < 0:
            raise ValueError(f"offset= must be >= 0: {raw_offset}")

    per_partition: Optional[Dict[int, int]] = None
    raw_offsets = params.get("offsets", [None])[0]
    if raw_offsets:
        per_partition = {}
        for pair in raw_offsets.split(","):
            partition_text, _, offset_text = pair.partition(":")
            per_partition[int(partition_text)] = int(offset_text)

    group = params.get("group", [None])[0]
    return _KafkaSpec(
        bootstrap=bootstrap,
        topic=topic,
        start=start,
        per_partition=per_partition,
        group=group,
    )


class KafkaConnector(BaseFetcher):
    """
    Unbounded Kafka topic connector for the v1 contract
    ===============================================
    Consumes messages as canonical per-item ``Result``s, statelessly by
    default (assign+seek, zero commits) with ``?group=`` opting into
    committing consumer-group semantics owned by the host (D7).
    ``fetch()`` is a typed ``UNSUPPORTED``. All broker access goes through
    the ``_make_consumer`` seam.
    ===============================================
    NOTE:
        1. In group mode the connector commits after each yielded message
           (explicit at-least-once after delivery), never before.
        2. Without the ``kafka`` extra the stream yields one typed
           ``UNSUPPORTED`` naming the extra; nothing raises.

    Attributes
    ----------
        timeout:
            Per-connection timeout in seconds for the production client.

    Methods
    -------
        can_handle:
        stream:
        fetch:
    """

    name = SOURCE_NAMESPACE

    def __init__(self, timeout: float = 30.0) -> None:
        """
        Create a Kafka connector

        Parameters
        ----------
            timeout:
                Per-connection timeout in seconds for the underlying
                client.
        """
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI names a Kafka source

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for ``kafka://`` URIs.
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
        Consume a topic and yield one ``Result`` per message, forever

        Stateless mode (no ``group=``) assigns all partitions and seeks
        the requested start (``offsets=`` wins over ``offset=``); group
        mode subscribes and commits after each yielded message. The
        stream ends only on a typed error or consumer abandonment --
        either way the underlying consumer is stopped.

        NOTE:
            1. ``zoom`` is accepted; a single message is its own natural
               granularity (central pruning still applies per item).

        Parameters
        ----------
            uri:
                The ``kafka://host[:port]/topic?...`` source URI.
            auth:
                Optional credential forwarded to the consumer factory.
            zoom:
                Accepted; natural per-message granularity is emitted.

        Return
        ------
            results:
                An unbounded async iterator of ``Result`` items.
        """
        del zoom
        if not AIOKAFKA_AVAILABLE:
            yield error(
                kind=ErrorKind.UNSUPPORTED,
                message=(
                    "aiokafka is not installed; install the 'kafka' extra "
                    "(pip install 'omni_fetcher[kafka]') to use kafka:// sources"
                ),
                locator=uri,
            )
            return

        try:
            spec = _parse_uri(uri)
        except ValueError as exc:
            yield from_exception(
                exc,
                kind=ErrorKind.INVALID_INPUT,
                message="invalid kafka:// URI",
                locator=uri,
            )
            return

        try:
            consumer = await self._make_consumer(spec, auth)
        except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
            yield from_exception(
                exc,
                kind=ErrorKind.TRANSIENT,
                message="could not connect to Kafka",
                locator=uri,
            )
            return

        counter = SequenceCounter()
        try:
            try:
                if spec.group is not None:
                    await consumer.subscribe(spec.topic)
                else:
                    partitions = await consumer.partition_ids(spec.topic)
                    starts: Dict[int, StartPosition] = {
                        partition: spec.start for partition in partitions
                    }
                    if spec.per_partition:
                        starts.update(spec.per_partition)
                    await consumer.assign_and_seek(spec.topic, starts)

                while True:
                    record = await consumer.getone()
                    yield self._message_result(uri, record, counter)
                    if spec.group is not None:
                        await consumer.commit()
            except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
                yield from_exception(
                    exc,
                    kind=ErrorKind.TRANSIENT,
                    message="kafka consume failed",
                    locator=uri,
                )
                return
        finally:
            await consumer.stop()

    async def fetch(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> Result:
        """
        Refuse collection of an unbounded source (typed, immediate)

        Parameters
        ----------
            uri:
                The ``kafka://`` URI whose collection was requested.
            auth:
                Ignored.
            zoom:
                Ignored.

        Return
        ------
            result:
                ``error(UNSUPPORTED)`` directing callers to ``stream()``.
        """
        del auth, zoom
        return error(
            kind=ErrorKind.UNSUPPORTED,
            message=(
                "kafka:// is an unbounded source and cannot be collected; "
                "iterate stream() instead of calling fetch()"
            ),
            locator=uri,
        )

    async def _make_consumer(
        self,
        spec: _KafkaSpec,
        auth: Optional[AuthCredential],
    ) -> _Consumer:
        """Build a started consumer for the spec (the broker seam).

        Production wraps aiokafka in the narrow ``_Consumer`` protocol;
        tests replace this method with a scripted fake. Only ever called
        when ``AIOKAFKA_AVAILABLE`` (or under a test seam).
        """
        return await _AioKafkaAdapter.create(spec, auth, self.timeout)

    def _message_result(
        self,
        uri: str,
        record: _Record,
        counter: SequenceCounter,
    ) -> Result:
        """Map one consumed record onto the canonical per-item Result."""
        value = record.value or b""
        key = record.key.decode("utf-8", errors="replace") if record.key else None
        timestamp = datetime.fromtimestamp(record.timestamp / 1000.0, tz=timezone.utc).isoformat()
        node = build_node(
            kind=MESSAGE_KIND,
            atoms=[
                Text(
                    content=value.decode("utf-8", errors="replace"),
                    # A broker payload is arbitrary bytes: routinely JSON, often
                    # not prose. We decoded it; we make no claim about its syntax.
                    format=TextFormat.OPAQUE,
                )
            ],
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "topic": record.topic,
                "partition": record.partition,
                "offset": record.offset,
                "key": key,
                "timestamp": timestamp,
            },
        )
        stamp_temporal(node, sequence=counter.next(), timestamp=now_utc())
        return success(node)


class _AioKafkaAdapter:
    """Production ``_Consumer`` built on aiokafka (integration-tested only).

    Unit suites never construct this (D10); it exists so the stream's
    protocol has exactly one production implementation.
    """

    def __init__(self, consumer: Any) -> None:
        self._consumer = consumer

    @classmethod
    async def create(
        cls,
        spec: _KafkaSpec,
        auth: Optional[AuthCredential],
        timeout: float,
    ) -> "_AioKafkaAdapter":
        """Construct and start an aiokafka consumer for the spec."""
        from aiokafka import AIOKafkaConsumer  # imported only with the extra

        del auth  # SASL/SSL wiring is a follow-up; local brokers need none.
        consumer = AIOKafkaConsumer(
            bootstrap_servers=spec.bootstrap,
            group_id=spec.group,
            enable_auto_commit=False,
            auto_offset_reset=(spec.start if spec.start in ("latest", "earliest") else "latest"),
            request_timeout_ms=int(timeout * 1000),
        )
        await consumer.start()
        return cls(consumer)

    async def partition_ids(self, topic: str) -> List[int]:
        partitions = self._consumer.partitions_for_topic(topic)
        if not partitions:
            # partitions_for_topic reads cached metadata, and a freshly
            # started assign-mode consumer has none -- so discovery comes
            # back empty and getone() would block forever. _wait_on_metadata
            # fetches THIS topic's partition set (consumer.topics() only
            # refreshes the topic *list*, not partition metadata -- verified
            # against a live broker; the scripted unit fake cannot reproduce
            # this staleness). It is aiokafka's own idiom for the same need.
            partitions = await self._consumer._client._wait_on_metadata(topic)
        return sorted(partitions or set())

    async def assign_and_seek(self, topic: str, starts: Dict[int, StartPosition]) -> None:
        from aiokafka import TopicPartition  # imported only with the extra

        assignments = [TopicPartition(topic, partition) for partition in starts]
        self._consumer.assign(assignments)
        for assignment in assignments:
            start = starts[assignment.partition]
            if start == "earliest":
                await self._consumer.seek_to_beginning(assignment)
            elif start == "latest":
                await self._consumer.seek_to_end(assignment)
            else:
                self._consumer.seek(assignment, int(start))

    async def subscribe(self, topic: str) -> None:
        self._consumer.subscribe([topic])

    async def getone(self) -> _Record:
        return await self._consumer.getone()

    async def commit(self) -> None:
        await self._consumer.commit()

    async def stop(self) -> None:
        await self._consumer.stop()
