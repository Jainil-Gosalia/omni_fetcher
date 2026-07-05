"""External-behaviour tests for ``omni_fetcher.v1.retry`` (issue 010).

Scripted fetchers + an injected sleeper prove the policy without waiting:

- attempt counting and the exact backoff sequence (cap included);
- non-retryable kinds and ``Partial`` pass through after one attempt;
- exhausted attempts return the last ``Error`` unchanged;
- the retryable-kind set is configurable;
- jitter stays within its documented bounds;
- one frozen policy serves interleaved concurrent calls without sharing
  state, including through the real orchestrator (which also proves the
  ``tags`` pass-through).
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

import pytest

from omni_fetcher.v1.atoms import Text
from omni_fetcher.v1.auth import AuthCredential, BearerAuth
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.orchestrator import OmniFetcher
from omni_fetcher.v1.registry import FrozenRegistry, SourceDefinition
from omni_fetcher.v1.result import (
    Error,
    Partial,
    Result,
    Success,
    error,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.retry import RetryPolicy, fetch_with_retry
from omni_fetcher.v1.zoom import ZoomSpec

pytestmark = pytest.mark.asyncio


def _ok(content: str = "ok") -> Result:
    return success(build_node(kind="doc", atoms=[Text(content=content)]))


def _err(kind: ErrorKind) -> Result:
    return error(kind=kind, message=kind.value, locator="mem://x")


class _Scripted(BaseFetcher):
    """Replays a fixed list of results across successive fetch() calls."""

    def __init__(self, items: list[Result]) -> None:
        self._items = list(items)
        self.calls = 0

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        self.calls += 1
        yield self._items.pop(0)


class _Sleeper:
    """Records requested delays instead of sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


async def test_retries_transient_until_success_with_backoff() -> None:
    """Transient errors are retried on the exact backoff sequence."""
    fetcher = _Scripted([_err(ErrorKind.TRANSIENT), _err(ErrorKind.TRANSIENT), _ok()])
    sleeper = _Sleeper()
    policy = RetryPolicy(max_attempts=3, initial_delay=1.0, exponential_base=2.0)

    result = await fetch_with_retry(fetcher, "mem://x", policy=policy, sleep=sleeper)

    assert isinstance(result, Success)
    assert fetcher.calls == 3
    assert sleeper.delays == [1.0, 2.0]


async def test_backoff_delay_is_capped() -> None:
    """Delays never exceed max_delay (pre-jitter)."""
    fetcher = _Scripted([_err(ErrorKind.TRANSIENT)] * 5)
    sleeper = _Sleeper()
    policy = RetryPolicy(max_attempts=5, initial_delay=1.0, exponential_base=2.0, max_delay=3.0)

    result = await fetch_with_retry(fetcher, "mem://x", policy=policy, sleep=sleeper)

    assert isinstance(result, Error)
    assert sleeper.delays == [1.0, 2.0, 3.0, 3.0]


async def test_exhausted_attempts_return_last_error_unchanged() -> None:
    """After the cap, the final Error is returned as-is."""
    last = _err(ErrorKind.RATE_LIMITED)
    fetcher = _Scripted([_err(ErrorKind.TRANSIENT), last])
    policy = RetryPolicy(max_attempts=2, initial_delay=0.0)

    result = await fetch_with_retry(fetcher, "mem://x", policy=policy, sleep=_Sleeper())

    assert result is last


async def test_non_retryable_kind_passes_through_after_one_attempt() -> None:
    """NOT_FOUND is returned immediately with no sleeping."""
    fetcher = _Scripted([_err(ErrorKind.NOT_FOUND), _ok()])
    sleeper = _Sleeper()

    result = await fetch_with_retry(fetcher, "mem://x", sleep=sleeper)

    assert isinstance(result, Error) and result.kind == ErrorKind.NOT_FOUND
    assert fetcher.calls == 1
    assert sleeper.delays == []


async def test_partial_is_never_retried() -> None:
    """Partial delivered data is returned immediately, not retried."""
    node = build_node(kind="doc", atoms=[Text(content="some")])
    partial_result = partial(
        node, [gap(kind=ErrorKind.TRANSIENT, locator="mem://x", detail="hole")]
    )
    fetcher = _Scripted([partial_result, _ok()])
    sleeper = _Sleeper()

    result = await fetch_with_retry(fetcher, "mem://x", sleep=sleeper)

    assert isinstance(result, Partial)
    assert fetcher.calls == 1
    assert sleeper.delays == []


async def test_retryable_kinds_are_configurable() -> None:
    """A custom kind set retries what the caller declares retryable."""
    fetcher = _Scripted([_err(ErrorKind.NOT_FOUND), _ok()])
    policy = RetryPolicy(
        max_attempts=2,
        initial_delay=0.0,
        retryable_kinds=frozenset({ErrorKind.NOT_FOUND}),
    )

    result = await fetch_with_retry(fetcher, "mem://x", policy=policy, sleep=_Sleeper())

    assert isinstance(result, Success)
    assert fetcher.calls == 2


async def test_jitter_stays_within_documented_bounds() -> None:
    """With jitter, each delay lies in [base, base * (1 + jitter)]."""
    fetcher = _Scripted([_err(ErrorKind.TRANSIENT)] * 4)
    sleeper = _Sleeper()
    policy = RetryPolicy(max_attempts=4, initial_delay=1.0, exponential_base=2.0, jitter=0.5)

    await fetch_with_retry(fetcher, "mem://x", policy=policy, sleep=sleeper)

    expected_bases = [1.0, 2.0, 4.0]
    assert len(sleeper.delays) == 3
    for delay, base in zip(sleeper.delays, expected_bases):
        assert base <= delay <= base * 1.5


async def test_one_policy_serves_interleaved_concurrent_calls() -> None:
    """A shared frozen policy carries no per-call state across coroutines."""
    policy = RetryPolicy(max_attempts=3, initial_delay=0.0)
    flaky = _Scripted([_err(ErrorKind.TRANSIENT), _ok("a")])
    steady = _Scripted([_ok("b")])

    results = await asyncio.gather(
        fetch_with_retry(flaky, "mem://a", policy=policy, sleep=_Sleeper()),
        fetch_with_retry(steady, "mem://b", policy=policy, sleep=_Sleeper()),
    )

    assert all(isinstance(result, Success) for result in results)
    assert flaky.calls == 2 and steady.calls == 1


async def test_works_through_the_real_orchestrator_with_tags() -> None:
    """Retrying through OmniFetcher works and forwards caller tags."""
    calls = {"n": 0}

    class _Flaky(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            calls["n"] += 1
            if calls["n"] == 1:
                yield _err(ErrorKind.TRANSIENT)
            else:
                yield _ok("via orchestrator")

    omni = OmniFetcher(
        FrozenRegistry(
            (SourceDefinition(name="flaky", fetcher_class=_Flaky, uri_patterns=("mem://*",)),)
        )
    )
    policy = RetryPolicy(max_attempts=2, initial_delay=0.0)

    result = await fetch_with_retry(
        omni,
        "mem://x",
        policy=policy,
        auth=BearerAuth(token="t"),
        tags=["tenant-a"],
        sleep=_Sleeper(),
    )

    assert isinstance(result, Success)
    assert calls["n"] == 2
    assert "tenant-a" in result.tree.metadata.tags


async def test_policy_is_immutable() -> None:
    """The policy is frozen; mutation attempts raise."""
    policy = RetryPolicy()

    with pytest.raises(Exception):
        policy.max_attempts = 5  # type: ignore[misc]
