"""The canonical ``mysql`` query connector for the v1 contract (v1.10).

Runs a read query against MySQL or MariaDB (wire-compatible; one connector, a
``mariadb://`` alias) and maps the result onto the canonical contract: one
``kind="query_result"`` node carrying a single ``Table`` atom (column names as
``headers``, rows as ``rows``), with the column list, row count, and truncation
flag in ``source_extra["mysql"]``.

The third connector in the SQL query family: it reuses the v1.9 shared spec
(``connectors._sql_query``) for URI parsing, the injection-safe ``SELECT *``
builder, the input resolver, JSON scalar coercion, and the row-cap fold, and
supplies only the three things that vary per database -- the driver, the
read-only mechanism, and the auth model (v1.10 PRD).

URI: ``mysql://<host>[:<port>]/<database>`` (or ``mariadb://``; default port
3306) with one of ``?table=<name>`` / ``?query=<url-encoded SELECT>`` /
``?query_env=<ENV_NAME>``, plus ``?limit=`` and ``?user=``/``?password=``.

Read-only is enforced by the engine, not by parsing (D5): every query runs inside
``START TRANSACTION READ ONLY``, so a write is refused by the server (error 1792)
and mapped to ``PERMISSION_DENIED``. Credentials arrive as a per-call
``BasicAuth`` -- so the MCP server injects them from
``OMNI_FETCHER_MYSQL_USERNAME`` / ``_PASSWORD`` (D6) -- with URI
``?user=``/``?password=`` as a fallback; explicit ``auth`` wins.

aiomysql is optional (the ``mysql`` extra): this module imports without it,
``builtin_registry()`` skips the source when it is missing, and direct use yields
a typed ``UNSUPPORTED`` naming the extra. All database access flows through the
``_make_executor`` seam, so tests script a fake and never touch a live server.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any, AsyncIterator, Optional, Protocol

from omni_fetcher.v1.auth import AuthCredential, BasicAuth
from omni_fetcher.v1.connectors._sql_query import (
    MYSQL_QUOTE,
    SqlQuerySpec,
    build_query_result,
    parse_sql_uri,
    resolve_row_cap,
    resolve_statement,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.result import Result, error, from_exception
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace for MySQL/MariaDB descriptive metadata in ``source_extra``.
SOURCE_NAMESPACE = "mysql"

# Whether the optional aiomysql client is importable (the ``mysql`` extra).
AIOMYSQL_AVAILABLE = importlib.util.find_spec("aiomysql") is not None

_SCHEMES = ("mysql://", "mariadb://")
_DEFAULT_PORT = 3306

# MySQL/MariaDB numeric error code -> v1 error taxonomy (D7). ``1792`` is
# ``ER_CANT_EXECUTE_IN_READ_ONLY_TRANSACTION`` -- a write refused by the
# ``START TRANSACTION READ ONLY``.
_ERROR_CODE_KINDS: dict[int, ErrorKind] = {
    1045: ErrorKind.AUTH_FAILED,  # ER_ACCESS_DENIED_ERROR
    1044: ErrorKind.PERMISSION_DENIED,  # ER_DBACCESS_DENIED_ERROR
    1792: ErrorKind.PERMISSION_DENIED,  # ER_CANT_EXECUTE_IN_READ_ONLY_TRANSACTION
    1146: ErrorKind.NOT_FOUND,  # ER_NO_SUCH_TABLE
    1049: ErrorKind.NOT_FOUND,  # ER_BAD_DB_ERROR
    1064: ErrorKind.INVALID_INPUT,  # ER_PARSE_ERROR
    1054: ErrorKind.INVALID_INPUT,  # ER_BAD_FIELD_ERROR
    2003: ErrorKind.TRANSIENT,  # CR_CONN_HOST_ERROR
    2006: ErrorKind.TRANSIENT,  # CR_SERVER_GONE_ERROR
    2013: ErrorKind.TRANSIENT,  # CR_SERVER_LOST
}


class _QueryExecutor(Protocol):
    """The narrow DB seam: run one read query, return columns and rows.

    Implemented by the production aiomysql adapter and by test fakes, so the
    connector's routing/auth/fold/error logic is exercised without a live server.
    """

    async def run(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        """Run ``sql`` READ ONLY and return ``(columns, rows)`` (up to cap+1)."""
        ...


def _scheme_of(uri: str) -> Optional[str]:
    """Return the matched scheme prefix (``mysql://`` / ``mariadb://``) or None."""
    for scheme in _SCHEMES:
        if uri.startswith(scheme):
            return scheme
    return None


def _resolve_credentials(
    spec: SqlQuerySpec, auth: Optional[AuthCredential]
) -> tuple[Optional[str], Optional[str]]:
    """Resolve ``(user, password)``: per-call ``BasicAuth`` wins, URI is the fallback (D6)."""
    if isinstance(auth, BasicAuth):
        return auth.username, auth.password
    return spec.user, spec.password


def _error_code(exc: Exception) -> Optional[int]:
    """Extract a MySQL numeric error code from an aiomysql/pymysql exception."""
    args = getattr(exc, "args", None)
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _map_mysql_error(exc: Exception) -> ErrorKind:
    """Map an aiomysql/pymysql exception onto the v1 error taxonomy (D7)."""
    code = _error_code(exc)
    if code is not None and code in _ERROR_CODE_KINDS:
        return _ERROR_CODE_KINDS[code]
    if isinstance(exc, (OSError, TimeoutError)):
        return ErrorKind.TRANSIENT
    return ErrorKind.PARSE_ERROR


class _AiomysqlExecutor:
    """Production executor: run a query inside ``START TRANSACTION READ ONLY``.

    NOTE:
        The row cap is applied to the returned ``Table`` by the caller. A table
        browse already carries ``LIMIT cap+1`` in its SQL; a raw ``?query=`` is
        bounded by ``fetchmany(cap+1)`` on the client. Callers of very large raw
        queries should still include their own ``LIMIT`` (the default buffered
        cursor materialises the full result server-side first).
    """

    def __init__(self, spec: SqlQuerySpec, user: Optional[str], password: Optional[str]) -> None:
        self._spec = spec
        self._user = user
        self._password = password

    async def run(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        import aiomysql  # imported lazily; gated by AIOMYSQL_AVAILABLE at the boundary

        connection = await aiomysql.connect(
            host=self._spec.host,
            port=self._spec.port,
            db=self._spec.database,
            user=self._user,
            password=self._password,
        )
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("START TRANSACTION READ ONLY")
                await cursor.execute(sql)
                fetched = await cursor.fetchmany(row_cap + 1)
                columns = [description[0] for description in (cursor.description or [])]
                rows = [list(row) for row in fetched]
                await connection.rollback()
            return columns, rows
        finally:
            connection.close()


class MySQLQueryConnector(BaseFetcher):
    """
    Canonical v1 connector for read queries against MySQL / MariaDB
    ===============================================
    Runs a SELECT (or a table browse) inside ``START TRANSACTION READ ONLY`` and
    emits one ``kind="query_result"`` node with a single ``Table`` atom.
    Descriptive fields live in ``source_extra["mysql"]``; the atom carries
    content only.
    ===============================================
    NOTE:
        1. Implements only ``stream()``; ``fetch()`` is inherited and collects
           the bounded one-item stream into a single ``Result``.
        2. Read-only is enforced by ``START TRANSACTION READ ONLY`` -- a write is
           refused by the server (error 1792) and mapped to ``PERMISSION_DENIED``.
        3. aiomysql is optional (the ``mysql`` extra); without it the connector
           yields a typed ``UNSUPPORTED``.

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
        Stream the canonical result for one MySQL / MariaDB read query

        Yields exactly one ``Result``: a ``Success`` with a ``Table``-bearing
        ``query_result`` node, a ``Partial`` when the row cap truncated the
        result, or a typed ``Error`` (unsupported extra, bad input, auth failure,
        a rejected write, a missing table).

        NOTE:
            1. ``zoom`` is accepted for protocol conformance; a query result has
               one natural granularity (a table).
            2. Credentials come from ``auth`` (a ``BasicAuth``) when supplied,
               else from the URI ``?user=``/``?password=``.

        Parameters
        ----------
            uri:
                The ``mysql://`` or ``mariadb://`` source URI.
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

        if not AIOMYSQL_AVAILABLE:
            yield error(
                ErrorKind.UNSUPPORTED,
                message=(
                    "aiomysql is not installed; install the 'mysql' extra "
                    '(pip install "omni-fetcher[mysql]") to query mysql://'
                ),
                locator=uri,
            )
            return

        try:
            scheme = _scheme_of(uri)
            if scheme is None:
                raise ValueError(f"not a mysql:// or mariadb:// URI: {uri}")
            spec = parse_sql_uri(uri, scheme=scheme, default_port=_DEFAULT_PORT)
            row_cap = resolve_row_cap(spec.limit)
            sql = resolve_statement(
                table_ref=spec.table,
                query=spec.query,
                query_env=spec.query_env,
                environ=os.environ,
                row_cap=row_cap,
                quote=MYSQL_QUOTE,  # MySQL/MariaDB quote identifiers with backticks
            )
        except ValueError as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return

        user, password = _resolve_credentials(spec, auth)
        executor = self._make_executor(spec, user, password)

        try:
            columns, rows = await executor.run(sql, row_cap)
        except Exception as exc:  # noqa: BLE001 - mapped onto the typed taxonomy
            yield from_exception(exc, kind=_map_mysql_error(exc), locator=uri)
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
        self, spec: SqlQuerySpec, user: Optional[str], password: Optional[str]
    ) -> _QueryExecutor:
        """Build the DB executor (the test seam). Overridden by fakes in tests."""
        return _AiomysqlExecutor(spec, user, password)

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
                ``True`` for a ``mysql://`` or ``mariadb://`` URI.
        """
        return _scheme_of(uri) is not None
