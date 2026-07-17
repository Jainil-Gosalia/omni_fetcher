"""Stateless, host-side retry helpers for the v1 contract.

The v1 contract returns expected failures as typed values, so retrying is a
*host* decision made on ``Result`` values -- never an exception-driven loop
inside a connector. This module provides that host-side policy:

- :class:`RetryPolicy` -- a frozen description of when and how to retry
  (attempt cap, exponential backoff with cap and jitter, and the set of
  retryable ``ErrorKind``\\ s, defaulting to ``TRANSIENT`` + ``RATE_LIMITED``).
- :func:`fetch_with_retry` -- a pure wrapper that re-invokes ``fetch()`` on a
  fetcher or orchestrator while the returned value is a retryable ``Error``,
  and returns the final ``Result`` unchanged.

Statelessness contract: the policy is immutable, the wrapper keeps all
per-call state in local variables, and sleeping goes through an injectable
async ``sleep`` so tests never wait. ``Success`` and ``Partial`` are never
retried -- data was delivered -- and non-retryable errors pass through after
a single attempt. Nothing here raises for an expected failure.
"""

from __future__ import annotations

import asyncio
import random
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)
from urllib.parse import parse_qs, urlencode

from pydantic import BaseModel, ConfigDict, Field

from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Result, Success
from omni_fetcher.v1.zoom import ZoomSpec

#: Error kinds retried by default: transport blips and rate limiting.
DEFAULT_RETRYABLE_KINDS: frozenset[ErrorKind] = frozenset(
    {ErrorKind.TRANSIENT, ErrorKind.RATE_LIMITED}
)


class _FetchTarget(Protocol):
    """Anything exposing the contract ``fetch()`` (fetcher or orchestrator)."""

    def fetch(self, uri: str, **kwargs: Any) -> Awaitable[Result]: ...


class _StreamTarget(Protocol):
    """Anything exposing the contract ``stream()`` (fetcher or orchestrator)."""

    def stream(self, uri: str, **kwargs: Any) -> AsyncIterator[Result]: ...


class RetryPolicy(BaseModel):
    """
    A frozen description of when and how to retry a fetch
    ===============================================
    Declares the attempt cap, the exponential backoff shape (initial delay,
    multiplier, cap, jitter fraction), and which ``ErrorKind`` values are
    worth retrying. Immutable after construction, so one policy instance can
    be shared safely across concurrent calls and tenants.
    ===============================================
    NOTE:
        1. ``Success`` and ``Partial`` results are never retried regardless
           of policy -- data was delivered.
        2. Jitter is uniform in ``[0, jitter * delay]`` and added on top of
           the capped exponential delay.

    Attributes
    ----------
        max_attempts:
            Total number of ``fetch()`` invocations allowed (>= 1).
        initial_delay:
            Delay in seconds before the second attempt.
        max_delay:
            Upper bound in seconds for any single backoff delay (pre-jitter).
        exponential_base:
            Multiplier applied to the delay after each attempt.
        jitter:
            Fraction of the delay added as uniform random jitter (0 disables).
        retryable_kinds:
            The ``ErrorKind`` values that trigger a retry.

    Methods
    -------
        delay_for:
    """

    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=3, ge=1)
    initial_delay: float = Field(default=0.5, ge=0.0)
    max_delay: float = Field(default=30.0, ge=0.0)
    exponential_base: float = Field(default=2.0, ge=1.0)
    jitter: float = Field(default=0.0, ge=0.0)
    retryable_kinds: frozenset[ErrorKind] = DEFAULT_RETRYABLE_KINDS

    def delay_for(self, attempt: int) -> float:
        """
        Compute the backoff delay after a given (1-based) failed attempt

        Parameters
        ----------
            attempt:
                The number of attempts completed so far (1 after the first
                failure).

        Return
        ------
            delay:
                Seconds to wait before the next attempt: the capped
                exponential delay plus uniform jitter.
        """
        base = min(
            self.initial_delay * self.exponential_base ** (attempt - 1),
            self.max_delay,
        )
        if self.jitter:
            base += random.uniform(0.0, self.jitter * base)
        return base


async def fetch_with_retry(
    target: _FetchTarget,
    uri: str,
    *,
    policy: Optional[RetryPolicy] = None,
    auth: Optional[AuthCredential] = None,
    zoom: Optional[ZoomSpec] = None,
    tags: Optional[Sequence[str]] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Result:
    """
    Fetch through a fetcher or orchestrator, retrying retryable errors

    Invokes ``target.fetch(uri, ...)`` up to ``policy.max_attempts`` times,
    sleeping the policy's backoff between attempts, while the returned value
    is an ``Error`` whose kind is retryable. The final ``Result`` -- whatever
    its state -- is returned unchanged; nothing is raised for expected
    failures and no state outlives the call.

    NOTE:
        1. ``tags`` is forwarded only when supplied; plain fetchers do not
           accept it (it is an orchestrator parameter).
        2. ``sleep`` is injectable so tests can record delays without
           waiting.

    Parameters
    ----------
        target:
            A ``BaseFetcher`` instance or v1 ``OmniFetcher`` orchestrator.
        uri:
            The source URI to fetch.
        policy:
            The retry policy; defaults to ``RetryPolicy()``.
        auth:
            Optional per-call credential, forwarded verbatim.
        zoom:
            Optional per-atom-type zoom spec, forwarded verbatim.
        tags:
            Optional caller tags (orchestrator targets only).
        sleep:
            Async sleeper used between attempts.

    Return
    ------
        result:
            The final ``Result`` produced by ``target.fetch``.
    """
    active_policy = policy if policy is not None else RetryPolicy()
    kwargs: dict[str, Any] = {"auth": auth, "zoom": zoom}
    if tags is not None:
        kwargs["tags"] = tags

    attempt = 1
    while True:
        result = await target.fetch(uri, **kwargs)
        is_retryable = isinstance(result, Error) and result.kind in active_policy.retryable_kinds
        if not is_retryable or attempt >= active_policy.max_attempts:
            return result
        await sleep(active_policy.delay_for(attempt))
        attempt += 1


# Type of a caller-supplied resume-URI deriver: (original uri, last item)
# -> the URI to reopen the stream with.
ResumeDeriver = Callable[[str, Result], str]


def _with_query_params(uri: str, updates: Dict[str, str]) -> str:
    """Return ``uri`` with the given query parameters set/replaced.

    Custom-scheme safe: splits on the first ``?`` rather than relying on
    scheme-aware netloc parsing, so ``tail://`` and ``kafka://`` URIs
    round-trip cleanly.
    """
    base, _, query = uri.partition("?")
    params = {key: values[-1] for key, values in parse_qs(query).items()}
    params.update(updates)
    encoded = urlencode(params)
    return f"{base}?{encoded}" if encoded else base


def _source_extra(result: Result) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return the ``(namespace, fields)`` of a data item's source_extra.

    Only the first namespace is considered -- streaming connectors emit a
    single-namespace node per item. Returns ``None`` for an ``Error`` or a
    node without ``source_extra``.
    """
    if not isinstance(result, (Success, Partial)):
        return None
    extra = result.tree.metadata.source_extra or {}
    for namespace, fields in extra.items():
        if isinstance(fields, dict):
            return namespace, fields
    return None


async def stream_with_restart(
    target: _StreamTarget,
    uri: str,
    *,
    policy: Optional[RetryPolicy] = None,
    auth: Optional[AuthCredential] = None,
    zoom: Optional[ZoomSpec] = None,
    tags: Optional[Sequence[str]] = None,
    resume: Optional[ResumeDeriver] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[Result]:
    """
    Stream through a fetcher/orchestrator, resuming across retryable ends

    Iterates ``target.stream(uri, ...)`` and forwards every data item
    (``Success`` / ``Partial``) to the consumer. When the inner stream
    ends with a *retryable* ``Error``, that error is swallowed: the helper
    sleeps the policy backoff and reopens the stream from a **resume URI**
    derived from the last data item's position -- so a dropped log tail or
    broker connection continues where it left off. Non-retryable terminal
    errors, and the final retryable error once attempts are exhausted, are
    yielded and end iteration.

    Resume derivation (unless a ``resume`` deriver overrides it):

    - ``source_extra["tail"].byte_offset`` -> ``?from=<byte>``
    - ``source_extra["kafka"]`` positions accumulated per partition ->
      ``?offsets=<partition>:<offset+1>,...`` (resume AFTER each consumed
      message)
    - ``source_extra["redis"].entry_id`` -> ``?offset=<entry-id>``
    - ``source_extra["websocket"|"sse"].sequence`` -> ``?sequence=<n+1>``
    - ``source_extra["postgres"].slot`` -> ``?slot=<name>`` (the slot's
      ``confirmed_flush_lsn`` is the resume position; no LSN in the URI)
    - no data item seen yet -> the original URI

    NOTE:
        1. All resume URIs are derived from the *original* ``uri`` with the
           relevant query parameters replaced, never compounded.
        2. The Kafka offset accumulator persists across restarts, so
           partitions seen in earlier attempts keep their resume point.
        3. ``sleep`` is injectable so tests record backoff without waiting;
           closing the returned iterator closes the inner stream.

    Parameters
    ----------
        target:
            A ``BaseFetcher`` instance or v1 ``OmniFetcher`` orchestrator.
        uri:
            The source URI to stream.
        policy:
            The restart policy (reused ``RetryPolicy``); defaults to
            ``RetryPolicy()``.
        auth:
            Optional per-call credential, forwarded verbatim.
        zoom:
            Optional per-atom-type zoom spec, forwarded verbatim.
        tags:
            Optional caller tags (orchestrator targets only).
        resume:
            Optional ``(uri, last_item) -> resume_uri`` override.
        sleep:
            Async sleeper used between restarts.

    Return
    ------
        results:
            An async iterator of ``Result`` items spanning restarts.
    """
    active_policy = policy if policy is not None else RetryPolicy()
    kwargs: Dict[str, Any] = {"auth": auth, "zoom": zoom}
    if tags is not None:
        kwargs["tags"] = tags

    kafka_offsets: Dict[int, int] = {}
    last_item: Optional[Result] = None
    attempt = 1
    current_uri = uri

    while True:
        inner = target.stream(current_uri, **kwargs)
        terminal_error: Optional[Error] = None
        try:
            async for item in inner:
                if isinstance(item, Error):
                    terminal_error = item
                    break
                last_item = item
                extra = _source_extra(item)
                if extra is not None and extra[0] == "kafka":
                    fields = extra[1]
                    partition = fields.get("partition")
                    offset = fields.get("offset")
                    if isinstance(partition, int) and isinstance(offset, int):
                        kafka_offsets[partition] = offset
                yield item
        finally:
            await inner.aclose()  # type: ignore[attr-defined]

        if terminal_error is None:
            # The inner stream ended without an error (bounded source or a
            # clean close). Nothing to resume.
            return

        retryable = terminal_error.kind in active_policy.retryable_kinds
        if not retryable or attempt >= active_policy.max_attempts:
            yield terminal_error
            return

        await sleep(active_policy.delay_for(attempt))
        attempt += 1
        current_uri = _resume_uri(uri, last_item, kafka_offsets, resume)


def _resume_uri(
    uri: str,
    last_item: Optional[Result],
    kafka_offsets: Dict[int, int],
    resume: Optional[ResumeDeriver],
) -> str:
    """Derive the URI to reopen a dropped stream with (see stream_with_restart)."""
    if last_item is None:
        return uri
    if resume is not None:
        return resume(uri, last_item)

    extra = _source_extra(last_item)
    if extra is None:
        return uri
    namespace, fields = extra
    if namespace == "tail":
        byte_offset = fields.get("byte_offset")
        if isinstance(byte_offset, int):
            return _with_query_params(uri, {"from": str(byte_offset)})
    elif namespace == "kafka" and kafka_offsets:
        pairs = ",".join(
            f"{partition}:{offset + 1}" for partition, offset in sorted(kafka_offsets.items())
        )
        return _with_query_params(uri, {"offsets": pairs})
    elif namespace == "redis":
        entry_id = fields.get("entry_id")
        if isinstance(entry_id, str):
            return _with_query_params(uri, {"offset": entry_id})
    elif namespace in ("websocket", "sse"):
        sequence = fields.get("sequence")
        if isinstance(sequence, int):
            return _with_query_params(uri, {"sequence": str(sequence + 1)})
    elif namespace == "postgres":
        slot = fields.get("slot")
        if isinstance(slot, str):
            return _with_query_params(uri, {"slot": slot})
    return uri
