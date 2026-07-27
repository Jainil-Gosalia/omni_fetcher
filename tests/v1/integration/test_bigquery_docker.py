"""Real integration test for the v1 ``bigquery`` connector against the emulator.

Drives ``BigQueryConnector`` through its real ``google-cloud-bigquery`` path
against the ``ghcr.io/goccy/bigquery-emulator``, addressed via the connector's
``?endpoint=`` option: a ``SELECT`` is planned (dry run), executed, and folded
into a ``Table`` with the right rows. Skipped unless the emulator is reachable
at ``$OMNI_TEST_BQ_ENDPOINT`` (default ``http://localhost:9050``).

The read-only *refusal* of a non-SELECT is **not** exercised here: this emulator
reports ``statement_type='SELECT'`` for DELETE/INSERT/UPDATE dry runs (it does
not replicate BigQuery's statement classification), so the gate's refusal branch
stays seam-verified in ``tests/v1/test_connector_bigquery.py`` (real BigQuery
classifies DML correctly).

The ``?table=`` browse path is exercised against the emulator's **hyphenated**
project (``test-project``), which regression-guards the fix that let BigQuery
identifiers contain hyphens (``BIGQUERY_IDENTIFIER``).

Spin one up with Docker:

    docker run -d --name omni-bq -p 9050:9050 \
        ghcr.io/goccy/bigquery-emulator:latest --project=test-project --dataset=test-dataset
"""

from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import quote

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.auth import OAuth2Auth
from omni_fetcher.v1.connectors.bigquery import BigQueryConnector
from omni_fetcher.v1.result import Success

pytest.importorskip("google.cloud.bigquery", reason="the 'bigquery' extra is not installed")
from google.api_core.client_options import ClientOptions  # noqa: E402
from google.auth.credentials import AnonymousCredentials  # noqa: E402
from google.cloud import bigquery  # noqa: E402

_ENDPOINT = os.environ.get("OMNI_TEST_BQ_ENDPOINT", "http://localhost:9050")
_PROJECT = "test-project"
_DATASET = "test-dataset"
_TABLE = f"items_{int(time.time())}"
_FQTN = f"`{_PROJECT}`.`{_DATASET}`.`{_TABLE}`"
_AUTH = OAuth2Auth(access_token="emulator")


async def _first(stream, timeout=25):
    """Return the first ``Result`` from a bounded stream, guarded by a timeout."""
    return await asyncio.wait_for(stream.__aiter__().__anext__(), timeout)


def _uri(query: str) -> str:
    return (
        f"bigquery://{_PROJECT}/{_DATASET}"
        f"?query={quote(query)}&endpoint={quote(_ENDPOINT, safe='')}"
    )


@pytest.fixture(scope="module")
def seeded():
    """Create a table + two rows in the emulator once; skip if unreachable.

    Module-scoped and time-bounded: the DDL/DML jobs are given an explicit
    result timeout so a wedged emulator surfaces as a skip rather than hanging.
    """
    try:
        client = bigquery.Client(
            project=_PROJECT,
            credentials=AnonymousCredentials(),
            client_options=ClientOptions(api_endpoint=_ENDPOINT),
        )
        client.query(f"CREATE TABLE {_FQTN} (id INT64, name STRING)").result(timeout=20)
        client.query(f"INSERT INTO {_FQTN} (id, name) VALUES (1, 'one'), (2, 'two')").result(
            timeout=20
        )
    except Exception as exc:  # noqa: BLE001 - any failure = emulator not usable
        pytest.skip(f"BigQuery emulator not usable at {_ENDPOINT}: {exc}")
    yield


async def test_select_query(seeded):
    result = await _first(
        BigQueryConnector().stream(_uri(f"SELECT id, name FROM {_FQTN} ORDER BY id"), auth=_AUTH)
    )

    assert isinstance(result, Success), result
    node = result.tree
    assert node.metadata.kind == "query_result"
    atoms = list(node.iter_atoms())
    assert atoms[0].kind == AtomKind.TABLE
    assert atoms[0].headers == ["id", "name"]
    assert atoms[0].rows == [[1, "one"], [2, "two"]]


async def test_table_browse_against_hyphenated_project(seeded):
    # `?table=` browses `test-project.test-dataset.<table>` -- the project is
    # hyphenated, which would have been rejected before the BIGQUERY_IDENTIFIER
    # fix. The connector builds the backtick-quoted three-part SELECT itself.
    uri = f"bigquery://{_PROJECT}/{_DATASET}?table={_TABLE}&endpoint={quote(_ENDPOINT, safe='')}"

    result = await _first(BigQueryConnector().stream(uri, auth=_AUTH))

    assert isinstance(result, Success), result
    atoms = list(result.tree.iter_atoms())
    assert atoms[0].kind == AtomKind.TABLE
    assert atoms[0].headers == ["id", "name"]
    assert sorted(atoms[0].rows) == [[1, "one"], [2, "two"]]
