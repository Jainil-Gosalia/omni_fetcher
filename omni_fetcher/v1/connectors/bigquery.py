"""The canonical ``bigquery`` query connector for the v1 contract (v1.13).

Runs a read query against Google BigQuery and maps the result onto the canonical
contract: one ``kind="query_result"`` node carrying a single ``Table`` atom
(column names as ``headers``, result rows as ``rows``), with the column list,
row count, and truncation flag in ``source_extra["bigquery"]``. The
serverless-warehouse member of the SQL query family; the shared spec lives in
``omni_fetcher.v1.connectors._sql_query``.

URI: ``bigquery://<project>/<dataset>`` with one of:

- ``?table=<name>`` -- browse ``project.dataset.<name>`` (``SELECT *`` under the
  row cap);
- ``?query=<url-encoded SELECT>`` -- an arbitrary read query;
- ``?query_env=<ENV_NAME>`` -- read the SQL from an environment variable.

Plus ``?limit=<n>`` to raise the row cap. BigQuery quotes identifiers with
backticks and addresses tables with three parts (``project.dataset.table``) --
the first three-part customer of the shared ``build_select_star``.

Read-only is enforced by the engine, not by parsing (v1.9 PRD, D5): before
executing, the connector runs the statement as a **dry run**, and BigQuery's own
parser reports its ``statement_type``. Anything other than ``SELECT`` (an
``INSERT`` / ``UPDATE`` / ``DELETE`` / ``MERGE`` / DDL / multi-statement
``SCRIPT``) is refused with ``PERMISSION_DENIED`` before any data is touched; a
malformed query is rejected by the dry run itself as ``INVALID_INPUT``.

Credentials are supplied *per call* as an ``OAuth2Auth`` access token (per
PHILOSOPHY §7 a service-account key is a host-side token-exchange concern), so
the MCP server injects ``OMNI_FETCHER_BIGQUERY_ACCESS_TOKEN``; this connector
never reads ambient credentials or Application Default Credentials. The
``google-cloud-bigquery`` client is optional (the ``bigquery`` extra): this
module imports without it, ``builtin_registry()`` skips the source when it is
missing, and direct use yields a typed ``UNSUPPORTED``. All client construction
flows through the ``_build_client`` seam so tests script a fake.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any, AsyncIterator, Optional, Protocol
from urllib.parse import parse_qs

from omni_fetcher.v1.auth import AuthCredential, OAuth2Auth
from omni_fetcher.v1.connectors._sql_query import (
    MYSQL_QUOTE,
    build_query_result,
    parse_sql_uri,
    resolve_row_cap,
    resolve_statement,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.result import Result, error, from_exception
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace for BigQuery descriptive metadata in ``source_extra``.
SOURCE_NAMESPACE = "bigquery"

# Whether the optional google-cloud-bigquery client is importable (``bigquery`` extra).
BIGQUERY_AVAILABLE = importlib.util.find_spec("google.cloud.bigquery") is not None

_SCHEME = "bigquery://"

# The only statement type this read-only connector will execute.
_READ_ONLY_STATEMENT = "SELECT"


class _NonReadOnlyStatement(Exception):
    """Raised when BigQuery's dry run classifies the statement as not a SELECT."""

    def __init__(self, statement_type: Optional[str]) -> None:
        super().__init__(f"statement is not read-only (statement_type={statement_type!r})")
        self.statement_type = statement_type


class _QueryExecutor(Protocol):
    """The narrow DB seam: run one read query, return columns and rows."""

    async def run(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        """Dry-run-gate then run ``sql``; return ``(columns, rows)`` (up to cap+1)."""
        ...


def _classify_google_error(exc: BaseException) -> ErrorKind:
    """Map a google-cloud/api-core exception onto the taxonomy by its HTTP status."""
    code = getattr(exc, "code", None)
    status = code if isinstance(code, int) else None
    if status == 404:
        return ErrorKind.NOT_FOUND
    if status == 403:
        return ErrorKind.PERMISSION_DENIED
    if status == 401:
        return ErrorKind.AUTH_FAILED
    if status == 400:
        # A bad query is rejected by BigQuery's parser as a 400.
        return ErrorKind.INVALID_INPUT
    if status == 429:
        return ErrorKind.RATE_LIMITED
    return ErrorKind.TRANSIENT


def _endpoint_of(uri: str) -> Optional[str]:
    """Extract an optional ``?endpoint=`` override from the URI query string."""
    query = uri.partition("?")[2]
    return parse_qs(query).get("endpoint", [None])[0]


def _build_client(project: str, access_token: str, endpoint: Optional[str] = None) -> Any:
    """Build a google-cloud-bigquery client from a per-call access token.

    The heavy import is deferred to here so the module imports on a base
    install; the client is built from the injected token only. ``endpoint``
    overrides the API endpoint for a compatible or local service (an emulator).
    """
    from google.api_core.client_options import ClientOptions
    from google.cloud import bigquery
    from google.oauth2.credentials import Credentials

    credentials = Credentials(token=access_token)
    client_options = ClientOptions(api_endpoint=endpoint) if endpoint else None
    return bigquery.Client(
        project=project, credentials=credentials, client_options=client_options
    )


class _BigQueryExecutor:
    """Production executor: dry-run gate then run a query via google-cloud-bigquery."""

    def __init__(self, project: str, access_token: str, endpoint: Optional[str] = None) -> None:
        self._project = project
        self._access_token = access_token
        self._endpoint = endpoint

    async def run(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        # google-cloud-bigquery is synchronous; run it on a worker thread.
        import asyncio

        return await asyncio.to_thread(self._run_sync, sql, row_cap)

    def _run_sync(self, sql: str, row_cap: int) -> tuple[list[str], list[list[Any]]]:
        from google.cloud import bigquery

        client = _build_client(self._project, self._access_token, self._endpoint)

        # Read-only gate: BigQuery's own parser classifies the statement.
        dry_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        dry_job = client.query(sql, job_config=dry_config)
        if dry_job.statement_type != _READ_ONLY_STATEMENT:
            raise _NonReadOnlyStatement(dry_job.statement_type)

        job = client.query(sql)
        rows_iter = job.result(max_results=row_cap + 1)
        columns = [field.name for field in rows_iter.schema]
        rows = [list(row.values()) for row in rows_iter]
        return columns, rows


class BigQueryConnector(BaseFetcher):
    """
    Canonical v1 connector for read queries against Google BigQuery
    ===============================================
    Runs a SELECT (or a table browse) after a dry-run statement-type gate and
    emits one ``kind="query_result"`` node with a single ``Table`` atom.
    Descriptive fields live in ``source_extra["bigquery"]``; the atom carries
    content only.
    ===============================================
    NOTE:
        1. Implements only ``stream()``; ``fetch()`` is inherited and collects
           the bounded one-item stream into a single ``Result``.
        2. Read-only is enforced by a dry run: BigQuery reports the
           ``statement_type``, and anything but ``SELECT`` is refused with
           ``PERMISSION_DENIED`` before execution.
        3. ``google-cloud-bigquery`` is optional (the ``bigquery`` extra);
           without it the connector yields a typed ``UNSUPPORTED``.

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
        Stream the canonical result for one BigQuery read query

        Yields exactly one ``Result``: a ``Success`` with a ``Table``-bearing
        ``query_result`` node, a ``Partial`` when the row cap truncated the
        result, or a typed ``Error`` (unsupported extra, bad input, missing auth,
        a refused non-SELECT, a missing table).

        NOTE:
            1. ``zoom`` is accepted for protocol conformance; a query result has
               one natural granularity (a table).
            2. Credentials come from ``auth`` (an ``OAuth2Auth`` access token);
               there is no URI credential fallback for BigQuery.

        Parameters
        ----------
            uri:
                The ``bigquery://project/dataset`` source URI.
            auth:
                The per-call ``OAuth2Auth`` credential.
            zoom:
                Unused; accepted for protocol conformance.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        del zoom

        if not BIGQUERY_AVAILABLE:
            yield error(
                ErrorKind.UNSUPPORTED,
                message=(
                    "google-cloud-bigquery is not installed; install the 'bigquery' extra "
                    '(pip install "omni-fetcher[bigquery]") to query bigquery://'
                ),
                locator=uri,
            )
            return

        if not isinstance(auth, OAuth2Auth):
            yield error(
                ErrorKind.AUTH_FAILED,
                message="bigquery requires a per-call OAuth2Auth access token",
                locator=uri,
            )
            return

        try:
            spec = parse_sql_uri(uri, scheme=_SCHEME, default_port=0)
            row_cap = resolve_row_cap(spec.limit)
            table_ref = f"{spec.host}.{spec.database}.{spec.table}" if spec.table else None
            sql = resolve_statement(
                table_ref=table_ref,
                query=spec.query,
                query_env=spec.query_env,
                environ=os.environ,
                row_cap=row_cap,
                quote=MYSQL_QUOTE,
                max_parts=3,
            )
        except ValueError as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return

        executor = self._make_executor(spec.host, auth.access_token, _endpoint_of(uri))

        try:
            columns, rows = await executor.run(sql, row_cap)
        except _NonReadOnlyStatement as exc:
            yield error(
                ErrorKind.PERMISSION_DENIED,
                message=(f"refused: only SELECT is allowed, got {exc.statement_type or 'a write'}"),
                locator=uri,
            )
            return
        except Exception as exc:  # noqa: BLE001 - mapped onto the typed taxonomy
            yield from_exception(exc, kind=_classify_google_error(exc), locator=uri)
            return

        yield build_query_result(
            uri,
            SOURCE_NAMESPACE,
            columns,
            rows,
            row_cap=row_cap,
            extra_fields={"project": spec.host, "dataset": spec.database},
        )

    def _make_executor(
        self, project: str, access_token: str, endpoint: Optional[str] = None
    ) -> _QueryExecutor:
        """Build the DB executor (the test seam). Overridden by fakes in tests."""
        return _BigQueryExecutor(project, access_token, endpoint)

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
                ``True`` for a ``bigquery://`` URI.
        """
        return uri.startswith(_SCHEME)
