"""External-behaviour tests for the v1 MySQL / MariaDB query connector.

No live server: the DB executor is replaced through the ``_make_executor`` seam,
and ``AIOMYSQL_AVAILABLE`` is forced on so the tests run without the ``mysql``
extra (as the Postgres tests do without asyncpg). Mirrors
``test_connector_postgres_query`` -- the two connectors differ only in driver,
read-only mechanism, auth wiring, and error mapping, so their tests should look
alike.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from omni_fetcher.v1.atoms import Table
from omni_fetcher.v1.auth import BasicAuth
from omni_fetcher.v1.connectors import mysql as my_module
from omni_fetcher.v1.connectors.mysql import MySQLQueryConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _aiomysql_available(monkeypatch):
    """Force the extra 'installed' so the seam runs without aiomysql."""
    monkeypatch.setattr(my_module, "AIOMYSQL_AVAILABLE", True)


class _FakeMySQLError(Exception):
    """A pymysql-shaped error: ``args[0]`` is the numeric code."""

    def __init__(self, code: int) -> None:
        super().__init__(code, "mysql error")


class _FakeExecutor:
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
    connector = MySQLQueryConnector()

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
    result = await _connector(["id", "name"], [[1, "a"], [2, "b"]]).fetch(
        "mysql://db/app?query=SELECT%20id,name%20FROM%20users"
    )
    assert isinstance(result, Success)
    assert result.tree.metadata.kind == "query_result"
    assert _table(result).headers == ["id", "name"]
    assert _table(result).rows == [[1, "a"], [2, "b"]]


async def test_mariadb_alias_routes_and_builds_backtick_quoted_select_star() -> None:
    await _connector(["id"], [[1]]).fetch("mariadb://h/db?table=app.users&limit=5")
    # MySQL/MariaDB quote identifiers with backticks, not double quotes.
    assert _FakeExecutor.last["sql"] == "SELECT * FROM `app`.`users` LIMIT 6"
    assert _FakeExecutor.last["row_cap"] == 5


async def test_basic_auth_overrides_uri_credentials() -> None:
    sink: dict[str, Any] = {}
    await _connector(["x"], [[1]], creds_sink=sink).fetch(
        "mysql://h/db?query=SELECT%201&user=uriuser&password=uripw",
        auth=BasicAuth(username="authuser", password="authpw"),
    )
    assert sink == {"user": "authuser", "password": "authpw"}


async def test_uri_credentials_used_when_no_auth() -> None:
    sink: dict[str, Any] = {}
    await _connector(["x"], [[1]], creds_sink=sink).fetch(
        "mysql://h/db?query=SELECT%201&user=bob&password=pw"
    )
    assert sink == {"user": "bob", "password": "pw"}


async def test_row_cap_truncates_to_partial() -> None:
    result = await _connector(["id"], [[i] for i in range(3)]).fetch(
        "mysql://h/db?query=SELECT%20id%20FROM%20t&limit=2"
    )
    assert isinstance(result, Partial)
    assert len(_table(result).rows) == 2
    assert result.gaps[0].kind is ErrorKind.UNSUPPORTED


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (1045, ErrorKind.AUTH_FAILED),  # access denied
        (1044, ErrorKind.PERMISSION_DENIED),  # db access denied
        (1792, ErrorKind.PERMISSION_DENIED),  # read-only transaction write
        (1146, ErrorKind.NOT_FOUND),  # no such table
        (1049, ErrorKind.NOT_FOUND),  # unknown database
        (1064, ErrorKind.INVALID_INPUT),  # syntax error
        (1054, ErrorKind.INVALID_INPUT),  # unknown column
        (2006, ErrorKind.TRANSIENT),  # server gone
    ],
)
async def test_error_code_maps_to_error_kind(code: int, kind: ErrorKind) -> None:
    result = await _connector(raise_exc=_FakeMySQLError(code)).fetch(
        "mysql://h/db?query=SELECT%201"
    )
    assert isinstance(result, Error) and result.kind is kind


async def test_connection_oserror_is_transient() -> None:
    result = await _connector(raise_exc=OSError("refused")).fetch("mysql://h/db?query=SELECT%201")
    assert isinstance(result, Error) and result.kind is ErrorKind.TRANSIENT


async def test_both_query_and_table_is_invalid_input() -> None:
    result = await _connector(["x"], [[1]]).fetch("mysql://h/db?query=SELECT%201&table=t")
    assert isinstance(result, Error) and result.kind is ErrorKind.INVALID_INPUT


async def test_missing_extra_yields_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(my_module, "AIOMYSQL_AVAILABLE", False)
    result = await MySQLQueryConnector().fetch("mysql://h/db?query=SELECT%201")
    assert isinstance(result, Error) and result.kind is ErrorKind.UNSUPPORTED
    assert "mysql" in result.message


async def test_can_handle_mysql_and_mariadb_schemes() -> None:
    assert MySQLQueryConnector.can_handle("mysql://h/db")
    assert MySQLQueryConnector.can_handle("mariadb://h/db")
    assert not MySQLQueryConnector.can_handle("postgres://h/db")
