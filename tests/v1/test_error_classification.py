"""Cross-connector error-classification audit (issue 011).

Pins the canonical status -> ErrorKind table (documented in
``omni_fetcher.v1.errors``) against every API-based connector's own
classifier, so retryable conditions (429 -> RATE_LIMITED, 5xx -> TRANSIENT)
are classified identically wherever they occur -- which is what makes
``fetch_with_retry``'s defaults meaningful.
"""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from omni_fetcher.v1.connectors import confluence as confluence_mod
from omni_fetcher.v1.connectors import github as github_mod
from omni_fetcher.v1.connectors import google_drive as gdrive_mod
from omni_fetcher.v1.connectors import http_auth as http_auth_mod
from omni_fetcher.v1.connectors import http_json as http_json_mod
from omni_fetcher.v1.connectors import http_url as http_url_mod
from omni_fetcher.v1.connectors import jira as jira_mod
from omni_fetcher.v1.connectors import notion as notion_mod
from omni_fetcher.v1.connectors import s3 as s3_mod
from omni_fetcher.v1.connectors import sharepoint as sharepoint_mod
from omni_fetcher.v1.connectors import slack as slack_mod
from omni_fetcher.v1.connectors.graphql import GraphQLConnector
from omni_fetcher.v1.connectors.linear import LinearConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error

# Every connector-level "int status -> ErrorKind" classifier.
_STATUS_CLASSIFIERS: dict[str, Callable[[int], ErrorKind]] = {
    "http_url": http_url_mod._classify_status,
    "http_auth": http_auth_mod._classify_status,
    "sharepoint": sharepoint_mod._classify_status,
    "http_json": http_json_mod._status_to_error_kind,
    "notion": notion_mod._status_to_error_kind,
    "slack": slack_mod._status_to_error_kind,
    "confluence": confluence_mod._status_to_error_kind,
    "jira": jira_mod._kind_for_status,
    "google_drive": gdrive_mod._status_error_kind,
    "github": lambda status: github_mod._status_to_error_kind(httpx.Response(status_code=status)),
}

# The canonical rows every classifier must agree on.
_CANONICAL_ROWS: list[tuple[int, ErrorKind]] = [
    (401, ErrorKind.AUTH_FAILED),
    (403, ErrorKind.PERMISSION_DENIED),
    (404, ErrorKind.NOT_FOUND),
    (429, ErrorKind.RATE_LIMITED),
    (500, ErrorKind.TRANSIENT),
    (503, ErrorKind.TRANSIENT),
]


@pytest.mark.parametrize("name", sorted(_STATUS_CLASSIFIERS))
@pytest.mark.parametrize(("status", "expected"), _CANONICAL_ROWS)
def test_status_classifiers_agree_on_the_canonical_table(
    name: str, status: int, expected: ErrorKind
) -> None:
    """Each connector's status classifier matches the documented table."""
    classify = _STATUS_CLASSIFIERS[name]

    assert classify(status) is expected, f"{name} misclassifies HTTP {status}"


@pytest.mark.parametrize(("status", "expected"), _CANONICAL_ROWS)
def test_graphql_and_linear_response_classifiers_agree(status: int, expected: ErrorKind) -> None:
    """The Response-shaped classifiers produce the same kinds."""
    response = httpx.Response(status_code=status)

    for connector in (GraphQLConnector, LinearConnector):
        result = connector._status_error(response, "mem://x")
        assert isinstance(result, Error)
        assert result.kind is expected, f"{connector.__name__} on {status}"


def test_s3_throttle_and_auth_code_sets_cover_the_canonical_rows() -> None:
    """The botocore code sets classify throttling and auth like the table."""
    assert {"429", "503", "Throttling", "SlowDown"} <= s3_mod._RATE_LIMITED_CODES
    assert "401" in s3_mod._AUTH_CODES


def test_jira_status_extraction_reads_nested_and_flat_shapes() -> None:
    """The exception-based connectors read status from both common shapes."""

    class _Response:
        status_code = 429

    nested = Exception("throttled")
    nested.response = _Response()  # type: ignore[attr-defined]
    flat = Exception("throttled")
    flat.status_code = 503  # type: ignore[attr-defined]

    assert jira_mod._status_of(nested) == 429
    assert jira_mod._status_of(flat) == 503
    assert jira_mod._kind_for_status(429) is ErrorKind.RATE_LIMITED
