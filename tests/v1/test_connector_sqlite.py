"""External-behaviour tests for the v1 SQLite query connector.

SQLite needs no server and no extra, so these run against *real* temporary
``.db`` files (no seam). They assert the canonical contract and, above all, that
read-only is enforced by the engine: a write is refused and the file on disk is
byte-unchanged.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from omni_fetcher.v1.atoms import Table
from omni_fetcher.v1.connectors.sqlite import SOURCE_NAMESPACE, SQLiteQueryConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, ResultAdapter, Success

pytestmark = pytest.mark.asyncio


def _make_db(tmp_path: Path, rows: int = 3) -> Path:
    """Create a temp SQLite db with a ``users(id, name, blob)`` table."""
    path = tmp_path / "app.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE users(id INTEGER, name TEXT, data BLOB)")
    con.executemany(
        "INSERT INTO users VALUES(?,?,?)",
        [(i, f"user{i}", bytes([i, 255])) for i in range(rows)],
    )
    con.commit()
    con.close()
    return path


def _uri(path: Path, query: str) -> str:
    return "sqlite:///" + str(path).replace("\\", "/") + query


def _table(result: object) -> Table:
    atoms = list(result.tree.iter_atoms())  # type: ignore[attr-defined]
    assert len(atoms) == 1 and isinstance(atoms[0], Table)
    return atoms[0]


async def test_query_returns_a_table(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    result = await SQLiteQueryConnector().fetch(
        _uri(db, "?query=SELECT%20id,name%20FROM%20users%20ORDER%20BY%20id")
    )
    assert isinstance(result, Success)
    assert result.tree.metadata.kind == "query_result"
    table = _table(result)
    assert table.headers == ["id", "name"]
    assert table.rows == [[0, "user0"], [1, "user1"], [2, "user2"]]


async def test_table_browse_needs_no_sql(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    result = await SQLiteQueryConnector().fetch(_uri(db, "?table=users"))
    assert isinstance(result, Success)
    table = _table(result)
    assert table.headers == ["id", "name", "data"]
    extra = result.tree.metadata.source_extra[SOURCE_NAMESPACE]
    assert extra["columns"] == ["id", "name", "data"] and extra["row_count"] == 3


async def test_blob_coerces_to_hex_and_round_trips(tmp_path: Path) -> None:
    db = _make_db(tmp_path, rows=1)
    result = await SQLiteQueryConnector().fetch(_uri(db, "?table=users"))
    assert isinstance(result, Success)
    assert _table(result).rows[0][2] == bytes([0, 255]).hex()  # "00ff"
    # The whole result must survive a JSON round-trip through the contract.
    import json

    rebuilt = ResultAdapter.validate_python(json.loads(result.model_dump_json()))
    assert rebuilt.state.value == "success"


async def test_read_only_is_enforced_and_file_is_untouched(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    before = hashlib.md5(db.read_bytes()).hexdigest()

    result = await SQLiteQueryConnector().fetch(
        _uri(db, "?query=INSERT%20INTO%20users%20VALUES(9,'x',null)")
    )

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.PERMISSION_DENIED
    assert hashlib.md5(db.read_bytes()).hexdigest() == before  # byte-for-byte identical


async def test_row_cap_truncates_to_a_partial_with_a_gap(tmp_path: Path) -> None:
    db = _make_db(tmp_path, rows=5)
    result = await SQLiteQueryConnector().fetch(_uri(db, "?table=users&limit=2"))
    assert isinstance(result, Partial)
    assert len(_table(result).rows) == 2
    assert result.gaps and result.gaps[0].kind is ErrorKind.UNSUPPORTED
    assert result.tree.metadata.source_extra[SOURCE_NAMESPACE]["truncated"] is True


async def test_empty_result_is_success_with_no_rows(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    result = await SQLiteQueryConnector().fetch(
        _uri(db, "?query=SELECT%20id%20FROM%20users%20WHERE%20id%20%3D%20999")
    )
    assert isinstance(result, Success)
    assert _table(result).rows == []


async def test_missing_database_is_not_found(tmp_path: Path) -> None:
    result = await SQLiteQueryConnector().fetch(
        "sqlite:///" + str(tmp_path / "nope.db").replace("\\", "/") + "?table=users"
    )
    assert isinstance(result, Error) and result.kind is ErrorKind.NOT_FOUND


async def test_unreadable_database_is_permission_denied(tmp_path: Path, monkeypatch) -> None:
    db = _make_db(tmp_path)
    monkeypatch.setattr("os.access", lambda *a, **k: False)
    result = await SQLiteQueryConnector().fetch(_uri(db, "?table=users"))
    assert isinstance(result, Error) and result.kind is ErrorKind.PERMISSION_DENIED


async def test_bad_sql_is_invalid_input(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    result = await SQLiteQueryConnector().fetch(_uri(db, "?query=SELECT%20nope%20FRM"))
    assert isinstance(result, Error) and result.kind is ErrorKind.INVALID_INPUT


async def test_both_query_and_table_is_invalid_input(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    result = await SQLiteQueryConnector().fetch(_uri(db, "?table=users&query=SELECT%201"))
    assert isinstance(result, Error) and result.kind is ErrorKind.INVALID_INPUT


async def test_can_handle_only_sqlite_scheme() -> None:
    assert SQLiteQueryConnector.can_handle("sqlite:///a.db")
    assert not SQLiteQueryConnector.can_handle("postgres://h/db")
