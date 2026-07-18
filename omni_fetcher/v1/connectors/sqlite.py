"""The canonical ``sqlite`` query connector for the v1 contract (v1.9).

Runs a read query against a local SQLite database file and maps the result onto
the canonical contract: one ``kind="query_result"`` node carrying a single
``Table`` atom (column names as ``headers``, result rows as ``rows``), with the
column list, row count, and truncation flag in ``source_extra["sqlite"]``. Part
of the SQL query connector family; the shared spec lives in
``omni_fetcher.v1.connectors._sql_query``.

URI: ``sqlite://<file-path>`` (``sqlite:///abs/path.db`` for an absolute path,
``sqlite://relative.db`` for a relative one), with one of:

- ``?table=<name>`` -- browse a table (``SELECT * FROM <name>`` under the row cap);
- ``?query=<url-encoded SELECT>`` -- an arbitrary read query;
- ``?query_env=<ENV_NAME>`` -- read the SQL from an environment variable.

Plus ``?limit=<n>`` to raise the row cap.

Read-only is enforced by the engine, not by parsing (v1.9 PRD, D5): the database
is opened ``mode=ro`` and ``PRAGMA query_only=ON`` is set, so any write fails
with the database's own refusal. The connector needs no credential (a local
file; access is governed by filesystem permissions) and **no optional extra** --
it uses the standard-library ``sqlite3`` on a worker thread, so ``sqlite://``
querying works on a bare install (D2c/D12).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
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

# Source namespace for SQLite descriptive metadata in ``source_extra``.
SOURCE_NAMESPACE = "sqlite"

_SCHEME = "sqlite://"


class _SQLiteSpec:
    """Parsed ``sqlite://`` routing decision (db path + statement inputs)."""

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
    """Resolve the ``sqlite://`` path part to a filesystem path.

    ``sqlite:///abs`` keeps its leading slash (an absolute POSIX path);
    ``sqlite:///C:/...`` drops the slash before a Windows drive letter;
    ``sqlite://relative`` is used as-is.
    """
    path = unquote(raw)
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path


def _parse_uri(uri: str) -> _SQLiteSpec:
    """Parse a ``sqlite://`` URI into a spec, raising ``ValueError`` when bad."""
    if not uri.startswith(_SCHEME):
        raise ValueError(f"not a sqlite:// URI: {uri}")
    remainder = uri[len(_SCHEME) :]
    path_part, _, query = remainder.partition("?")
    path = _uri_to_path(path_part)
    if not path:
        raise ValueError(f"sqlite:// URI carries no database path: {uri}")

    params = parse_qs(query)
    return _SQLiteSpec(
        path=path,
        table=params.get("table", [None])[0],
        query=params.get("query", [None])[0],
        query_env=params.get("query_env", [None])[0],
        limit=params.get("limit", [None])[0],
    )


def _map_sqlite_error(exc: sqlite3.Error) -> ErrorKind:
    """Map a ``sqlite3`` exception onto the v1 error taxonomy (D10)."""
    message = str(exc).lower()
    if "readonly" in message or "read-only" in message or "not authorized" in message:
        return ErrorKind.PERMISSION_DENIED
    if "no such table" in message or "unable to open database" in message:
        return ErrorKind.NOT_FOUND
    if "not a database" in message or "file is encrypted" in message:
        return ErrorKind.PARSE_ERROR
    # Syntax errors, unknown columns/functions, misuse: the caller's SQL is bad.
    return ErrorKind.INVALID_INPUT


class SQLiteQueryConnector(BaseFetcher):
    """
    Canonical v1 connector for read queries against a SQLite file
    ===============================================
    Opens a local SQLite database read-only and runs a SELECT (or a table
    browse), emitting one ``kind="query_result"`` node with a single ``Table``
    atom. Descriptive fields live in ``source_extra["sqlite"]``; the atom
    carries content only.
    ===============================================
    NOTE:
        1. Implements only ``stream()``; ``fetch()`` is inherited and collects
           the bounded one-item stream into a single ``Result``.
        2. Read-only is enforced by opening ``mode=ro`` and setting
           ``PRAGMA query_only=ON`` -- a write fails with the engine's refusal,
           mapped to ``PERMISSION_DENIED``.
        3. Uses the standard-library ``sqlite3`` on a worker thread; no
           credential and no optional extra.

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
        Stream the canonical result for one SQLite read query

        Yields exactly one ``Result``: a ``Success`` with a ``Table``-bearing
        ``query_result`` node, a ``Partial`` when the row cap truncated the
        result, or a typed ``Error`` (missing file, bad SQL, a rejected write).

        NOTE:
            1. ``auth`` and ``zoom`` are accepted for protocol conformance; a
               local SQLite file needs no credential and a table has one natural
               granularity.

        Parameters
        ----------
            uri:
                The ``sqlite://`` source URI.
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
        # (PERMISSION_DENIED) before sqlite blurs both into "unable to open".
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
        except sqlite3.Error as exc:
            yield from_exception(exc, kind=_map_sqlite_error(exc), locator=uri)
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

        Runs on a worker thread (sqlite3 is blocking). Read-only is enforced by
        the ``mode=ro`` open plus ``PRAGMA query_only=ON``; a write raises
        ``sqlite3.OperationalError``, surfaced as ``PERMISSION_DENIED``.
        """
        connect_uri = f"file:{path}?mode=ro"
        connection = sqlite3.connect(connect_uri, uri=True)
        try:
            connection.execute("PRAGMA query_only = ON")
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
                ``True`` for a ``sqlite://`` URI.
        """
        return uri.startswith(_SCHEME)
