"""The canonical ``duckdb`` query connector for the v1 contract (v1.13).

Runs a read query against a local DuckDB database file and maps the result onto
the canonical contract: one ``kind="query_result"`` node carrying a single
``Table`` atom (column names as ``headers``, result rows as ``rows``), with the
column list, row count, and truncation flag in ``source_extra["duckdb"]``. The
embedded member of the SQL warehouse family (v1.13); the shared spec lives in
``omni_fetcher.v1.connectors._sql_query``.

URI: ``duckdb://<file-path>`` (``duckdb:///abs/path.duckdb`` for an absolute
path, ``duckdb://relative.duckdb`` for a relative one), with one of:

- ``?table=<name>`` -- browse a table (``SELECT * FROM <name>`` under the row cap);
- ``?query=<url-encoded SELECT>`` -- an arbitrary read query;
- ``?query_env=<ENV_NAME>`` -- read the SQL from an environment variable.

Plus ``?limit=<n>`` to raise the row cap.

Read-only is enforced by the engine, not by parsing (v1.9 PRD, D5): the database
is opened with ``read_only=True``, so any write fails with DuckDB's own refusal
(mapped to ``PERMISSION_DENIED``) and the file is left unchanged. The connector
needs no credential (a local file; access is governed by filesystem
permissions). ``duckdb`` is optional (the ``duckdb`` extra): this module imports
without it, ``builtin_registry()`` skips the source when it is missing, and
direct use yields a typed ``UNSUPPORTED`` naming the extra. All database access
runs on a worker thread (duckdb is blocking).
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qs, unquote

from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.connectors._sql_query import (
    build_query_result,
    resolve_row_cap,
    resolve_statement,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.result import Result, error, from_exception
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace for DuckDB descriptive metadata in ``source_extra``.
SOURCE_NAMESPACE = "duckdb"

# Whether the optional duckdb client is importable (the ``duckdb`` extra).
DUCKDB_AVAILABLE = importlib.util.find_spec("duckdb") is not None

_SCHEME = "duckdb://"


class _DuckDBSpec:
    """Parsed ``duckdb://`` routing decision (db path + statement inputs)."""

    def __init__(
        self,
        path: str,
        table: Optional[str],
        query: Optional[str],
        query_env: Optional[str],
        limit: Optional[str],
    ) -> None:
        self.path = path
        self.table = table
        self.query = query
        self.query_env = query_env
        self.limit = limit


def _uri_to_path(raw: str) -> str:
    """Resolve the ``duckdb://`` path part to a filesystem path.

    Handles the file-URI slash forms uniformly (the pattern is checked, not the
    OS, so behaviour is identical everywhere), mirroring the ``sqlite://``
    connector:

    - ``/C:/data.duckdb`` -> ``C:/data.duckdb`` (Windows drive: drop the slash);
    - ``//abs/path`` -> ``/abs/path``;
    - ``/abs/path`` and ``relative.duckdb`` -> unchanged.
    """
    path = unquote(raw)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        return path[1:]
    if path.startswith("//"):
        return "/" + path.lstrip("/")
    return path


def _parse_uri(uri: str) -> _DuckDBSpec:
    """Parse a ``duckdb://`` URI into a spec, raising ``ValueError`` when bad."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a duckdb:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    path_part, _, query = remainder.partition("?")
    path = _uri_to_path(path_part)
    if not path:
        raise ValueError(f"duckdb:// URI carries no database path: {uri}")

    params = parse_qs(query)
    return _DuckDBSpec(
        path=path,
        table=params.get("table", [None])[0],
        query=params.get("query", [None])[0],
        query_env=params.get("query_env", [None])[0],
        limit=params.get("limit", [None])[0],
    )


def _map_duckdb_error(exc: Exception) -> ErrorKind:
    """Map a ``duckdb`` exception onto the v1 error taxonomy (D10)."""
    message = str(exc).lower()
    if "read-only" in message or "read only" in message:
        # A write refused by the read_only connection.
        return ErrorKind.PERMISSION_DENIED
    if "does not exist" in message or "catalog error" in message or "no such" in message:
        return ErrorKind.NOT_FOUND
    if "cannot open" in message or "unable to open" in message or "io error" in message:
        return ErrorKind.NOT_FOUND
    # Parser / binder / syntax / conversion errors: the caller's SQL is bad.
    return ErrorKind.INVALID_INPUT


class DuckDBQueryConnector(BaseFetcher):
    """
    Canonical v1 connector for read queries against a DuckDB file
    ===============================================
    Opens a local DuckDB database read-only and runs a SELECT (or a table
    browse), emitting one ``kind="query_result"`` node with a single ``Table``
    atom. Descriptive fields live in ``source_extra["duckdb"]``; the atom
    carries content only.
    ===============================================
    NOTE:
        1. Implements only ``stream()``; ``fetch()`` is inherited and collects
           the bounded one-item stream into a single ``Result``.
        2. Read-only is enforced by opening with ``read_only=True`` -- a write
           fails with the engine's refusal, mapped to ``PERMISSION_DENIED``,
           and the file is left unchanged.
        3. ``duckdb`` is optional (the ``duckdb`` extra); without it the
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
        Stream the canonical result for one DuckDB read query

        Yields exactly one ``Result``: a ``Success`` with a ``Table``-bearing
        ``query_result`` node, a ``Partial`` when the row cap truncated the
        result, or a typed ``Error`` (unsupported extra, missing file, bad SQL,
        a rejected write).

        NOTE:
            1. ``auth`` and ``zoom`` are accepted for protocol conformance; a
               local DuckDB file needs no credential and a table has one natural
               granularity.

        Parameters
        ----------
            uri:
                The ``duckdb://`` source URI.
            auth:
                Unused; accepted for protocol conformance.
            zoom:
                Unused; accepted for protocol conformance.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        del auth, zoom

        if not DUCKDB_AVAILABLE:
            yield error(
                ErrorKind.UNSUPPORTED,
                message=(
                    "duckdb is not installed; install the 'duckdb' extra "
                    '(pip install "omni-fetcher[duckdb]") to query duckdb://'
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

        # Distinguish a missing file (NOT_FOUND) from an unreadable one
        # (PERMISSION_DENIED) before duckdb blurs both into an IO error.
        if not os.path.exists(spec.path):
            yield error(
                ErrorKind.NOT_FOUND, message=f"database not found: {spec.path}", locator=uri
            )
            return
        if not os.access(spec.path, os.R_OK):
            yield error(
                ErrorKind.PERMISSION_DENIED,
                message=f"database not readable: {spec.path}",
                locator=uri,
            )
            return

        try:
            columns, rows = await asyncio.to_thread(self._run_query, spec.path, sql, row_cap)
        except Exception as exc:  # noqa: BLE001 - mapped onto the typed taxonomy
            yield from_exception(exc, kind=_map_duckdb_error(exc), locator=uri)
            return

        yield build_query_result(
            uri,
            SOURCE_NAMESPACE,
            columns,
            rows,
            row_cap=row_cap,
            extra_fields={"database": spec.path},
        )

    @staticmethod
    def _run_query(path: str, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        """Open the database read-only and fetch up to ``row_cap + 1`` rows.

        Runs on a worker thread (duckdb is blocking). Read-only is enforced by
        the ``read_only=True`` connection; a write raises a duckdb error,
        surfaced as ``PERMISSION_DENIED``, and the file is left unchanged.
        """
        import duckdb

        connection = duckdb.connect(database=path, read_only=True)
        try:
            cursor = connection.execute(sql)
            fetched = cursor.fetchmany(row_cap + 1)
            columns = [description[0] for description in (cursor.description or [])]
            rows = [list(row) for row in fetched]
            return columns, rows
        finally:
            connection.close()

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
                ``True`` for a ``duckdb://`` URI.
        """
        return uri.startswith(_SCHEME)
