"""Retry utilities for OmniFetcher."""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any, Callable, Optional, Type

from omni_fetcher.core.exceptions import FetchError


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        exponential_base: float = 2.0,
        retry_on: tuple = (Exception,),
    ):
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.retry_on = retry_on


def with_retry(config: Optional[RetryConfig] = None):
    """Decorator to add retry logic to async functions."""
    if config is None:
        config = RetryConfig()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(config.max_attempts):
                try:
                    return await func(*args, **kwargs)
                except config.retry_on as e:
                    last_exception = e

                    if attempt < config.max_attempts - 1:
                        delay = min(
                            config.initial_delay * (config.exponential_base**attempt),
                            config.max_delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        raise FetchError(
                            uri=args[1] if len(args) > 1 else str(args),
                            reason=f"Failed after {config.max_attempts} attempts: {e}",
                        )

            raise last_exception

        return wrapper

    return decorator


class RateLimiter:
    """Rate limiter for API calls."""

    def __init__(self, calls_per_second: float = 1.0):
        self.calls_per_second = calls_per_second
        self.min_interval = 1.0 / calls_per_second
        self._last_call = 0.0

    async def acquire(self) -> None:
        """Wait if necessary to maintain rate limit."""
        import time

        now = time.monotonic()
        time_since_last = now - self._last_call

        if time_since_last < self.min_interval:
            await asyncio.sleep(self.min_interval - time_since_last)

        self._last_call = time.monotonic()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        pass
