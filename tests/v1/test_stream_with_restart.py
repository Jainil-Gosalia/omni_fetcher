"""External-behaviour tests for ``stream_with_restart`` (issue 04).

Scripted stream targets + an injected sleeper prove the restart machine
without waiting, plus one real-tail integration proving byte-offset resume
against the actual connector:

- retryable stream-ends are swallowed and the stream resumes; the
  consumer sees a seamless item sequence across restarts;
- the injected sleeper records the policy backoff sequence;
- attempts are capped (the final retryable error is yielded, then ends);
- non-retryable terminal errors pass straight through;
- resume URIs are derived per namespace (tail byte_offset -> ?from=,
  kafka accumulated per-partition -> ?offsets=p:o+1) with a ``resume``
  override;
- one frozen policy serves concurrent restarts without shared state;
- a real TailConnector resumes from ``byte_offset`` without duplicating
  already-delivered lines.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, List
from urllib.parse import parse_qs, urlsplit

import pytest

from omni_fetcher.v1.atoms import AtomKind, Text
from omni_fetcher.v1.connectors.tail import TailConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.result import (
    Error,
    Result,
    Success,
    error,
    success,
)
from omni_fetcher.v1.retry import RetryPolicy, stream_with_restart

pytestmark = pytest.mark.asyncio


def _item(content: str, namespace: str = "gen", **fields: Any) -> Result:
    """A Success item carrying source_extra[namespace] fields."""
    node = build_node(
        kind="item",
        atoms=[Text(content=content)],
        source_namespace=namespace,
        source_fields=fields or {"n": content},
    )
    return success(node)


def _err(kind: ErrorKind = ErrorKind.TRANSIENT) -> Result:
    return error(kind=kind, message=kind.value, locator="mem://x")


def _content(item: Result) -> str:
    assert isinstance(item, Success)
    return item.tree.find_atoms(AtomKind.TEXT)[0].content


class _ScriptedTarget:
    """Yields a pre-scripted item list per successive ``stream()`` call."""

    def __init__(self, scripts: List[List[Result]]) -> None:
        self._scripts = scripts
        self.uris: List[str] = []
        self._call = 0

    async def stream(self, uri: str, **kwargs: Any) -> AsyncIterator[Result]:
        self.uris.append(uri)
        script = self._scripts[self._call]
        self._call += 1
        for item in script:
            yield item


class _Sleeper:
    def __init__(self) -> None:
        self.delays: List[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


async def _drain(stream: AsyncIterator[Result]) -> List[Result]:
    return [item async for item in stream]


async def _collect(stream: AsyncIterator[Result], count: int) -> List[Result]:
    items: List[Result] = []

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


def _query(uri: str) -> dict[str, str]:
    return {k: v[-1] for k, v in parse_qs(urlsplit(uri).query).items()}


# ---------------------------------------------------------------------------
# Restart across retryable ends


async def test_resumes_across_retryable_ends_seamlessly() -> None:
    """Items flow seamlessly across restarts; backoff is recorded."""
    target = _ScriptedTarget(
        [
            [_item("a"), _err()],
            [_item("b"), _err()],
            [_item("c")],
        ]
    )
    sleeper = _Sleeper()
    policy = RetryPolicy(max_attempts=3, initial_delay=1.0, exponential_base=2.0)

    items = await _drain(stream_with_restart(target, "mem://x", policy=policy, sleep=sleeper))

    assert [_content(item) for item in items] == ["a", "b", "c"]
    assert sleeper.delays == [1.0, 2.0]
    assert len(target.uris) == 3


async def test_attempt_cap_yields_final_error_and_ends() -> None:
    """After the cap, the final retryable error is surfaced."""
    target = _ScriptedTarget([[_item("a"), _err()], [_item("b"), _err()]])
    sleeper = _Sleeper()
    policy = RetryPolicy(max_attempts=2, initial_delay=0.0)

    items = await _drain(stream_with_restart(target, "mem://x", policy=policy, sleep=sleeper))

    assert [_content(item) for item in items[:2]] == ["a", "b"]
    assert isinstance(items[-1], Error) and items[-1].kind == ErrorKind.TRANSIENT
    assert len(sleeper.delays) == 1


async def test_non_retryable_error_passes_through_without_restart() -> None:
    """A NOT_FOUND terminal error is yielded once; no restart, no sleep."""
    target = _ScriptedTarget([[_item("a"), _err(ErrorKind.NOT_FOUND)]])
    sleeper = _Sleeper()

    items = await _drain(stream_with_restart(target, "mem://x", sleep=sleeper))

    assert _content(items[0]) == "a"
    assert isinstance(items[1], Error) and items[1].kind == ErrorKind.NOT_FOUND
    assert sleeper.delays == []
    assert len(target.uris) == 1


async def test_clean_stream_end_stops_without_error() -> None:
    """A stream that ends without a terminal error simply stops."""
    target = _ScriptedTarget([[_item("a"), _item("b")]])

    items = await _drain(stream_with_restart(target, "mem://x", sleep=_Sleeper()))

    assert [_content(item) for item in items] == ["a", "b"]
    assert len(target.uris) == 1


# ---------------------------------------------------------------------------
# Resume URI derivation


async def test_tail_resume_derives_from_byte_offset() -> None:
    """A tail item's byte_offset becomes ?from= on the restart URI."""
    target = _ScriptedTarget(
        [
            [_item("l1", namespace="tail", byte_offset=42, line_number=1), _err()],
            [_item("l2", namespace="tail", byte_offset=99, line_number=2)],
        ]
    )
    policy = RetryPolicy(max_attempts=2, initial_delay=0.0)

    await _drain(
        stream_with_restart(target, "tail:///log?poll=0.01", policy=policy, sleep=_Sleeper())
    )

    assert _query(target.uris[1])["from"] == "42"
    assert _query(target.uris[1])["poll"] == "0.01"


async def test_kafka_resume_accumulates_per_partition_offsets() -> None:
    """Kafka positions accumulate to ?offsets=p:o+1 across partitions."""
    target = _ScriptedTarget(
        [
            [
                _item("m0", namespace="kafka", partition=0, offset=5),
                _item("m1", namespace="kafka", partition=1, offset=9),
                _err(),
            ],
            [_item("m2", namespace="kafka", partition=0, offset=6)],
        ]
    )
    policy = RetryPolicy(max_attempts=2, initial_delay=0.0)

    await _drain(
        stream_with_restart(target, "kafka://h/t?offset=latest", policy=policy, sleep=_Sleeper())
    )

    assert _query(target.uris[1])["offsets"] == "0:6,1:10"


async def test_redis_resume_derives_from_entry_id() -> None:
    """A Redis item's entry_id becomes ?offset= on the restart URI."""
    target = _ScriptedTarget(
        [
            [_item("m1", namespace="redis", entry_id="1526919030474-0"), _err()],
            [_item("m2", namespace="redis", entry_id="1526919030475-0")],
        ]
    )
    policy = RetryPolicy(max_attempts=2, initial_delay=0.0)

    await _drain(
        stream_with_restart(target, "redis://localhost/mystream?offset=$", policy=policy, sleep=_Sleeper())
    )

    assert _query(target.uris[1])["offset"] == "1526919030474-0"
    assert _query(target.uris[1]).get("db") is None  # Original params preserved


async def test_custom_resume_deriver_overrides_builtins() -> None:
    """A resume callable takes precedence over namespace derivation."""
    target = _ScriptedTarget(
        [
            [_item("l1", namespace="tail", byte_offset=42), _err()],
            [_item("l2", namespace="tail", byte_offset=99)],
        ]
    )
    policy = RetryPolicy(max_attempts=2, initial_delay=0.0)

    await _drain(
        stream_with_restart(
            target,
            "tail:///log",
            policy=policy,
            resume=lambda uri, item: "tail:///log?from=999",
            sleep=_Sleeper(),
        )
    )

    assert _query(target.uris[1])["from"] == "999"


async def test_no_data_before_error_restarts_with_original_uri() -> None:
    """When the first stream errors before any item, resume is the original."""
    target = _ScriptedTarget([[_err()], [_item("a")]])
    policy = RetryPolicy(max_attempts=2, initial_delay=0.0)

    await _drain(
        stream_with_restart(target, "tail:///log?poll=0.01", policy=policy, sleep=_Sleeper())
    )

    assert target.uris[1] == "tail:///log?poll=0.01"


# ---------------------------------------------------------------------------
# Concurrency + real-connector integration


async def test_one_policy_serves_concurrent_restarts() -> None:
    """A shared frozen policy carries no per-call state across coroutines."""
    policy = RetryPolicy(max_attempts=3, initial_delay=0.0)
    left = _ScriptedTarget([[_item("a"), _err()], [_item("b")]])
    right = _ScriptedTarget([[_item("x")]])

    results = await asyncio.gather(
        _drain(stream_with_restart(left, "mem://a", policy=policy, sleep=_Sleeper())),
        _drain(stream_with_restart(right, "mem://b", policy=policy, sleep=_Sleeper())),
    )

    assert [_content(item) for item in results[0]] == ["a", "b"]
    assert [_content(item) for item in results[1]] == ["x"]


class _RestartableTail:
    """Wraps a real TailConnector, injecting one TRANSIENT on the first run."""

    def __init__(self, connector: TailConnector, fail_first_after: int) -> None:
        self._connector = connector
        self._fail_first_after = fail_first_after
        self._calls = 0

    async def stream(self, uri: str, **kwargs: Any) -> AsyncIterator[Result]:
        self._calls += 1
        inject = self._calls == 1
        seen = 0
        inner = self._connector.stream(uri, **kwargs)
        try:
            async for item in inner:
                seen += 1
                yield item
                if inject and seen >= self._fail_first_after:
                    yield error(kind=ErrorKind.TRANSIENT, message="injected drop", locator=uri)
                    return
        finally:
            await inner.aclose()


async def test_real_tail_resumes_without_duplicating_lines(tmp_path: Path) -> None:
    """A real tail resumes from byte_offset -- no line delivered twice."""
    log = tmp_path / "app.log"
    log.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8", newline="")
    uri = "tail://" + str(log).replace("\\", "/") + "?from=start&poll=0.01"

    target = _RestartableTail(TailConnector(), fail_first_after=2)
    policy = RetryPolicy(max_attempts=3, initial_delay=0.0)

    items = await _collect(stream_with_restart(target, uri, policy=policy, sleep=_Sleeper()), 4)

    assert [_content(item) for item in items] == ["one", "two", "three", "four"]
