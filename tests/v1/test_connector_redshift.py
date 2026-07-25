"""External-behaviour tests for the v1 ``redshift`` connector.

The connector's routing / credential / fold / error-mapping logic is exercised
through the ``_make_executor`` seam with a fake executor (no live Redshift). One
focused test drives the real ``_RedshiftExecutor`` through the ``_connect`` seam
with a fake DBAPI connection, to prove ``SET TRANSACTION READ ONLY`` is issued
as the first statement of the transaction (the engine-level read-only guarantee)
and that the transaction is rolled back and closed.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import BasicAuth
from omni_fetcher.v1.connectors import redshift as redshift_module
from omni_fetcher.v1.connectors._sql_query import SqlQuerySpec
from omni_fetcher.v1.connectors.redshift import RedshiftQueryConnector, _RedshiftExecutor
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success


class _RsError(Exception):
    """A redshift_connector error stand-in carrying a SQLSTATE."""

    def __init__(self, sqlstate: Optional[str] = None, message: str = "") -> None:
        super().__init__(message or sqlstate or "error")
        self.sqlstate = sqlstate


class _FakeExecutor:
    def __init__(
        self,
        *,
        columns: Optional[list[str]] = None,
        rows: Optional[list[list[Any]]] = None,
        raises: Optional[BaseException] = None,
    ) -> None:
        self._columns = columns or []
        self._rows = rows or []
        self._raises = raises
        self.calls: list[tuple[str, int]] = []

    async def run(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        self.calls.append((sql, row_cap))
        if self._raises is not None:
            raise self._raises
        return self._columns, self._rows


def _install_executor(monkeypatch, executor: _FakeExecutor, captured: Optional[dict] = None):
    def _make(self, spec, user, password):
        if captured is not None:
            captured.update(spec=spec, user=user, password=password)
        return executor

    monkeypatch.setattr(RedshiftQueryConnector, "_make_executor", _make)


_SELECT = quote("SELECT 1")


async def test_success_yields_table(monkeypatch):
    executor = _FakeExecutor(columns=["id", "name"], rows=[[1, "alpha"], [2, "beta"]])
    _install_executor(monkeypatch, executor)

    result = await RedshiftQueryConnector().fetch(f"redshift://h:5439/db?query={_SELECT}")

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "query_result"
    atoms = list(node.iter_atoms())
    assert atoms[0].kind == AtomKind.TABLE
    assert atoms[0].headers == ["id", "name"]
    assert atoms[0].rows == [[1, "alpha"], [2, "beta"]]
    assert node.metadata.source_extra["redshift"]["host"] == "h"


async def test_unsupported_when_extra_missing(monkeypatch):
    monkeypatch.setattr(redshift_module, "REDSHIFT_AVAILABLE", False)

    result = await RedshiftQueryConnector().fetch(f"redshift://h/db?query={_SELECT}")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED


async def test_invalid_uri_is_invalid_input(monkeypatch):
    _install_executor(monkeypatch, _FakeExecutor())

    result = await RedshiftQueryConnector().fetch("redshift://h/db")  # no statement

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_basic_auth_overrides_uri_credentials(monkeypatch):
    captured: dict = {}
    _install_executor(monkeypatch, _FakeExecutor(columns=["x"], rows=[[1]]), captured)

    result = await RedshiftQueryConnector().fetch(
        f"redshift://h/db?query={_SELECT}&user=uri_user&password=uri_pw",
        auth=BasicAuth(username="auth_user", password="auth_pw"),
    )

    assert isinstance(result, Success)
    assert captured["user"] == "auth_user"
    assert captured["password"] == "auth_pw"


async def test_uri_credentials_are_the_fallback(monkeypatch):
    captured: dict = {}
    _install_executor(monkeypatch, _FakeExecutor(columns=["x"], rows=[[1]]), captured)

    result = await RedshiftQueryConnector().fetch(
        f"redshift://h/db?query={_SELECT}&user=uri_user&password=uri_pw"
    )

    assert isinstance(result, Success)
    assert captured["user"] == "uri_user"
    assert captured["password"] == "uri_pw"


async def test_read_only_violation_is_permission_denied(monkeypatch):
    _install_executor(monkeypatch, _FakeExecutor(raises=_RsError(sqlstate="25006")))

    result = await RedshiftQueryConnector().fetch(f"redshift://h/db?query={quote('DELETE FROM t')}")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PERMISSION_DENIED


async def test_missing_table_is_not_found(monkeypatch):
    _install_executor(monkeypatch, _FakeExecutor(raises=_RsError(sqlstate="42P01")))

    result = await RedshiftQueryConnector().fetch(f"redshift://h/db?query={_SELECT}")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_bad_password_is_auth_failed(monkeypatch):
    _install_executor(monkeypatch, _FakeExecutor(raises=_RsError(sqlstate="28P01")))

    result = await RedshiftQueryConnector().fetch(f"redshift://h/db?query={_SELECT}")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_transport_error_is_transient(monkeypatch):
    _install_executor(monkeypatch, _FakeExecutor(raises=OSError("connection reset")))

    result = await RedshiftQueryConnector().fetch(f"redshift://h/db?query={_SELECT}")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.TRANSIENT


# --- the read-only mechanism, driven through the _connect seam ---------------


class _FakeCursor:
    def __init__(self, recorder: dict, description, rows) -> None:
        self._recorder = recorder
        self.description = description
        self._rows = rows

    def execute(self, sql: str) -> None:
        self._recorder["executed"].append(sql)

    def fetchmany(self, n: int) -> list:
        return self._rows[:n]


class _FakeConnection:
    def __init__(self, recorder: dict, cursor: _FakeCursor) -> None:
        self._recorder = recorder
        self._cursor = cursor
        self.autocommit = True

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def rollback(self) -> None:
        self._recorder["rollback"] = True

    def close(self) -> None:
        self._recorder["closed"] = True


async def test_executor_issues_read_only_before_query(monkeypatch):
    recorder: dict = {"executed": [], "rollback": False, "closed": False}
    cursor = _FakeCursor(recorder, description=[("id",), ("name",)], rows=[[1, "a"], [2, "b"]])
    connection = _FakeConnection(recorder, cursor)
    monkeypatch.setattr(redshift_module, "_connect", lambda spec, user, password: connection)

    spec = SqlQuerySpec(
        host="h",
        port=5439,
        database="db",
        table=None,
        query="SELECT * FROM t",
        query_env=None,
        limit=None,
        user=None,
        password=None,
    )
    executor = _RedshiftExecutor(spec, "u", "p")
    columns, rows = await executor.run("SELECT * FROM t", 1000)

    assert recorder["executed"] == ["SET TRANSACTION READ ONLY", "SELECT * FROM t"]
    assert columns == ["id", "name"]
    assert rows == [[1, "a"], [2, "b"]]
    assert connection.autocommit is False
    assert recorder["rollback"] is True
    assert recorder["closed"] is True


def test_can_handle():
    assert RedshiftQueryConnector.can_handle("redshift://h/db")
    assert not RedshiftQueryConnector.can_handle("postgres://h/db")
