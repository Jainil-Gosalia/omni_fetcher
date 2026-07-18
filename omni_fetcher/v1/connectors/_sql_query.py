"""Shared spec for the SQL query connector family (v1.9).

The bounded "run a read query, get a ``Table`` atom" shape is the same across
SQL databases; what differs per database is the driver, the read-only
*mechanism*, and the auth model. This module holds the part that is genuinely
shared -- and *only* that part (see the v1.9 PRD, D13):

- :func:`coerce_cell` -- deterministic coercion of a driver's native Python
  value to a JSON-round-trippable scalar (D7).
- :func:`quote_identifier` / :func:`build_select_star` -- the table-reference to
  ``SELECT *`` builder, with standard double-quote identifier quoting (D3).
- :func:`resolve_statement` -- resolve the caller's input (a table reference, a
  ``?query=``, or a ``?query_env=``) into the SQL string to run (D3/D4).
- :func:`resolve_row_cap` -- parse and clamp ``?limit=`` (D9).
- :func:`build_query_result` -- fold fetched rows into one ``kind="query_result"``
  node carrying a ``Table`` atom, applying the row cap as an honest truncation
  ``Gap`` (D6/D9).

Each connector owns its own connection, read-only enforcement, credentials, and
error mapping. This is a spec, not a base class doing the work. Deliberately not
a generic SQL connector: dialects and type systems differ too much (D1).
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs
from uuid import UUID

from omni_fetcher.v1.atoms import Table
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.result import Gap, Result, gap, partial, success
from omni_fetcher.v1.errors import ErrorKind

# Advisory semantic ``kind`` for every node a query connector emits.
QUERY_RESULT_KIND = "query_result"

# Row-cap defaults (D9). A query returns at most ``DEFAULT_ROW_CAP`` rows unless
# ``?limit=`` raises it, never above ``MAX_ROW_CAP``.
DEFAULT_ROW_CAP = 1000
MAX_ROW_CAP = 100_000

# A SQL identifier we are willing to quote and interpolate into ``SELECT *``.
# Deliberately strict: letters, digits, underscore, starting non-numeric. Any
# real object name that does not match must be reached via an explicit
# ``?query=`` instead, so the table-reference path can never carry injection.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class SqlQuerySpec:
    """Parsed routing decision shared by the server-based SQL query connectors.

    ``host[:port]/database`` plus the statement inputs (a table reference, an
    inline query, or an env-var-named query), the row-cap override, and the URI
    credential fallback. Every server SQL connector (Postgres, MySQL, ...) parses
    into this same shape via :func:`parse_sql_uri`; only the driver, the
    read-only mechanism, and the auth model differ per database.
    """

    host: str
    port: int
    database: str
    table: Optional[str]
    query: Optional[str]
    query_env: Optional[str]
    limit: Optional[str]
    user: Optional[str]
    password: Optional[str]


def parse_sql_uri(uri: str, *, scheme: str, default_port: int) -> SqlQuerySpec:
    """
    Parse a ``<scheme>host[:port]/database?...`` URI into a :class:`SqlQuerySpec`

    Shared by the server-based SQL query connectors (``postgres://``,
    ``mysql://``, ...): the location is ``host[:port]/database`` and the query
    string carries ``table`` / ``query`` / ``query_env`` / ``limit`` / ``user`` /
    ``password``. Raises ``ValueError`` (mapped to ``INVALID_INPUT`` at the
    boundary) for a malformed URI.

    Parameters
    ----------
        uri:
            The full source URI.
        scheme:
            The exact matched scheme prefix (e.g. ``"postgres://"``,
            ``"mysql://"``, ``"mariadb://"``).
        default_port:
            The port to use when the URI omits one.

    Return
    ------
        spec:
            The parsed routing decision.
    """
    if not uri.startswith(scheme):
        raise ValueError(f"not a {scheme} URI: {uri}")
    remainder = uri[len(scheme) :]
    location, _, query = remainder.partition("?")
    host_part, _, database = location.partition("/")
    if not host_part or not database or "/" in database:
        raise ValueError(f"{scheme} URI must be {scheme}host[:port]/database: {uri}")

    if ":" in host_part:
        host, port_text = host_part.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError(f"{scheme} port must be numeric: {port_text}") from exc
    else:
        host = host_part
        port = default_port

    params = parse_qs(query)
    return SqlQuerySpec(
        host=host,
        port=port,
        database=database,
        table=params.get("table", [None])[0],
        query=params.get("query", [None])[0],
        query_env=params.get("query_env", [None])[0],
        limit=params.get("limit", [None])[0],
        user=params.get("user", [None])[0],
        password=params.get("password", [None])[0],
    )


def coerce_cell(value: Any) -> Any:
    """
    Coerce a driver's native value to a JSON-round-trippable scalar

    The fixed, shared coercion (D7): the returned value is always one of
    ``None`` / ``bool`` / ``int`` / ``float`` / ``str`` / ``list`` / ``dict`` so
    the enclosing ``Table`` round-trips through ``ResultAdapter`` unchanged.
    Containers are coerced recursively; anything unrecognised falls back to its
    string form rather than raising -- a query result is never rejected because
    a column had an exotic type.

    Parameters
    ----------
        value:
            A cell value as returned by the database driver.

    Return
    ------
        cell:
            A JSON-serialisable value.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, Mapping):
        return {str(k): coerce_cell(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [coerce_cell(v) for v in value]
    return str(value)


def coerce_row(row: Iterable[Any]) -> list[Any]:
    """Coerce every cell of a row (see :func:`coerce_cell`)."""
    return [coerce_cell(cell) for cell in row]


# Identifier quote characters. Standard SQL (PostgreSQL, SQLite) uses the
# double quote; MySQL/MariaDB use the backtick unless ANSI_QUOTES is set. The
# connector passes its dialect's character -- the one place identifier quoting
# is not portable, surfaced by MySQL as the SQL query family's second customer.
STANDARD_QUOTE = '"'
MYSQL_QUOTE = "`"


def quote_identifier(part: str, quote: str = STANDARD_QUOTE) -> str:
    """
    Quote one SQL identifier for safe interpolation

    Accepts only a strict identifier (``[A-Za-z_][A-Za-z0-9_]*``) and wraps it in
    ``quote`` -- the double quote for standard SQL (PostgreSQL, SQLite), the
    backtick for MySQL/MariaDB. Raises ``ValueError`` for anything else, so a
    table reference can never carry injection; exotic names go through an
    explicit ``?query=``.

    Parameters
    ----------
        part:
            A single identifier (a schema or table name).
        quote:
            The dialect's identifier quote character (default ``"``).

    Return
    ------
        quoted:
            The quoted identifier.
    """
    if not _IDENTIFIER.match(part):
        raise ValueError(
            f"unsafe SQL identifier {part!r}; use ?query= for names with dots, spaces, or quoting"
        )
    return f"{quote}{part}{quote}"


def build_select_star(table_ref: str, *, limit: int, quote: str = STANDARD_QUOTE) -> str:
    """
    Build a ``SELECT * FROM <table> LIMIT <n>`` for a table reference

    ``table_ref`` is a ``table`` or ``schema.table`` reference; each dotted part
    is validated and quoted via :func:`quote_identifier` using ``quote`` (the
    dialect's identifier quote character). The ``LIMIT`` is applied in SQL (cap+1
    detection happens in :func:`build_query_result`) so a table browse never
    scans an unbounded table.

    Parameters
    ----------
        table_ref:
            A ``table`` or ``schema.table`` reference.
        limit:
            The row limit to apply in the generated SQL.
        quote:
            The dialect's identifier quote character (default ``"``).

    Return
    ------
        sql:
            The generated ``SELECT`` statement.
    """
    parts = table_ref.split(".")
    if not 1 <= len(parts) <= 2 or any(not p for p in parts):
        raise ValueError(f"table reference must be 'table' or 'schema.table': {table_ref!r}")
    quoted = ".".join(quote_identifier(p, quote) for p in parts)
    return f"SELECT * FROM {quoted} LIMIT {int(limit)}"


def resolve_row_cap(limit_param: Optional[str]) -> int:
    """
    Resolve the effective row cap from an optional ``?limit=`` value

    Returns :data:`DEFAULT_ROW_CAP` when unset, else the parsed value clamped to
    ``[1, MAX_ROW_CAP]``. Raises ``ValueError`` on a non-integer.

    Parameters
    ----------
        limit_param:
            The raw ``?limit=`` string, or ``None``.

    Return
    ------
        cap:
            The effective maximum row count.
    """
    if limit_param is None:
        return DEFAULT_ROW_CAP
    try:
        value = int(limit_param)
    except ValueError as exc:
        raise ValueError(f"limit must be an integer: {limit_param!r}") from exc
    if value < 1:
        raise ValueError(f"limit must be >= 1: {value}")
    return min(value, MAX_ROW_CAP)


def resolve_statement(
    *,
    table_ref: Optional[str],
    query: Optional[str],
    query_env: Optional[str],
    environ: Mapping[str, str],
    row_cap: int,
    quote: str = STANDARD_QUOTE,
) -> str:
    """
    Resolve the caller's input into the SQL statement to run

    Exactly one input must be supplied (D3/D4): a table reference (from the URI
    path), an inline ``?query=``, or a ``?query_env=`` naming an environment
    variable that holds the SQL. More than one is ambiguous, none is missing --
    both raise ``ValueError`` (mapped to ``INVALID_INPUT`` at the boundary).

    Parameters
    ----------
        table_ref:
            A ``table`` / ``schema.table`` reference from the URI path, or
            ``None``.
        query:
            An inline SQL statement (``?query=``), or ``None``.
        query_env:
            The name of an environment variable holding the SQL (``?query_env=``),
            or ``None``.
        environ:
            The environment mapping used to resolve ``query_env``.
        row_cap:
            The row cap, applied as ``LIMIT`` only for a table reference (a raw
            query is capped by bounded fetching, not by rewriting its SQL).

    Return
    ------
        sql:
            The SQL statement to execute.
    """
    supplied = [
        name
        for name, value in (
            ("table", table_ref),
            ("query", query),
            ("query_env", query_env),
        )
        if value
    ]
    if len(supplied) > 1:
        raise ValueError(
            f"exactly one of a table reference, ?query=, or ?query_env= may be "
            f"given; got {', '.join(supplied)}"
        )
    if not supplied:
        raise ValueError("no query given: use a /table path, ?query=, or ?query_env=")

    if query is not None:
        return query
    if query_env is not None:
        sql = environ.get(query_env)
        if not sql:
            raise ValueError(
                f"?query_env names environment variable {query_env!r}, which is not set"
            )
        return sql
    assert table_ref is not None  # exactly-one guarantees this branch.
    # cap + 1 so an over-cap table is detectable as a truncation, exactly as a
    # raw query's bounded fetch(cap + 1) is.
    return build_select_star(table_ref, limit=row_cap + 1, quote=quote)


def build_query_result(
    uri: str,
    namespace: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    row_cap: int,
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> Result:
    """
    Fold fetched rows into one canonical ``query_result`` ``Result``

    Builds a ``kind="query_result"`` node carrying a single ``Table`` atom
    (``headers`` = ``columns``, ``rows`` = the coerced rows). ``rows`` may hold
    up to ``row_cap + 1`` rows: an over-cap result is truncated to ``row_cap``
    and returned as a ``Partial`` whose ``Gap`` names the cap, so truncation is
    never silent (D9). Descriptive fields land in ``source_extra[namespace]``.

    Parameters
    ----------
        uri:
            The source URI (recorded as ``source_url`` and gap locator).
        namespace:
            The ``source_extra`` namespace for this connector (``"postgres"`` /
            ``"sqlite"``).
        columns:
            The result column names, in order.
        rows:
            The fetched rows (each an ordered sequence of cell values), up to
            ``row_cap + 1``.
        row_cap:
            The applied row cap.
        extra_fields:
            Extra descriptive fields merged into ``source_extra[namespace]``.

    Return
    ------
        result:
            A ``Success`` (within cap) or ``Partial`` (truncated) result.
    """
    headers = [str(column) for column in columns]
    truncated = len(rows) > row_cap
    kept = rows[:row_cap] if truncated else rows
    coerced = [coerce_row(row) for row in kept]

    table = Table(headers=headers, rows=coerced)
    fields: dict[str, Any] = {
        "columns": headers,
        "row_count": len(coerced),
        "truncated": truncated,
        "row_cap": row_cap,
    }
    if extra_fields:
        fields.update(extra_fields)

    node = build_node(
        kind=QUERY_RESULT_KIND,
        atoms=[table],
        source_url=uri,
        source_namespace=namespace,
        source_fields=fields,
    )

    if truncated:
        truncation: Gap = gap(
            kind=ErrorKind.UNSUPPORTED,
            locator=uri,
            detail=(
                f"result truncated to the {row_cap}-row cap; more rows exist. "
                "Raise ?limit= (up to the hard ceiling) or narrow the query"
            ),
        )
        return partial(node, [truncation])
    return success(node)
