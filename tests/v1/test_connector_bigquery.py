"""External-behaviour tests for the v1 ``bigquery`` connector.

The connector's routing / auth / fold / error-mapping logic is exercised through
the ``_make_executor`` seam with a fake executor (no live BigQuery). Two focused
tests drive the real ``_BigQueryExecutor`` through the ``_build_client`` seam to
prove the dry-run read-only gate: a ``SELECT`` executes and folds, while a
non-SELECT ``statement_type`` is refused before any execution.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import BearerAuth, OAuth2Auth
from omni_fetcher.v1.connectors import bigquery as bigquery_module
from omni_fetcher.v1.connectors.bigquery import (
    BigQueryConnector,
    _BigQueryExecutor,
    _NonReadOnlyStatement,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success


class _GoogleError(Exception):
    """A google-cloud exception stand-in carrying an HTTP status ``code``."""

    def __init__(self, code: int) -> None:
        super().__init__(f"google error {code}")
        self.code = code


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
    def _make(self, project, access_token, endpoint=None):
        if captured is not None:
            captured.update(project=project, access_token=access_token, endpoint=endpoint)
        return executor

    monkeypatch.setattr(BigQueryConnector, "_make_executor", _make)


_AUTH = OAuth2Auth(access_token="ya29.bq-token")
_SELECT = quote("SELECT 1")


async def test_success_yields_table(monkeypatch):
    executor = _FakeExecutor(columns=["id", "name"], rows=[[1, "alpha"]])
    _install_executor(monkeypatch, executor)

    result = await BigQueryConnector().fetch(f"bigquery://proj/ds?query={_SELECT}", auth=_AUTH)

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "query_result"
    atoms = list(node.iter_atoms())
    assert atoms[0].kind == AtomKind.TABLE
    assert atoms[0].headers == ["id", "name"]
    assert atoms[0].rows == [[1, "alpha"]]
    extra = node.metadata.source_extra["bigquery"]
    assert extra["project"] == "proj"
    assert extra["dataset"] == "ds"


async def test_table_ref_is_three_part_backtick(monkeypatch):
    executor = _FakeExecutor(columns=["x"], rows=[[1]])
    _install_executor(monkeypatch, executor)

    result = await BigQueryConnector().fetch("bigquery://proj/ds?table=events", auth=_AUTH)

    assert isinstance(result, Success)
    sql, _cap = executor.calls[0]
    assert sql == "SELECT * FROM `proj`.`ds`.`events` LIMIT 1001"


async def test_unsupported_when_extra_missing(monkeypatch):
    monkeypatch.setattr(bigquery_module, "BIGQUERY_AVAILABLE", False)

    result = await BigQueryConnector().fetch(f"bigquery://p/d?query={_SELECT}", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED


async def test_missing_auth_is_auth_failed(monkeypatch):
    def _explode(self, project, access_token):
        raise AssertionError("executor must not be built without auth")

    monkeypatch.setattr(BigQueryConnector, "_make_executor", _explode)

    result = await BigQueryConnector().fetch(f"bigquery://p/d?query={_SELECT}", auth=None)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_non_oauth_auth_is_auth_failed(monkeypatch):
    def _explode(self, project, access_token):
        raise AssertionError("executor must not be built for non-OAuth auth")

    monkeypatch.setattr(BigQueryConnector, "_make_executor", _explode)

    result = await BigQueryConnector().fetch(
        f"bigquery://p/d?query={_SELECT}", auth=BearerAuth(token="nope")
    )

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


async def test_invalid_uri_is_invalid_input(monkeypatch):
    _install_executor(monkeypatch, _FakeExecutor())

    result = await BigQueryConnector().fetch("bigquery://proj/ds", auth=_AUTH)  # no statement

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_non_select_is_permission_denied(monkeypatch):
    _install_executor(monkeypatch, _FakeExecutor(raises=_NonReadOnlyStatement("DELETE")))

    result = await BigQueryConnector().fetch(
        f"bigquery://p/d?query={quote('DELETE FROM t')}", auth=_AUTH
    )

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PERMISSION_DENIED


async def test_bad_query_is_invalid_input(monkeypatch):
    _install_executor(monkeypatch, _FakeExecutor(raises=_GoogleError(400)))

    result = await BigQueryConnector().fetch(f"bigquery://p/d?query={_SELECT}", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_missing_table_is_not_found(monkeypatch):
    _install_executor(monkeypatch, _FakeExecutor(raises=_GoogleError(404)))

    result = await BigQueryConnector().fetch(f"bigquery://p/d?query={_SELECT}", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_throttled_is_rate_limited(monkeypatch):
    _install_executor(monkeypatch, _FakeExecutor(raises=_GoogleError(429)))

    result = await BigQueryConnector().fetch(f"bigquery://p/d?query={_SELECT}", auth=_AUTH)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.RATE_LIMITED


# --- the dry-run read-only gate, driven through the _build_client seam --------


class _FakeSchemaField:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeRow:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def values(self) -> list[Any]:
        return self._values


class _FakeRowIterator:
    def __init__(self, schema: list[_FakeSchemaField], rows: list[_FakeRow]) -> None:
        self.schema = schema
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeDryJob:
    def __init__(self, statement_type: str) -> None:
        self.statement_type = statement_type


class _FakeExecJob:
    def __init__(self, rows_iter: _FakeRowIterator) -> None:
        self._rows_iter = rows_iter

    def result(self, max_results: Optional[int] = None) -> _FakeRowIterator:
        return self._rows_iter


class _FakeClient:
    def __init__(self, statement_type, schema, rows, recorder) -> None:
        self._statement_type = statement_type
        self._schema = schema
        self._rows = rows
        self._recorder = recorder

    def query(self, sql: str, job_config: Any = None) -> Any:
        self._recorder["queries"].append(sql)
        if job_config is not None and getattr(job_config, "dry_run", False):
            return _FakeDryJob(self._statement_type)
        return _FakeExecJob(_FakeRowIterator(self._schema, self._rows))


async def test_executor_dry_run_allows_select(monkeypatch):
    recorder: dict = {"queries": []}
    client = _FakeClient(
        "SELECT", [_FakeSchemaField("id")], [_FakeRow([1]), _FakeRow([2])], recorder
    )
    monkeypatch.setattr(bigquery_module, "_build_client", lambda project, token, endpoint=None: client)

    executor = _BigQueryExecutor("proj", "tok")
    columns, rows = await executor.run("SELECT id FROM `proj`.`ds`.`t`", 1000)

    assert columns == ["id"]
    assert rows == [[1], [2]]
    # A dry run (gate) plus the real execute: two queries issued.
    assert len(recorder["queries"]) == 2


async def test_executor_dry_run_refuses_non_select(monkeypatch):
    recorder: dict = {"queries": []}
    client = _FakeClient("DELETE", [], [], recorder)
    monkeypatch.setattr(bigquery_module, "_build_client", lambda project, token, endpoint=None: client)

    executor = _BigQueryExecutor("proj", "tok")
    with pytest.raises(_NonReadOnlyStatement):
        await executor.run("DELETE FROM `proj`.`ds`.`t`", 1000)

    # Only the dry run ran; the write was never executed.
    assert len(recorder["queries"]) == 1


def test_can_handle():
    assert BigQueryConnector.can_handle("bigquery://proj/ds")
    assert not BigQueryConnector.can_handle("postgres://h/db")
