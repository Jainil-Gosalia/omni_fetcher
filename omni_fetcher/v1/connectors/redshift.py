"""The canonical ``redshift`` query connector for the v1 contract (v1.13).

Runs a read query against Amazon Redshift and maps the result onto the canonical
contract: one ``kind="query_result"`` node carrying a single ``Table`` atom
(column names as ``headers``, result rows as ``rows``), with the column list,
row count, and truncation flag in ``source_extra["redshift"]``. The warehouse
member of the SQL query family that keeps the engine-level read-only guarantee:
Redshift speaks the PostgreSQL wire protocol and supports a ``READ ONLY``
transaction, so this connector reuses the v1.9 ``_sql_query`` spec whole and
differs from ``postgres://`` only in the driver (``redshift_connector``, run on a
worker thread) and the port.

URI: ``redshift://<host>[:<port>]/<database>`` (default port 5439) with one of:

- ``?table=<schema.table>`` -- browse a table (``SELECT *`` under the row cap);
- ``?query=<url-encoded SELECT>`` -- an arbitrary read query;
- ``?query_env=<ENV_NAME>`` -- read the SQL from an environment variable.

Plus ``?limit=<n>`` to raise the row cap, and ``?user=``/``?password=`` as a
CLI-convenience credential fallback.

Read-only is enforced by the engine, not by parsing (v1.9 PRD, D5): every query
runs after ``SET TRANSACTION READ ONLY`` in an un-committed transaction, so any
write is refused by Redshift itself (SQLSTATE ``25006`` -> ``PERMISSION_DENIED``)
and the transaction is rolled back. Credentials arrive as a per-call
``BasicAuth`` -- so the MCP server can inject them from
``OMNI_FETCHER_REDSHIFT_USERNAME`` / ``_PASSWORD`` (D8) -- with the URI
``?user=``/``?password=`` as a fallback; explicit ``auth`` wins.

``redshift_connector`` is optional (the ``redshift`` extra): this module imports
without it, ``builtin_registry()`` skips the source when it is missing, and
direct use yields a typed ``UNSUPPORTED`` naming the extra. All database access
flows through the ``_connect`` seam, so tests script a fake DBAPI connection and
never touch a live Redshift.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from typing import Any, AsyncIterator, Optional, Protocol

from omni_fetcher.v1.auth import AuthCredential, BasicAuth
from omni_fetcher.v1.connectors._sql_query import (
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

# Source namespace for Redshift descriptive metadata in ``source_extra``.
SOURCE_NAMESPACE = "redshift"

# Whether the optional redshift_connector client is importable (``redshift`` extra).
REDSHIFT_AVAILABLE = importlib.util.find_spec("redshift_connector") is not None

_SCHEME = "redshift://"
_DEFAULT_PORT = 5439

# PostgreSQL/Redshift SQLSTATE -> v1 error taxonomy (D10). ``25006`` is
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
    """The narrow DB seam: run one read query, return columns and rows."""

    async def run(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        """Run ``sql`` READ ONLY and return ``(columns, rows)`` (up to cap+1)."""
        ...


def _resolve_credentials(
    spec: SqlQuerySpec, auth: Optional[AuthCredential]
) -> tuple[Optional[str], Optional[str]]:
    """Resolve ``(user, password)``: per-call ``BasicAuth`` wins, URI is the fallback (D8)."""
    if isinstance(auth, BasicAuth):
        return auth.username, auth.password
    return spec.user, spec.password


def _sqlstate_of(exc: Exception) -> Optional[str]:
    """Best-effort extraction of a SQLSTATE from a redshift_connector error.

    ``redshift_connector`` surfaces the server error fields either as a
    ``.sqlstate``-like attribute or as a mapping in ``args[0]`` keyed ``"C"``
    (the wire code field). Return whichever is found, else ``None``.
    """
    for attr in ("sqlstate", "pgcode", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, str) and value:
            return value
    if exc.args and isinstance(exc.args[0], dict):
        code = exc.args[0].get("C") or exc.args[0].get("code")
        if isinstance(code, str) and code:
            return code
    return None


def _map_redshift_error(exc: Exception) -> ErrorKind:
    """Map a redshift_connector exception onto the v1 error taxonomy (D10)."""
    sqlstate = _sqlstate_of(exc)
    if sqlstate and sqlstate in _SQLSTATE_KINDS:
        return _SQLSTATE_KINDS[sqlstate]
    if isinstance(exc, (OSError, TimeoutError)):
        return ErrorKind.TRANSIENT
    if sqlstate and sqlstate.startswith("08"):  # connection exception class
        return ErrorKind.TRANSIENT
    if sqlstate and sqlstate.startswith("42"):  # syntax / access-rule class
        return ErrorKind.INVALID_INPUT
    message = str(exc).lower()
    if "read-only" in message or "read only" in message:
        return ErrorKind.PERMISSION_DENIED
    if "does not exist" in message or "not found" in message:
        return ErrorKind.NOT_FOUND
    if "authentication" in message or "password" in message:
        return ErrorKind.AUTH_FAILED
    if "syntax" in message:
        return ErrorKind.INVALID_INPUT
    return ErrorKind.PARSE_ERROR


def _connect(spec: SqlQuerySpec, user: Optional[str], password: Optional[str]) -> Any:
    """Open a redshift_connector DBAPI connection (the seam tests replace).

    The heavy import is deferred to here so the module imports on a base
    install; the connection is built from the resolved credentials only.
    """
    import redshift_connector

    return redshift_connector.connect(
        host=spec.host,
        port=spec.port,
        database=spec.database,
        user=user,
        password=password,
    )


class _RedshiftExecutor:
    """Production executor: run a query READ ONLY over a redshift_connector connection."""

    def __init__(self, spec: SqlQuerySpec, user: Optional[str], password: Optional[str]) -> None:
        self._spec = spec
        self._user = user
        self._password = password

    async def run(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        return await asyncio.to_thread(self._run_sync, sql, row_cap)

    def _run_sync(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        connection = _connect(self._spec, self._user, self._password)
        try:
            connection.autocommit = False
            cursor = connection.cursor()
            # Read-only enforced by the engine: SET TRANSACTION READ ONLY as the
            # first statement of the transaction, so a write raises 25006.
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(sql)
            fetched = cursor.fetchmany(row_cap + 1)
            columns = [description[0] for description in (cursor.description or [])]
            rows = [list(row) for row in fetched]
            connection.rollback()
            return columns, rows
        finally:
            connection.close()


class RedshiftQueryConnector(BaseFetcher):
    """
    Canonical v1 connector for read queries against Amazon Redshift
    ===============================================
    Runs a SELECT (or a table browse) after ``SET TRANSACTION READ ONLY`` and
    emits one ``kind="query_result"`` node with a single ``Table`` atom.
    Descriptive fields live in ``source_extra["redshift"]``; the atom carries
    content only.
    ===============================================
    NOTE:
        1. Implements only ``stream()``; ``fetch()`` is inherited and collects
           the bounded one-item stream into a single ``Result``.
        2. Read-only is enforced by ``SET TRANSACTION READ ONLY`` -- a write is
           refused by Redshift (SQLSTATE 25006) and mapped to
           ``PERMISSION_DENIED``; the transaction is rolled back.
        3. ``redshift_connector`` is optional (the ``redshift`` extra); without
           it the connector yields a typed ``UNSUPPORTED``.

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
        Stream the canonical result for one Redshift read query

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
                The ``redshift://`` source URI.
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

        if not REDSHIFT_AVAILABLE:
            yield error(
                ErrorKind.UNSUPPORTED,
                message=(
                    "redshift_connector is not installed; install the 'redshift' extra "
                    '(pip install "omni-fetcher[redshift]") to query redshift://'
                ),
                locator=uri,
            )
            return

        try:
            spec = parse_sql_uri(uri, scheme=_SCHEME, default_port=_DEFAULT_PORT)
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
            yield from_exception(exc, kind=_map_redshift_error(exc), locator=uri)
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
        return _RedshiftExecutor(spec, user, password)

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
                ``True`` for a ``redshift://`` URI.
        """
        return uri.startswith(_SCHEME)
