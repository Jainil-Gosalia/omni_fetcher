"""Unit tests for the shared SQL query base (``connectors._sql_query``).

The pure helpers behind both SQL query connectors: cell coercion, identifier
quoting, the table-reference builder, the statement resolver, the row-cap
parser, and the result fold. Driver-free and deterministic.
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from uuid import UUID

import pytest

from omni_fetcher.v1.connectors._sql_query import (
    DEFAULT_ROW_CAP,
    MAX_ROW_CAP,
    build_query_result,
    build_select_star,
    coerce_cell,
    parse_sql_uri,
    quote_identifier,
    resolve_row_cap,
    resolve_statement,
)
from omni_fetcher.v1.result import Partial, ResultAdapter, Success


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (5, 5),
        (3.5, 3.5),
        ("x", "x"),
        (Decimal("1.50"), "1.50"),
        (datetime.date(2026, 7, 18), "2026-07-18"),
        (UUID("12345678-1234-5678-1234-567812345678"), "12345678-1234-5678-1234-567812345678"),
        (b"\xde\xad", "dead"),
        ({"a": Decimal("2")}, {"a": "2"}),
        ([1, b"\x01"], [1, "01"]),
    ],
)
def test_coerce_cell(value, expected) -> None:
    assert coerce_cell(value) == expected


def test_coerce_cell_output_is_json_serialisable() -> None:
    row = [Decimal("1"), datetime.datetime(2026, 7, 18, 12), b"\x00", {"n": [1, 2]}]
    json.dumps([coerce_cell(cell) for cell in row])  # must not raise


def test_quote_identifier_rejects_injection() -> None:
    assert quote_identifier("users") == '"users"'
    with pytest.raises(ValueError):
        quote_identifier("users; DROP TABLE x")
    with pytest.raises(ValueError):
        quote_identifier('a"b')


def test_build_select_star_quotes_each_part() -> None:
    assert build_select_star("users", limit=10) == 'SELECT * FROM "users" LIMIT 10'
    assert build_select_star("public.users", limit=5) == 'SELECT * FROM "public"."users" LIMIT 5'
    with pytest.raises(ValueError):
        build_select_star("a.b.c", limit=1)


def test_build_select_star_honours_the_dialect_quote() -> None:
    # MySQL/MariaDB use backticks; the shared builder is parameterised on it.
    assert (
        build_select_star("app.users", limit=5, quote="`") == "SELECT * FROM `app`.`users` LIMIT 5"
    )
    assert quote_identifier("users", "`") == "`users`"


def test_resolve_row_cap() -> None:
    assert resolve_row_cap(None) == DEFAULT_ROW_CAP
    assert resolve_row_cap("50") == 50
    assert resolve_row_cap(str(MAX_ROW_CAP * 2)) == MAX_ROW_CAP  # clamped
    with pytest.raises(ValueError):
        resolve_row_cap("0")
    with pytest.raises(ValueError):
        resolve_row_cap("abc")


def test_resolve_statement_paths() -> None:
    assert (
        resolve_statement(
            table_ref=None, query="SELECT 1", query_env=None, environ={}, row_cap=1000
        )
        == "SELECT 1"
    )
    assert (
        resolve_statement(
            table_ref=None, query=None, query_env="Q", environ={"Q": "SELECT 2"}, row_cap=1000
        )
        == "SELECT 2"
    )
    assert (
        resolve_statement(table_ref="t", query=None, query_env=None, environ={}, row_cap=10)
        == 'SELECT * FROM "t" LIMIT 11'
    )


def test_resolve_statement_rejects_ambiguous_and_empty() -> None:
    with pytest.raises(ValueError):
        resolve_statement(table_ref="t", query="SELECT 1", query_env=None, environ={}, row_cap=1)
    with pytest.raises(ValueError):
        resolve_statement(table_ref=None, query=None, query_env=None, environ={}, row_cap=1)
    with pytest.raises(ValueError):
        resolve_statement(table_ref=None, query=None, query_env="MISSING", environ={}, row_cap=1)


def test_build_query_result_within_cap_is_success() -> None:
    result = build_query_result(
        "sqlite:///x", "sqlite", ["id", "name"], [[1, "a"], [2, "b"]], row_cap=1000
    )
    assert isinstance(result, Success)
    table = next(result.tree.iter_atoms())
    assert table.headers == ["id", "name"] and table.rows == [[1, "a"], [2, "b"]]
    assert result.tree.metadata.source_extra["sqlite"]["truncated"] is False


def test_build_query_result_over_cap_is_partial_and_round_trips() -> None:
    rows = [[i] for i in range(3)]  # cap+1 for cap=2
    result = build_query_result("sqlite:///x", "sqlite", ["id"], rows, row_cap=2)
    assert isinstance(result, Partial)
    assert len(next(result.tree.iter_atoms()).rows) == 2
    assert result.gaps and result.tree.metadata.source_extra["sqlite"]["truncated"] is True
    rebuilt = ResultAdapter.validate_python(json.loads(result.model_dump_json()))
    assert rebuilt.state.value == "partial"


def test_parse_sql_uri_host_port_database_and_params() -> None:
    spec = parse_sql_uri(
        "mysql://db.example.com:3307/app?table=app.users&limit=5&user=bob&password=pw",
        scheme="mysql://",
        default_port=3306,
    )
    assert (spec.host, spec.port, spec.database) == ("db.example.com", 3307, "app")
    assert spec.table == "app.users" and spec.limit == "5"
    assert (spec.user, spec.password) == ("bob", "pw")


def test_parse_sql_uri_defaults_port_and_rejects_malformed() -> None:
    spec = parse_sql_uri(
        "postgres://h/db?query=SELECT%201", scheme="postgres://", default_port=5432
    )
    # parse_qs URL-decodes, so %20 becomes a space -- the SQL that will run.
    assert spec.port == 5432 and spec.query == "SELECT 1"
    with pytest.raises(ValueError):
        parse_sql_uri("postgres://h", scheme="postgres://", default_port=5432)  # no database
    with pytest.raises(ValueError):
        parse_sql_uri("mysql://h/db", scheme="postgres://", default_port=5432)  # scheme mismatch
    with pytest.raises(ValueError):
        parse_sql_uri("postgres://h:xx/db", scheme="postgres://", default_port=5432)  # bad port


def test_build_query_result_coerces_cells() -> None:
    result = build_query_result(
        "sqlite:///x", "sqlite", ["ts"], [[datetime.date(2026, 7, 18)]], row_cap=1000
    )
    assert next(result.tree.iter_atoms()).rows == [["2026-07-18"]]
