"""External-behaviour tests for the v1 ``kafka`` connector (issue 03).

No broker anywhere: a scripted fake consumer is injected through the
``_make_consumer`` seam (recording assignment, seeks, commits, and stop),
and the aiokafka availability flag is monkeypatched so the suite runs
without the ``kafka`` extra installed:

- each message is one ``Success`` with the contract's node shape (kind
  ``message``, decoded value as a plain Text atom,
  topic/partition/offset/key/timestamp in ``source_extra["kafka"]``);
- stateless mode assigns+seeks per ``offset=`` / per-partition
  ``offsets=`` (which wins) and never commits; ``group=`` subscribes and
  commits after each yielded message;
- broker failures end the stream with one typed TRANSIENT and stop the
  consumer; abandonment stops it too;
- ``fetch()`` is a typed UNSUPPORTED; a missing extra is a typed
  UNSUPPORTED naming it; malformed URIs are INVALID_INPUT.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Dict, List, Optional, Union

import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors import kafka as kafka_module
from omni_fetcher.v1.connectors.kafka import KafkaConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Result, Success

pytestmark = pytest.mark.asyncio

URI = "kafka://broker.local:9092/events"


class _FakeRecord:
    def __init__(
        self,
        offset: int,
        value: bytes,
        *,
        partition: int = 0,
        key: Optional[bytes] = None,
        timestamp: int = 1_760_000_000_000,
        topic: str = "events",
    ) -> None:
        self.topic = topic
        self.partition = partition
        self.offset = offset
        self.key = key
        self.value = value
        self.timestamp = timestamp


class _FakeConsumer:
    """Scripted ``_Consumer`` recording every interaction."""

    def __init__(
        self,
        records: List[_FakeRecord],
        *,
        partitions: Optional[List[int]] = None,
        fail_after: Optional[int] = None,
    ) -> None:
        self._records = list(records)
        self._partitions = partitions if partitions is not None else [0, 1]
        self._fail_after = fail_after
        self.seeks: Optional[Dict[int, Union[str, int]]] = None
        self.subscribed: Optional[str] = None
        self.commits = 0
        self.stopped = False
        self.delivered = 0

    async def partition_ids(self, topic: str) -> List[int]:
        return list(self._partitions)

    async def assign_and_seek(self, topic: str, starts: Dict[int, Union[str, int]]) -> None:
        self.seeks = dict(starts)

    async def subscribe(self, topic: str) -> None:
        self.subscribed = topic

    async def getone(self) -> _FakeRecord:
        if self._fail_after is not None and self.delivered >= self._fail_after:
            raise ConnectionError("broker went away")
        if not self._records:
            await asyncio.sleep(3600)  # a quiet topic blocks forever
        self.delivered += 1
        return self._records.pop(0)

    async def commit(self) -> None:
        self.commits += 1

    async def stop(self) -> None:
        self.stopped = True


def _connector_with(fake: _FakeConsumer, monkeypatch: pytest.MonkeyPatch) -> KafkaConnector:
    """A connector whose broker seam returns the scripted fake."""
    monkeypatch.setattr(kafka_module, "AIOKAFKA_AVAILABLE", True)
    connector = KafkaConnector()

    async def make_consumer(spec, auth):
        return fake

    connector._make_consumer = make_consumer  # type: ignore[method-assign]
    return connector


async def _collect(stream: AsyncIterator[Result], count: int) -> list[Result]:
    items: list[Result] = []

    async def _run() -> None:
        async for item in stream:
            items.append(item)
            if len(items) >= count:
                break

    try:
        await asyncio.wait_for(_run(), timeout=8.0)
    finally:
        await stream.aclose()  # type: ignore[attr-defined]
    return items


# ---------------------------------------------------------------------------
# Message mapping


async def test_messages_map_onto_canonical_message_nodes(monkeypatch) -> None:
    """Each record is one Success with the contract's node shape."""
    fake = _FakeConsumer(
        [
            _FakeRecord(7, b"first payload", key=b"k-1"),
            _FakeRecord(8, b"second payload", partition=1),
        ]
    )
    connector = _connector_with(fake, monkeypatch)

    items = await _collect(connector.stream(URI), 2)

    first, second = items
    assert isinstance(first, Success) and isinstance(second, Success)
    node = first.tree
    assert node.metadata.kind == "message"
    atom = node.find_atoms(AtomKind.TEXT)[0]
    assert atom.content == "first payload"
    # A broker payload is arbitrary decoded bytes (routinely JSON), so the
    # connector asserts no syntax for it. PLAIN would claim it is prose and
    # license zoom to split it on sentence punctuation.
    assert atom.format == TextFormat.OPAQUE

    extra = node.metadata.source_extra["kafka"]
    assert extra["topic"] == "events"
    assert extra["partition"] == 0
    assert extra["offset"] == 7
    assert extra["key"] == "k-1"
    assert extra["timestamp"].startswith("2025") or extra["timestamp"]

    assert second.tree.metadata.source_extra["kafka"]["partition"] == 1
    seq_one = first.tree.metadata.temporal.sequence
    seq_two = second.tree.metadata.temporal.sequence
    assert seq_one is not None and seq_two is not None and seq_two > seq_one


# ---------------------------------------------------------------------------
# Start positions (stateless mode)


async def test_default_start_is_latest_on_every_partition(monkeypatch) -> None:
    """No offset param: every partition seeks 'latest'; nothing commits."""
    fake = _FakeConsumer([_FakeRecord(1, b"x")])
    connector = _connector_with(fake, monkeypatch)

    await _collect(connector.stream(URI), 1)

    assert fake.seeks == {0: "latest", 1: "latest"}
    assert fake.subscribed is None
    assert fake.commits == 0


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?offset=earliest", {0: "earliest", 1: "earliest"}),
        ("?offset=5", {0: 5, 1: 5}),
    ],
)
async def test_offset_param_seeks_accordingly(monkeypatch, query, expected) -> None:
    """offset= applies the named or absolute start to all partitions."""
    fake = _FakeConsumer([_FakeRecord(1, b"x")])
    connector = _connector_with(fake, monkeypatch)

    await _collect(connector.stream(URI + query), 1)

    assert fake.seeks == expected


async def test_per_partition_offsets_win_over_offset(monkeypatch) -> None:
    """offsets=p:o pairs override offset= for the named partitions."""
    fake = _FakeConsumer([_FakeRecord(1, b"x")])
    connector = _connector_with(fake, monkeypatch)

    await _collect(connector.stream(URI + "?offset=earliest&offsets=0:5,1:9"), 1)

    assert fake.seeks == {0: 5, 1: 9}


# ---------------------------------------------------------------------------
# Group mode (D7)


async def test_group_mode_subscribes_and_commits_after_each_message(
    monkeypatch,
) -> None:
    """group= subscribes and commits each delivered message (at-least-once).

    A commit runs once the consumer advances past a message, so the two
    records are both committed by the time the (fail-terminated) stream
    ends -- proving commit-after-delivery without a broker.
    """
    fake = _FakeConsumer([_FakeRecord(1, b"a"), _FakeRecord(2, b"b")], fail_after=2)
    connector = _connector_with(fake, monkeypatch)

    items = [item async for item in connector.stream(URI + "?group=g1")]

    assert [_content for _content in items[:2]]  # two data items delivered
    assert isinstance(items[0], Success) and isinstance(items[1], Success)
    assert isinstance(items[2], Error)
    assert fake.subscribed == "events"
    assert fake.seeks is None
    assert fake.commits == 2


# ---------------------------------------------------------------------------
# Failure + cleanup contract


async def test_broker_failure_is_one_transient_and_consumer_stops(
    monkeypatch,
) -> None:
    """A consume error yields one typed TRANSIENT, ends, and stops."""
    fake = _FakeConsumer([_FakeRecord(1, b"a")], fail_after=1)
    connector = _connector_with(fake, monkeypatch)

    items = [item async for item in connector.stream(URI)]

    assert len(items) == 2
    assert isinstance(items[0], Success)
    assert isinstance(items[1], Error)
    assert items[1].kind == ErrorKind.TRANSIENT
    assert fake.stopped


async def test_abandoned_stream_stops_the_consumer(monkeypatch) -> None:
    """Breaking iteration mid-stream stops the consumer."""
    fake = _FakeConsumer([_FakeRecord(1, b"a"), _FakeRecord(2, b"b")])
    connector = _connector_with(fake, monkeypatch)

    stream = connector.stream(URI)
    first = await stream.__anext__()  # type: ignore[attr-defined]
    assert isinstance(first, Success)
    await stream.aclose()  # type: ignore[attr-defined]

    assert fake.stopped


# ---------------------------------------------------------------------------
# Stream-only + gating contract


async def test_fetch_is_typed_unsupported() -> None:
    """fetch() fails fast with UNSUPPORTED, naming stream()."""
    result = await KafkaConnector().fetch(URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED
    assert "stream" in result.message


async def test_missing_extra_is_typed_unsupported(monkeypatch) -> None:
    """Without aiokafka, streaming yields one UNSUPPORTED naming the extra."""
    monkeypatch.setattr(kafka_module, "AIOKAFKA_AVAILABLE", False)

    items = [item async for item in KafkaConnector().stream(URI)]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.UNSUPPORTED
    assert "kafka" in items[0].message


async def test_malformed_uri_is_invalid_input(monkeypatch) -> None:
    """A kafka:// URI without a topic is a typed INVALID_INPUT."""
    monkeypatch.setattr(kafka_module, "AIOKAFKA_AVAILABLE", True)

    items = [item async for item in KafkaConnector().stream("kafka://host-only")]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind == ErrorKind.INVALID_INPUT
