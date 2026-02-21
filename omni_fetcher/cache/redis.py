"""Redis cache backend for OmniFetcher."""

from __future__ import annotations

import json
from typing import Any, Optional

import redis.asyncio as redis

from omni_fetcher.cache import CacheBackend


class RedisCacheBackend(CacheBackend):
    """Redis-based cache backend."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        prefix: str = "omni_fetcher:",
        default_ttl: int = 3600,
        decode_responses: bool = True,
    ):
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.prefix = prefix
        self.default_ttl = default_ttl
        self.decode_responses = decode_responses
        self._client: Optional[redis.Redis] = None

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                decode_responses=self.decode_responses,
            )
        return self._client

    def _add_prefix(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def _remove_prefix(self, key: str) -> str:
        if key.startswith(self.prefix):
            return key[len(self.prefix) :]
        return key

    def _serialize(self, value: Any) -> str:
        return json.dumps(value, default=str)

    def _deserialize(self, value: Optional[str]) -> Optional[Any]:
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    async def get(self, key: str) -> Optional[Any]:
        try:
            client = await self._get_client()
            prefixed_key = self._add_prefix(key)
            value = await client.get(prefixed_key)
            return self._deserialize(value)
        except redis.RedisError:
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            client = await self._get_client()
            prefixed_key = self._add_prefix(key)
            serialized_value = self._serialize(value)
            actual_ttl = ttl if ttl is not None else self.default_ttl
            await client.set(prefixed_key, serialized_value, ex=actual_ttl)
        except redis.RedisError:
            pass

    async def delete(self, key: str) -> None:
        try:
            client = await self._get_client()
            prefixed_key = self._add_prefix(key)
            await client.delete(prefixed_key)
        except redis.RedisError:
            pass

    async def clear(self) -> None:
        try:
            client = await self._get_client()
            pattern = f"{self.prefix}*"
            cursor = 0
            while True:
                cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
                if keys:
                    await client.delete(*keys)
                if cursor == 0:
                    break
        except redis.RedisError:
            pass

    async def stats(self) -> dict:
        try:
            client = await self._get_client()
            info = await client.info("memory")
            pattern = f"{self.prefix}*"
            cursor = 0
            key_count = 0
            while True:
                cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
                key_count += len(keys)
                if cursor == 0:
                    break

            return {
                "keys": key_count,
                "used_memory": info.get("used_memory_human", "unknown"),
                "connected": True,
                "host": self.host,
                "port": self.port,
                "db": self.db,
            }
        except redis.RedisError:
            return {
                "keys": 0,
                "used_memory": "unknown",
                "connected": False,
                "host": self.host,
                "port": self.port,
                "db": self.db,
            }

    async def ping(self) -> bool:
        try:
            client = await self._get_client()
            return await client.ping()
        except redis.RedisError:
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    async def pipeline(self) -> redis.Pipeline:
        client = await self._get_client()
        return client.pipeline()

    async def execute_pipeline(self, commands: list[tuple[str, list]]) -> list:
        pipeline = await self.pipeline()
        for cmd, args in commands:
            pipeline.execute_command(cmd, *args)
        return await pipeline.execute()

    async def mget(self, keys: list[str]) -> list[Optional[Any]]:
        try:
            client = await self._get_client()
            prefixed_keys = [self._add_prefix(k) for k in keys]
            values = await client.mget(prefixed_keys)
            return [self._deserialize(v) for v in values]
        except redis.RedisError:
            return [None] * len(keys)

    async def mset(self, mapping: dict[str, Any], ttl: Optional[int] = None) -> None:
        try:
            client = await self._get_client()
            pipeline = client.pipeline()
            actual_ttl = ttl if ttl is not None else self.default_ttl
            for key, value in mapping.items():
                prefixed_key = self._add_prefix(key)
                serialized_value = self._serialize(value)
                pipeline.set(prefixed_key, serialized_value, ex=actual_ttl)
            await pipeline.execute()
        except redis.RedisError:
            pass
