"""External-behaviour tests for the v1 ``duckdb`` connector.

Unlike the warehouse connectors, DuckDB is embedded, so these tests run against
a **real** DuckDB file (created per test): a table browse and a raw query return
a canonical ``query_result`` node, the ``read_only=True`` open genuinely refuses
a write (``PERMISSION_DENIED``) and leaves the file unchanged, a missing file is
``NOT_FOUND``, and a missing extra is a typed ``UNSUPPORTED``.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.connectors import duckdb_query as duckdb_module
from omni_fetcher.v1.connectors.duckdb_query import DuckDBQueryConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

# These tests run against a real DuckDB file, so the extra must be installed;
# skip cleanly (rather than error at collection) on a base install.
duckdb = pytest.importorskip("duckdb")


def _make_db(tmp_path) -> str:
    """Create a DuckDB file with a small ``items`` table; return its path."""
    path = tmp_path / "test.duckdb"
    connection = duckdb.connect(str(path))
    try:
        connection.execute("CREATE TABLE items (id INTEGER, name VARCHAR)")
        connection.execute("INSERT INTO items VALUES (1, 'alpha'), (2, 'beta')")
    finally:
        connection.close()
    return str(path).replace("\\", "/")


async def test_table_browse_is_success(tmp_path):
    path = _make_db(tmp_path)

    result = await DuckDBQueryConnector().fetch(f"duckdb://{path}?table=items")

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "query_result"
    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind == AtomKind.TABLE
    assert atoms[0].headers == ["id", "name"]
    assert atoms[0].rows == [[1, "alpha"], [2, "beta"]]
    assert node.metadata.source_extra["duckdb"]["row_count"] == 2


async def test_raw_query_is_success(tmp_path):
    path = _make_db(tmp_path)
    sql = quote("SELECT name FROM items WHERE id = 2")

    result = await DuckDBQueryConnector().fetch(f"duckdb://{path}?query={sql}")

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert atoms[0].headers == ["name"]
    assert atoms[0].rows == [["beta"]]


async def test_write_is_refused_and_file_unchanged(tmp_path):
    path = _make_db(tmp_path)
    sql = quote("DELETE FROM items")

    result = await DuckDBQueryConnector().fetch(f"duckdb://{path}?query={sql}")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PERMISSION_DENIED

    # The file is byte-for-byte intact: both rows still there.
    connection = duckdb.connect(path, read_only=True)
    try:
        count = connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    finally:
        connection.close()
    assert count == 2


async def test_missing_file_is_not_found(tmp_path):
    missing = str(tmp_path / "nope.duckdb").replace("\\", "/")

    result = await DuckDBQueryConnector().fetch(f"duckdb://{missing}?table=items")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_bad_sql_is_invalid_input(tmp_path):
    path = _make_db(tmp_path)
    sql = quote("SELECT * FROM no_such_table")

    result = await DuckDBQueryConnector().fetch(f"duckdb://{path}?query={sql}")

    assert isinstance(result, Error)
    assert result.kind in (ErrorKind.NOT_FOUND, ErrorKind.INVALID_INPUT)


async def test_no_statement_is_invalid_input(tmp_path):
    path = _make_db(tmp_path)

    result = await DuckDBQueryConnector().fetch(f"duckdb://{path}")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_unsupported_when_extra_missing(monkeypatch, tmp_path):
    path = _make_db(tmp_path)
    monkeypatch.setattr(duckdb_module, "DUCKDB_AVAILABLE", False)

    result = await DuckDBQueryConnector().fetch(f"duckdb://{path}?table=items")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED


def test_can_handle():
    assert DuckDBQueryConnector.can_handle("duckdb:///data.duckdb")
    assert not DuckDBQueryConnector.can_handle("sqlite:///data.db")
    assert not DuckDBQueryConnector.can_handle("postgres://h/db")
