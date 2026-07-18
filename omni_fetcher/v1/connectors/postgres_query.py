"""The canonical ``postgres`` query connector for the v1 contract (v1.9).

Runs a read query against PostgreSQL and maps the result onto the canonical
contract: one ``kind="query_result"`` node carrying a single ``Table`` atom
(column names as ``headers``, result rows as ``rows``), with the column list,
row count, and truncation flag in ``source_extra["postgres"]``. The read-side
complement to ``postgres-cdc://``; the shared query spec lives in
``omni_fetcher.v1.connectors._sql_query``.

URI: ``postgres://<host>[:<port>]/<database>`` (default port 5432) with one of:

- ``?table=<schema.table>`` -- browse a table (``SELECT *`` under the row cap);
- ``?query=<url-encoded SELECT>`` -- an arbitrary read query;
- ``?query_env=<ENV_NAME>`` -- read the SQL from an environment variable.

Plus ``?limit=<n>`` to raise the row cap, and ``?user=``/``?password=`` as a
CLI-convenience credential fallback.

Read-only is enforced by the engine, not by parsing (v1.9 PRD, D5): every query
runs inside a ``READ ONLY`` transaction, so any write is refused by PostgreSQL
itself. Credentials arrive as a per-call ``BasicAuth`` -- so the MCP server can
inject them from ``OMNI_FETCHER_POSTGRES_USERNAME`` / ``_PASSWORD`` (D8) -- with
the URI ``?user=``/``?password=`` as a fallback; explicit ``auth`` wins.

asyncpg is optional (the ``postgres`` extra): this module imports without it,
``builtin_registry()`` skips the source when it is missing, and direct use
yields a typed ``UNSUPPORTED`` naming the extra. All database access flows
through the ``_make_executor`` seam, so tests script a fake and never touch a
live PostgreSQL.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any, AsyncIterator, Optional, Protocol
from urllib.parse import parse_qs

from omni_fetcher.v1.auth import AuthCredential, BasicAuth
from omni_fetcher.v1.connectors._sql_query import (
    build_query_result,
    resolve_row_cap,
    resolve_statement,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.result import Result, error, from_exception
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace for PostgreSQL descriptive metadata in ``source_extra``
# (shared with the CDC connector -- one database, two connectors).
SOURCE_NAMESPACE = "postgres"

# Whether the optional asyncpg client is importable (the ``postgres`` extra).
ASYNCPG_AVAILABLE = importlib.util.find_spec("asyncpg") is not None

_SCHEME = "postgres://"
_DEFAULT_PORT = 5432

# PostgreSQL SQLSTATE -> v1 error taxonomy (D10). ``25006`` is
# ``read_only_sql_transaction`` -- a write refused by the READ ONLY transaction.
_SQLSTATE_KINDS: dict[str, ErrorKind] = {
    "28P01": ErrorKind.AUTH_FAILED,
    "28000": ErrorKind.AUTH_FAILED,
    "42501": ErrorKind.PERMISSION_DENIED,
    "25006": ErrorKind.PERMISSION_DENIED,
    "42P01": ErrorKind.NOT_FOUND,
    "3D000": ErrorKind.NOT_FOUND,
    "42601": ErrorKind.INVALID_INPUT,
    "42703": ErrorKind.INVALID_INPUT,
}


class _QueryExecutor(Protocol):
    """The narrow DB seam: run one read query, return columns and rows.

    Implemented by the production asyncpg adapter and by test fakes, so the
    connector's routing/auth/fold/error logic is exercised without a live
    PostgreSQL.
    """

    async def run(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        """Run ``sql`` READ ONLY and return ``(columns, rows)`` (up to cap+1)."""
        ...


class _PostgresQuerySpec:
    """Parsed ``postgres://`` routing decision (target + statement + creds)."""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        table: Optional[str],
        query: Optional[str],
        query_env: Optional[str],
        limit: Optional[str],
        user: Optional[str],
        password: Optional[str],
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.table = table
        self.query = query
        self.query_env = query_env
        self.limit = limit
        self.user = user
        self.password = password


def _parse_uri(uri: str) -> _PostgresQuerySpec:
    """Parse a ``postgres://`` URI into a spec, raising ``ValueError`` when bad."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a postgres:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    location, _, query = remainder.partition("?")
    host_part, _, database = location.partition("/")
    if not host_part or not database or "/" in database:
        raise ValueError(f"postgres:// URI must be postgres://host[:port]/database: {uri}")

    if ":" in host_part:
        host, port_text = host_part.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError(f"postgres:// port must be numeric: {port_text}") from exc
    else:
        host = host_part
        port = _DEFAULT_PORT

    params = parse_qs(query)
    return _PostgresQuerySpec(
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


def _resolve_credentials(
    spec: _PostgresQuerySpec, auth: Optional[AuthCredential]
) -> tuple[Optional[str], Optional[str]]:
    """Resolve ``(user, password)``: per-call ``BasicAuth`` wins, URI is the fallback (D8)."""
    if isinstance(auth, BasicAuth):
        return auth.username, auth.password
    return spec.user, spec.password


def _map_sqlstate(exc: Exception) -> ErrorKind:
    """Map an asyncpg/Postgres exception onto the v1 error taxonomy (D10)."""
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate and sqlstate in _SQLSTATE_KINDS:
        return _SQLSTATE_KINDS[sqlstate]
    if isinstance(exc, (OSError, TimeoutError)):
        return ErrorKind.TRANSIENT
    if sqlstate and sqlstate.startswith("08"):  # connection exception class
        return ErrorKind.TRANSIENT
    if sqlstate and sqlstate.startswith("42"):  # syntax error / access rule class
        return ErrorKind.INVALID_INPUT
    return ErrorKind.PARSE_ERROR


class _AsyncpgExecutor:
    """Production executor: run a query inside a ``READ ONLY`` asyncpg transaction.

    NOTE:
        The row cap is applied to the returned ``Table`` by the caller. A table
        browse already carries ``LIMIT cap+1`` in its SQL; a raw ``?query=`` is
        fetched in full and then capped -- callers of very large raw queries
        should include their own ``LIMIT``. Server-side-cursor bounding of raw
        queries is a follow-up.
    """

    def __init__(
        self, spec: _PostgresQuerySpec, user: Optional[str], password: Optional[str]
    ) -> None:
        self._spec = spec
        self._user = user
        self._password = password

    async def run(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        import asyncpg  # imported lazily; gated by ASYNCPG_AVAILABLE at the boundary

        connection = await asyncpg.connect(
            host=self._spec.host,
            port=self._spec.port,
            database=self._spec.database,
            user=self._user,
            password=self._password,
        )
        try:
            async with connection.transaction(readonly=True):
                statement = await connection.prepare(sql)
                columns = [attr.name for attr in statement.get_attributes()]
                records = await statement.fetch()
            rows = [list(record.values()) for record in records[: row_cap + 1]]
            return columns, rows
        finally:
            await connection.close()


class PostgresQueryConnector(BaseFetcher):
    """
    Canonical v1 connector for read queries against PostgreSQL
    ===============================================
    Runs a SELECT (or a table browse) inside a ``READ ONLY`` transaction and
    emits one ``kind="query_result"`` node with a single ``Table`` atom.
    Descriptive fields live in ``source_extra["postgres"]``; the atom carries
    content only.
    ===============================================
    NOTE:
        1. Implements only ``stream()``; ``fetch()`` is inherited and collects
           the bounded one-item stream into a single ``Result``.
        2. Read-only is enforced by a ``READ ONLY`` transaction -- a write is
           refused by PostgreSQL and mapped to ``PERMISSION_DENIED``.
        3. asyncpg is optional (the ``postgres`` extra); without it the
           connector yields a typed ``UNSUPPORTED``.

    Methods
    -------
        stream:
        can_handle:
    """

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical result for one PostgreSQL read query

        Yields exactly one ``Result``: a ``Success`` with a ``Table``-bearing
        ``query_result`` node, a ``Partial`` when the row cap truncated the
        result, or a typed ``Error`` (unsupported extra, bad input, auth
        failure, a rejected write, a missing table).

        NOTE:
            1. ``zoom`` is accepted for protocol conformance; a query result has
               one natural granularity (a table).
            2. Credentials come from ``auth`` (a ``BasicAuth``) when supplied,
               else from the URI ``?user=``/``?password=``.

        Parameters
        ----------
            uri:
                The ``postgres://`` source URI.
            auth:
                Optional per-call ``BasicAuth``; overrides URI credentials.
            zoom:
                Unused; accepted for protocol conformance.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        del zoom

        if not ASYNCPG_AVAILABLE:
            yield error(
                ErrorKind.UNSUPPORTED,
                message=(
                    "asyncpg is not installed; install the 'postgres' extra "
                    '(pip install "omni-fetcher[postgres]") to query postgres://'
                ),
                locator=uri,
            )
            return

        try:
            spec = _parse_uri(uri)
            row_cap = resolve_row_cap(spec.limit)
            sql = resolve_statement(
                table_ref=spec.table,
                query=spec.query,
                query_env=spec.query_env,
                environ=os.environ,
                row_cap=row_cap,
            )
        except ValueError as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return

        user, password = _resolve_credentials(spec, auth)
        executor = self._make_executor(spec, user, password)

        try:
            columns, rows = await executor.run(sql, row_cap)
        except Exception as exc:  # noqa: BLE001 - mapped onto the typed taxonomy
            yield from_exception(exc, kind=_map_sqlstate(exc), locator=uri)
            return

        yield build_query_result(
            uri,
            SOURCE_NAMESPACE,
            columns,
            rows,
            row_cap=row_cap,
            extra_fields={"database": spec.database, "host": spec.host},
        )

    def _make_executor(
        self, spec: _PostgresQuerySpec, user: Optional[str], password: Optional[str]
    ) -> _QueryExecutor:
        """Build the DB executor (the test seam). Overridden by fakes in tests."""
        return _AsyncpgExecutor(spec, user, password)

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether this connector claims a URI

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for a ``postgres://`` URI (not ``postgres-cdc://``).
        """
        return uri.startswith(_SCHEME)
