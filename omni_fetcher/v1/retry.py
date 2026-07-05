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
from typing import Any, Awaitable, Callable, Optional, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Result
from omni_fetcher.v1.zoom import ZoomSpec

#: Error kinds retried by default: transport blips and rate limiting.
DEFAULT_RETRYABLE_KINDS: frozenset[ErrorKind] = frozenset(
    {ErrorKind.TRANSIENT, ErrorKind.RATE_LIMITED}
)


class _FetchTarget(Protocol):
    """Anything exposing the contract ``fetch()`` (fetcher or orchestrator)."""

    def fetch(self, uri: str, **kwargs: Any) -> Awaitable[Result]: ...


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
