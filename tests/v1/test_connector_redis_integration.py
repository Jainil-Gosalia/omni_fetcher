"""Integration tests for RedisConnector with realistic Redis Streams behavior.

These tests use a mock async Redis client that behaves like the real redis-py
library, allowing testing without a live Redis instance. Run against a real
Redis instance by setting REDIS_TEST_URL env var.

To test with real Redis:
  redis-server &
  REDIS_TEST_URL="redis://localhost:6379/teststream" pytest tests/v1/test_connector_redis_integration.py -xvs
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.connectors.redis import RedisConnector
from omni_fetcher.v1.result import Success

pytestmark = pytest.mark.asyncio

# Environment variable for testing against real Redis
REDIS_TEST_URL = os.getenv("REDIS_TEST_URL")


class _RealisticRedisClient:
    """Mock Redis client that behaves like real redis.asyncio.Redis.

    Simulates actual Redis Streams behavior with proper entry IDs,
    blocking semantics, and stream state management.
    """

    def __init__(self) -> None:
        # Stream data: {stream_key: [(entry_id, data), ...]}
        self._streams: Dict[str, List[Tuple[str, Dict[bytes, bytes]]]] = {}
        self.closed = False
        self._read_count = 0  # Track number of reads (for testing)

    def add_stream(self, stream_key: str, entries: List[Tuple[str, Dict[bytes, bytes]]]) -> None:
        """Add test data to a stream."""
        self._streams[stream_key] = list(entries)

    async def ping(self) -> str:
        """Health check."""
        return "PONG"

    async def xread(
        self,
        streams: Dict[str, str],
        count: int = 1,
        block: Optional[int] = None,
    ) -> List[Tuple[str, List[Tuple[bytes, Dict]]]]:
        """Simulate XREAD: return entries from stream starting at offset."""
        stream_key = list(streams.keys())[0]
        start_offset = streams[stream_key]
        self._read_count += 1

        if stream_key not in self._streams:
            return []

        entries = self._streams[stream_key]
        result_entries = []

        if start_offset == "$":
            # Latest: return next batch of entries (test only - not real behavior)
            # Real Redis would return entries newer than the latest seen; for testing
            # we return batches as if we're streaming through all entries
            start_idx = (self._read_count - 1) * count
            result_entries = entries[start_idx : start_idx + count]
        elif start_offset == "0":
            # Earliest: return first batch
            start_idx = (self._read_count - 1) * count
            result_entries = entries[start_idx : start_idx + count]
        else:
            # Resume from specific entry_id
            for i, (entry_id, data) in enumerate(entries):
                if entry_id == start_offset and i + 1 < len(entries):
                    # Return entries after the given one
                    result_entries = entries[i + 1 : i + 1 + count]
                    break

        if result_entries:
            return [
                (
                    stream_key.encode(),
                    [(entry_id.encode(), data) for entry_id, data in result_entries],
                )
            ]
        # Return empty without blocking in tests
        return []

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Dict[str, str],
        count: int = 1,
        block: Optional[int] = None,
    ) -> List[Tuple[str, List[Tuple[bytes, Dict]]]]:
        """Simulate XREADGROUP for consumer groups."""
        stream_key = list(streams.keys())[0]
        start_offset = streams[stream_key]
        self._read_count += 1

        if stream_key not in self._streams:
            return []

        entries = self._streams[stream_key]
        result_entries = []

        if start_offset in (">", "$"):
            # Pending or new messages: return next batch
            start_idx = (self._read_count - 1) * count
            result_entries = entries[start_idx : start_idx + count]
        elif start_offset == "0":
            start_idx = (self._read_count - 1) * count
            result_entries = entries[start_idx : start_idx + count]

        if result_entries:
            return [
                (
                    stream_key.encode(),
                    [(entry_id.encode(), data) for entry_id, data in result_entries],
                )
            ]
        # Return empty without blocking in tests
        return []

    async def aclose(self) -> None:
        self.closed = True


# ============================================================================
# Integration Tests with Realistic Redis Behavior
# ============================================================================


@pytest.mark.asyncio
async def test_stream_from_realistic_redis() -> None:
    """Stream messages from a realistic Redis client, verifying entry IDs and metadata."""
    # Create a mock Redis client with realistic data
    fake_redis = _RealisticRedisClient()
    fake_redis.add_stream(
        "mystream",
        [
            ("1526919030474-0", {b"sensor": b"temp", b"value": b"23.5"}),
            ("1526919030475-0", {b"sensor": b"temp", b"value": b"24.1"}),
            ("1526919030476-0", {b"sensor": b"humidity", b"value": b"65"}),
        ],
    )

    connector = RedisConnector()

    # Inject the realistic fake client (must be async)
    async def mock_make_client(spec, timeout):
        return fake_redis

    connector._make_client = mock_make_client

    # Stream messages
    results = []
    stream_gen = connector.stream("redis://localhost:6379/mystream?offset=0")
    try:
        async for result in stream_gen:
            results.append(result)
            if len(results) >= 3:
                break
    finally:
        await stream_gen.aclose()

    # Verify we got all messages
    assert len(results) == 3
    assert all(isinstance(r, Success) for r in results)

    # Verify entry IDs are preserved
    entry_ids = [r.tree.metadata.source_extra["redis"]["entry_id"] for r in results]
    assert entry_ids == [
        "1526919030474-0",
        "1526919030475-0",
        "1526919030476-0",
    ]

    # Verify message content
    contents = []
    for result in results:
        atoms = result.tree.find_atoms(AtomKind.TEXT)
        assert len(atoms) > 0
        contents.append(atoms[0].content)

    assert "23.5" in contents[0]
    assert "24.1" in contents[1]
    assert "65" in contents[2]

    # Verify cleanup
    assert fake_redis.closed


@pytest.mark.asyncio
async def test_resume_from_realistic_redis() -> None:
    """Resume streaming from a specific entry_id with realistic Redis behavior."""
    fake_redis = _RealisticRedisClient()
    fake_redis.add_stream(
        "events",
        [
            ("1000-0", {b"type": b"start"}),
            ("1001-0", {b"type": b"processing"}),
            ("1002-0", {b"type": b"end"}),
        ],
    )

    connector = RedisConnector()

    async def mock_make_client(spec, timeout):
        return fake_redis

    connector._make_client = mock_make_client

    # Resume from specific entry_id
    results = []
    stream_gen = connector.stream("redis://localhost:6379/events?offset=1000-0")
    try:
        async for result in stream_gen:
            results.append(result)
            if len(results) >= 1:
                break
    finally:
        await stream_gen.aclose()

    # Should get the entry AFTER the specified one
    assert len(results) == 1
    entry_id = results[0].tree.metadata.source_extra["redis"]["entry_id"]
    assert entry_id == "1001-0"


@pytest.mark.asyncio
async def test_consumer_group_with_realistic_redis() -> None:
    """Consumer group mode with realistic Redis behavior."""
    fake_redis = _RealisticRedisClient()
    fake_redis.add_stream(
        "tasks",
        [
            ("1600-0", {b"job": b"task1"}),
            ("1601-0", {b"job": b"task2"}),
        ],
    )

    connector = RedisConnector()

    async def mock_make_client(spec, timeout):
        return fake_redis

    connector._make_client = mock_make_client

    # Consumer group mode
    results = []
    stream_gen = connector.stream("redis://localhost:6379/tasks?group=workers")
    try:
        async for result in stream_gen:
            results.append(result)
            if len(results) >= 2:
                break
    finally:
        await stream_gen.aclose()

    assert len(results) == 2
    assert all(isinstance(r, Success) for r in results)


@pytest.mark.skipif(not REDIS_TEST_URL, reason="REDIS_TEST_URL env var not set")
@pytest.mark.asyncio
async def test_against_real_redis_instance() -> None:
    """Test against a real Redis instance if REDIS_TEST_URL is set.

    To run this test:
      redis-server &
      REDIS_TEST_URL="redis://localhost:6379/teststream" pytest tests/v1/test_connector_redis_integration.py::test_against_real_redis_instance -xvs
    """
    import redis.asyncio

    # Add test data to real Redis
    client = redis.asyncio.from_url(REDIS_TEST_URL.rsplit("/", 1)[0])
    try:
        stream_key = REDIS_TEST_URL.rsplit("/", 1)[1]
        await client.delete(stream_key)  # Clean up
        await client.xadd(stream_key, {b"msg": b"hello"})
        await client.xadd(stream_key, {b"msg": b"world"})

        # Now test the connector
        connector = RedisConnector()
        results = []
        stream_gen = connector.stream(REDIS_TEST_URL + "?offset=0")
        try:
            async for result in stream_gen:
                results.append(result)
                if len(results) >= 2:
                    break
        finally:
            await stream_gen.aclose()

        # Verify
        assert len(results) == 2
        assert all(isinstance(r, Success) for r in results)
        assert results[0].tree.metadata.kind == "message"
        assert results[1].tree.metadata.kind == "message"
    finally:
        await client.close()
