"""Caching layer for OmniFetcher."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional


class CacheBackend:
    """Base cache backend."""

    async def get(self, key: str) -> Optional[Any]:
        raise NotImplementedError

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def clear(self) -> None:
        raise NotImplementedError


class FileCacheBackend(CacheBackend):
    """File-based cache backend."""

    def __init__(self, cache_dir: str = ".omni_fetcher_cache", default_ttl: int = 3600):
        self.cache_dir = Path(cache_dir)
        self.default_ttl = default_ttl
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, key: str) -> Path:
        """Get file path for cache key."""
        key_hash = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / f"{key_hash}.json"

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        cache_path = self._get_cache_path(key)

        if not cache_path.exists():
            return None

        try:
            data = json.loads(cache_path.read_text())

            # Check expiration
            if data.get("expires_at"):
                expires = datetime.fromisoformat(data["expires_at"])
                if datetime.now() > expires:
                    await self.delete(key)
                    return None

            return data.get("value")
        except Exception:
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache."""
        cache_path = self._get_cache_path(key)

        ttl = ttl or self.default_ttl
        expires_at = (datetime.now() + timedelta(seconds=ttl)).isoformat()

        data = {
            "value": value,
            "created_at": datetime.now().isoformat(),
            "expires_at": expires_at,
        }

        cache_path.write_text(json.dumps(data, default=str))

    async def delete(self, key: str) -> None:
        """Delete value from cache."""
        cache_path = self._get_cache_path(key)

        if cache_path.exists():
            cache_path.unlink()

    async def clear(self) -> None:
        """Clear all cache."""
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()


class MemoryCacheBackend(CacheBackend):
    """In-memory cache backend."""

    def __init__(self, default_ttl: int = 3600):
        self.default_ttl = default_ttl
        self._cache: dict[str, dict] = {}

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        if key not in self._cache:
            return None

        data = self._cache[key]

        # Check expiration
        if data.get("expires_at"):
            expires = datetime.fromisoformat(data["expires_at"])
            if datetime.now() > expires:
                await self.delete(key)
                return None

        return data.get("value")

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache."""
        ttl = ttl or self.default_ttl
        expires_at = (datetime.now() + timedelta(seconds=ttl)).isoformat()

        self._cache[key] = {
            "value": value,
            "created_at": datetime.now().isoformat(),
            "expires_at": expires_at,
        }

    async def delete(self, key: str) -> None:
        """Delete value from cache."""
        if key in self._cache:
            del self._cache[key]

    async def clear(self) -> None:
        """Clear all cache."""
        self._cache.clear()


def get_cache_key(uri: str, **kwargs: str) -> str:
    """Generate cache key from URI and options."""
    key_parts = [uri]
    for k, v in sorted(kwargs.items()):
        key_parts.append(f"{k}={v}")

    return hashlib.sha256("|".join(key_parts).encode()).hexdigest()


from omni_fetcher.cache.redis import RedisCacheBackend

__all__ = [
    "CacheBackend",
    "FileCacheBackend",
    "MemoryCacheBackend",
    "RedisCacheBackend",
    "get_cache_key",
]
