"""Tests for Redis cache backend."""

import pytest
from unittest.mock import AsyncMock, patch

from omni_fetcher.cache.redis import RedisCacheBackend


class TestRedisCacheCreation:
    """Tests for RedisCacheBackend creation."""

    def test_default_creation(self):
        with patch("omni_fetcher.cache.redis.redis.Redis"):
            cache = RedisCacheBackend()
            assert cache.host == "localhost"
            assert cache.port == 6379
            assert cache.db == 0
            assert cache.prefix == "omni_fetcher:"
            assert cache.default_ttl == 3600
            assert cache.decode_responses is True

    def test_custom_params(self):
        with patch("omni_fetcher.cache.redis.redis.Redis"):
            cache = RedisCacheBackend(
                host="redis.example.com",
                port=6380,
                db=2,
                password="secret",
                default_ttl=7200,
                decode_responses=False,
            )
            assert cache.host == "redis.example.com"
            assert cache.port == 6380
            assert cache.db == 2
            assert cache.password == "secret"
            assert cache.default_ttl == 7200
            assert cache.decode_responses is False

    def test_with_prefix(self):
        with patch("omni_fetcher.cache.redis.redis.Redis"):
            cache = RedisCacheBackend(prefix="custom_prefix:")
            assert cache.prefix == "custom_prefix:"


class TestRedisCacheOperations:
    """Tests for Redis cache operations."""

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        with patch("omni_fetcher.cache.redis.redis.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_redis_class.return_value = mock_client

            cache = RedisCacheBackend()
            await cache._get_client()

            await cache.set("test_key", {"data": "value"})
            mock_client.set.assert_called_once()

            mock_client.get.return_value = '{"data": "value"}'
            result = await cache.get("test_key")
            assert result == {"data": "value"}

    @pytest.mark.asyncio
    async def test_get_not_found(self):
        with patch("omni_fetcher.cache.redis.redis.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_redis_class.return_value = mock_client

            cache = RedisCacheBackend()
            await cache._get_client()

            mock_client.get.return_value = None
            result = await cache.get("nonexistent_key")
            assert result is None

    @pytest.mark.asyncio
    async def test_delete(self):
        with patch("omni_fetcher.cache.redis.redis.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_redis_class.return_value = mock_client

            cache = RedisCacheBackend()
            await cache._get_client()

            await cache.delete("test_key")
            mock_client.delete.assert_called_once_with("omni_fetcher:test_key")

    @pytest.mark.asyncio
    async def test_clear(self):
        with patch("omni_fetcher.cache.redis.redis.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_redis_class.return_value = mock_client

            cache = RedisCacheBackend()
            await cache._get_client()

            mock_client.scan.return_value = (0, ["omni_fetcher:key1", "omni_fetcher:key2"])

            await cache.clear()
            mock_client.delete.assert_called_once_with("omni_fetcher:key1", "omni_fetcher:key2")

    @pytest.mark.asyncio
    async def test_set_with_ttl(self):
        with patch("omni_fetcher.cache.redis.redis.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_redis_class.return_value = mock_client

            cache = RedisCacheBackend(default_ttl=3600)
            await cache._get_client()

            await cache.set("test_key", "value", ttl=1800)

            call_args = mock_client.set.call_args
            assert call_args[1]["ex"] == 1800


class TestRedisCacheStats:
    """Tests for Redis cache stats."""

    @pytest.mark.asyncio
    async def test_stats(self):
        with patch("omni_fetcher.cache.redis.redis.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_redis_class.return_value = mock_client

            mock_client.info.return_value = {"used_memory_human": "1.5MB"}
            mock_client.scan.return_value = (
                0,
                ["omni_fetcher:key1", "omni_fetcher:key2", "omni_fetcher:key3"],
            )

            cache = RedisCacheBackend(host="redis.example.com", port=6380, db=1)
            result = await cache.stats()

            assert result["keys"] == 3
            assert result["used_memory"] == "1.5MB"
            assert result["connected"] is True
            assert result["host"] == "redis.example.com"
            assert result["port"] == 6380
            assert result["db"] == 1


class TestRedisCacheErrorHandling:
    """Tests for Redis cache error handling."""

    @pytest.mark.asyncio
    async def test_connection_error_get(self):
        import redis.asyncio as redis

        with patch("omni_fetcher.cache.redis.redis.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_client.get.side_effect = redis.RedisError("Connection refused")
            mock_redis_class.return_value = mock_client

            cache = RedisCacheBackend()
            result = await cache.get("test_key")
            assert result is None

    @pytest.mark.asyncio
    async def test_connection_error_set(self):
        import redis.asyncio as redis

        with patch("omni_fetcher.cache.redis.redis.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_client.set.side_effect = redis.RedisError("Connection refused")
            mock_redis_class.return_value = mock_client

            cache = RedisCacheBackend()
            await cache.set("test_key", "value")
            mock_client.set.assert_called_once()
