"""Live-instance integration tests for the v1 ``postgres-cdc`` connector.

The unit suite (test_connector_postgres_cdc.py) scripts a fake replication
stream and never touches PostgreSQL; these tests exercise the paths the
fakes cannot -- the asyncpg adapter, publication/slot DDL, and the pgoutput
binary decoder -- against a real server. They are skipped unless
``POSTGRES_CDC_TEST_URI`` names a reachable PostgreSQL with
``wal_level=logical`` (a superuser or replication-role login):

  docker run -d --name omni-pg -e POSTGRES_PASSWORD=secret -p 55432:5432 \\
      postgres:16 -c wal_level=logical
  POSTGRES_CDC_TEST_URI="postgres-cdc://localhost:55432/postgres?user=postgres&password=secret" \\
      pytest tests/v1/test_connector_postgres_cdc_integration.py -xvs

Each test creates and drops its own table and slot; the database is assumed
disposable (tables named ``omni_cdc_it`` are dropped on entry).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import List
from urllib.parse import parse_qs, urlsplit

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.connectors.postgres_cdc import PostgresCDCConnector
from omni_fetcher.v1.result import Result, Success
from omni_fetcher.v1.retry import RetryPolicy, stream_with_restart

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("POSTGRES_CDC_TEST_URI"),
        reason="POSTGRES_CDC_TEST_URI env var not set",
    ),
]

TEST_URI = os.getenv("POSTGRES_CDC_TEST_URI", "")
TABLE = "omni_cdc_it"


def _with_slot(slot: str) -> str:
    separator = "&" if "?" in TEST_URI else "?"
    return f"{TEST_URI}{separator}slot={slot}"


async def _admin_connection():
    """A plain asyncpg connection to the database the test URI names."""
    import asyncpg

    parts = urlsplit(TEST_URI.replace("postgres-cdc://", "postgres://", 1))
    params = {key: values[-1] for key, values in parse_qs(parts.query).items()}
    connection = await asyncpg.connect(
        host=parts.hostname,
        port=parts.port or 5432,
        database=parts.path.lstrip("/"),
        user=params.get("user"),
        password=params.get("password"),
    )
    await connection.execute(f"DROP TABLE IF EXISTS {TABLE}")
    await connection.execute(f"CREATE TABLE {TABLE} (id int PRIMARY KEY, name text)")
    return connection


async def _slot_exists(admin, name: str) -> bool:
    row = await admin.fetchrow("SELECT 1 FROM pg_replication_slots WHERE slot_name=$1", name)
    return row is not None


async def _wait_for_slot(admin, name: str, present: bool = True) -> bool:
    for _ in range(100):
        if await _slot_exists(admin, name) == present:
            return True
        await asyncio.sleep(0.1)
    return False


def _record(item: Result) -> dict:
    assert isinstance(item, Success)
    return json.loads(item.tree.find_atoms(AtomKind.TEXT)[0].content)


async def test_live_changes_decode_and_slot_drops_on_abandonment() -> None:
    """Real INSERT/UPDATE/DELETE decode from pgoutput; abandonment drops the slot."""
    admin = await _admin_connection()
    try:
        connector = PostgresCDCConnector(poll_interval=0.2)
        stream = connector.stream(_with_slot("omni_it_basic"))
        items: List[Result] = []

        async def collect() -> None:
            async for item in stream:
                items.append(item)
                if len(items) >= 3:
                    break

        task = asyncio.create_task(collect())
        assert await _wait_for_slot(admin, "omni_it_basic"), "slot never appeared"

        await admin.execute(f"INSERT INTO {TABLE} VALUES (1, 'ada')")
        await admin.execute(f"UPDATE {TABLE} SET name = 'ada lovelace' WHERE id = 1")
        await admin.execute(f"DELETE FROM {TABLE} WHERE id = 1")

        await asyncio.wait_for(task, 30)
        await stream.aclose()  # type: ignore[attr-defined]

        records = [_record(item) for item in items]
        assert [r["op"] for r in records] == ["INSERT", "UPDATE", "DELETE"]
        assert records[0]["table"] == f"public.{TABLE}"
        assert records[0]["new"] == {"id": "1", "name": "ada"} and records[0]["old"] is None
        assert records[1]["new"] == {"id": "1", "name": "ada lovelace"}
        assert records[2]["new"] is None and records[2]["old"]["id"] == "1"
        assert all(re.fullmatch(r"[0-9A-F]+/[0-9A-F]+", r["lsn"]) for r in records)
        assert all(isinstance(r["xid"], int) for r in records)

        extra = items[0].tree.metadata.source_extra["postgres"]  # type: ignore[union-attr]
        assert {"table", "operation", "lsn", "timestamp", "xid", "slot"} <= set(extra)
        assert extra["slot"] == "omni_it_basic"

        assert await _wait_for_slot(admin, "omni_it_basic", present=False), "slot not dropped"
    finally:
        await admin.close()


async def test_live_preexisting_slot_is_reused_and_retained_changes_resume() -> None:
    """A surviving slot's retained changes are delivered on reattach.

    This is the server-side half of the restart contract (D7): after a
    transport failure the connector keeps the slot, and reattaching to it
    (as ``stream_with_restart``'s derived ``?slot=`` does) delivers every
    change made while nobody was streaming -- no duplicates, no gaps. The
    slot is created up front by the test, standing in for the one a failed
    stream left behind.
    """
    admin = await _admin_connection()
    try:
        connector = PostgresCDCConnector(poll_interval=0.2)
        await admin.execute(
            "SELECT pg_create_logical_replication_slot('omni_it_resume', 'pgoutput')"
        )
        try:
            # Changes made while nobody is streaming: the slot retains them.
            await admin.execute(f"INSERT INTO {TABLE} VALUES (2, 'grace')")
            await admin.execute(f"INSERT INTO {TABLE} VALUES (3, 'edsger')")

            policy = RetryPolicy(max_attempts=2, initial_delay=0.0)
            stream = stream_with_restart(connector, _with_slot("omni_it_resume"), policy=policy)
            items: List[Result] = []

            async def collect() -> None:
                async for item in stream:
                    items.append(item)
                    if len(items) >= 2:
                        break

            await asyncio.wait_for(collect(), 30)
            await stream.aclose()  # type: ignore[attr-defined]

            ids = [_record(item)["new"]["id"] for item in items]
            assert ids == ["2", "3"]  # retained changes, in order, exactly once
        finally:
            if await _slot_exists(admin, "omni_it_resume"):
                await admin.execute("SELECT pg_drop_replication_slot('omni_it_resume')")
    finally:
        await admin.close()
