"""External-behaviour tests for the v1 PostgreSQL query connector.

No live PostgreSQL: the DB executor is replaced through the ``_make_executor``
seam, and ``ASYNCPG_AVAILABLE`` is forced on so the tests run without the
``postgres`` extra installed (as the CDC tests do). The one test that leaves the
flag off pins the extra-gating behaviour.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from omni_fetcher.v1.atoms import Table
from omni_fetcher.v1.auth import BasicAuth
from omni_fetcher.v1.connectors import postgres_query as pg_module
from omni_fetcher.v1.connectors.postgres_query import PostgresQueryConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _asyncpg_available(monkeypatch):
    """Force the extra 'installed' so the seam runs without asyncpg."""
    monkeypatch.setattr(pg_module, "ASYNCPG_AVAILABLE", True)


class _FakePGError(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate


class _FakeExecutor:
    """Records the SQL/cap it received; returns scripted columns/rows or raises."""

    last: dict[str, Any] = {}

    def __init__(self, columns, rows, raise_exc: Optional[Exception] = None) -> None:
        self._columns = columns
        self._rows = rows
        self._raise = raise_exc

    async def run(self, sql: str, row_cap: int):
        type(self).last = {"sql": sql, "row_cap": row_cap}
        if self._raise is not None:
            raise self._raise
        return self._columns, self._rows


def _connector(columns=None, rows=None, raise_exc=None, creds_sink=None):
    connector = PostgresQueryConnector()

    def _make(spec, user, password):
        if creds_sink is not None:
            creds_sink.update(user=user, password=password)
        return _FakeExecutor(columns or [], rows or [], raise_exc)

    connector._make_executor = _make  # type: ignore[method-assign]
    return connector


def _table(result: object) -> Table:
    atoms = list(result.tree.iter_atoms())  # type: ignore[attr-defined]
    assert len(atoms) == 1 and isinstance(atoms[0], Table)
    return atoms[0]


async def test_query_returns_a_table() -> None:
    connector = _connector(["id", "name"], [[1, "a"], [2, "b"]])
    result = await connector.fetch("postgres://db/app?query=SELECT%20id,name%20FROM%20users")
    assert isinstance(result, Success)
    assert result.tree.metadata.kind == "query_result"
    table = _table(result)
    assert table.headers == ["id", "name"]
    assert table.rows == [[1, "a"], [2, "b"]]


async def test_table_browse_builds_quoted_select_star() -> None:
    connector = _connector(["id"], [[1]])
    await connector.fetch("postgres://h/db?table=public.users&limit=5")
    assert _FakeExecutor.last["sql"] == 'SELECT * FROM "public"."users" LIMIT 6'
    assert _FakeExecutor.last["row_cap"] == 5


async def test_basic_auth_overrides_uri_credentials() -> None:
    sink: dict[str, Any] = {}
    connector = _connector(["x"], [[1]], creds_sink=sink)
    await connector.fetch(
        "postgres://h/db?query=SELECT%201&user=uriuser&password=uripw",
        auth=BasicAuth(username="authuser", password="authpw"),
    )
    assert sink == {"user": "authuser", "password": "authpw"}


async def test_uri_credentials_used_when_no_auth() -> None:
    sink: dict[str, Any] = {}
    connector = _connector(["x"], [[1]], creds_sink=sink)
    await connector.fetch("postgres://h/db?query=SELECT%201&user=bob&password=pw")
    assert sink == {"user": "bob", "password": "pw"}


async def test_row_cap_truncates_to_partial() -> None:
    # Executor returns cap+1 rows -> the fold truncates and gaps.
    connector = _connector(["id"], [[i] for i in range(3)])
    result = await connector.fetch("postgres://h/db?query=SELECT%20id%20FROM%20t&limit=2")
    assert isinstance(result, Partial)
    assert len(_table(result).rows) == 2
    assert result.gaps[0].kind is ErrorKind.UNSUPPORTED


@pytest.mark.parametrize(
    ("sqlstate", "kind"),
    [
        ("28P01", ErrorKind.AUTH_FAILED),
        ("42501", ErrorKind.PERMISSION_DENIED),
        ("25006", ErrorKind.PERMISSION_DENIED),  # read-only transaction write refusal
        ("42P01", ErrorKind.NOT_FOUND),
        ("3D000", ErrorKind.NOT_FOUND),
        ("42601", ErrorKind.INVALID_INPUT),
        ("08006", ErrorKind.TRANSIENT),  # connection failure class
    ],
)
async def test_sqlstate_maps_to_error_kind(sqlstate: str, kind: ErrorKind) -> None:
    connector = _connector(raise_exc=_FakePGError(sqlstate))
    result = await connector.fetch("postgres://h/db?query=SELECT%201")
    assert isinstance(result, Error) and result.kind is kind


async def test_connection_oserror_is_transient() -> None:
    connector = _connector(raise_exc=OSError("connection refused"))
    result = await connector.fetch("postgres://h/db?query=SELECT%201")
    assert isinstance(result, Error) and result.kind is ErrorKind.TRANSIENT


async def test_both_query_and_table_is_invalid_input() -> None:
    connector = _connector(["x"], [[1]])
    result = await connector.fetch("postgres://h/db?query=SELECT%201&table=t")
    assert isinstance(result, Error) and result.kind is ErrorKind.INVALID_INPUT


async def test_missing_extra_yields_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(pg_module, "ASYNCPG_AVAILABLE", False)
    result = await PostgresQueryConnector().fetch("postgres://h/db?query=SELECT%201")
    assert isinstance(result, Error) and result.kind is ErrorKind.UNSUPPORTED
    assert "postgres" in result.message


async def test_can_handle_only_plain_postgres_scheme() -> None:
    assert PostgresQueryConnector.can_handle("postgres://h/db")
    # postgres-cdc:// is a different connector; this one still string-matches the
    # prefix, but the registry routes cdc first by its own pattern.
    assert not PostgresQueryConnector.can_handle("mysql://h/db")
