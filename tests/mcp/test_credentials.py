"""Startup credential-resolution tests for the MCP server.

Covers the ``OMNI_FETCHER_<SOURCE>_<FIELD>`` convention: each auth shape is
inferred from the fields present, source/field boundaries are resolved against
known source names (so underscored names like ``google_drive`` parse), and
anything unrecognised is skipped rather than crashing the server.
"""

from __future__ import annotations

import logging

import pytest

# The MCP server lives behind the optional ``mcp`` extra. Without it,
# ``omni_fetcher.mcp`` raises ImportError by design (D12), so skip this whole
# module rather than fail collection in a core-only environment.
pytest.importorskip("mcp")

from omni_fetcher.mcp.credentials import CredentialStore, load_credentials  # noqa: E402
from omni_fetcher.v1.auth import ApiKeyAuth, AwsAuth, BasicAuth, BearerAuth, OAuth2Auth

KNOWN = frozenset({"github", "jira", "s3", "google_drive", "http_url", "stripe"})


def test_bearer_from_token() -> None:
    store = load_credentials(KNOWN, {"OMNI_FETCHER_GITHUB_TOKEN": "ghp_x"})
    cred = store.get("github")
    assert isinstance(cred, BearerAuth) and cred.token == "ghp_x"


def test_basic_from_username_password() -> None:
    store = load_credentials(
        KNOWN,
        {"OMNI_FETCHER_JIRA_USERNAME": "bob", "OMNI_FETCHER_JIRA_PASSWORD": "pw"},
    )
    cred = store.get("jira")
    assert isinstance(cred, BasicAuth) and cred.username == "bob" and cred.password == "pw"


def test_api_key_with_default_and_custom_header() -> None:
    default = load_credentials(KNOWN, {"OMNI_FETCHER_STRIPE_API_KEY": "sk"})
    assert isinstance(default.get("stripe"), ApiKeyAuth)
    assert default.get("stripe").header == "X-API-Key"

    custom = load_credentials(
        KNOWN,
        {"OMNI_FETCHER_STRIPE_API_KEY": "sk", "OMNI_FETCHER_STRIPE_HEADER": "Authorization"},
    )
    assert custom.get("stripe").header == "Authorization"


def test_oauth2_from_access_token() -> None:
    store = load_credentials(KNOWN, {"OMNI_FETCHER_GOOGLE_DRIVE_ACCESS_TOKEN": "ya29"})
    cred = store.get("google_drive")
    assert isinstance(cred, OAuth2Auth) and cred.access_token == "ya29"


def test_aws_with_optional_session_and_region() -> None:
    store = load_credentials(
        KNOWN,
        {
            "OMNI_FETCHER_S3_ACCESS_KEY_ID": "AKIA",
            "OMNI_FETCHER_S3_SECRET_ACCESS_KEY": "secret",
            "OMNI_FETCHER_S3_SESSION_TOKEN": "sess",
            "OMNI_FETCHER_S3_REGION": "us-east-1",
        },
    )
    cred = store.get("s3")
    assert isinstance(cred, AwsAuth)
    assert cred.access_key_id == "AKIA" and cred.secret_access_key == "secret"
    assert cred.session_token == "sess" and cred.region == "us-east-1"


def test_underscored_source_name_parses() -> None:
    """``google_drive`` (underscore in the source name) resolves correctly."""
    store = load_credentials(KNOWN, {"OMNI_FETCHER_GOOGLE_DRIVE_TOKEN": "t"})
    assert isinstance(store.get("google_drive"), BearerAuth)


def test_aws_is_preferred_over_a_bearer_subset() -> None:
    """A multi-field shape wins over a single-field one it could subsume."""
    store = load_credentials(
        KNOWN,
        {
            "OMNI_FETCHER_S3_ACCESS_KEY_ID": "AKIA",
            "OMNI_FETCHER_S3_SECRET_ACCESS_KEY": "secret",
        },
    )
    assert isinstance(store.get("s3"), AwsAuth)


def test_unknown_field_shape_is_skipped_not_fatal(caplog) -> None:
    """A field set matching no shape is skipped with a warning; boot survives."""
    with caplog.at_level(logging.WARNING):
        store = load_credentials(KNOWN, {"OMNI_FETCHER_GITHUB_MYSTERY": "x"})
    assert store.get("github") is None
    assert "github" in caplog.text


def test_unrelated_and_unknown_source_vars_are_ignored() -> None:
    store = load_credentials(
        KNOWN,
        {
            "PATH": "/usr/bin",
            "OMNI_FETCHER_NONSOURCE_TOKEN": "x",  # 'nonsource' not in KNOWN
        },
    )
    assert store.get("nonsource") is None


def test_env_hint_names_the_conventional_vars_without_leaking_values() -> None:
    hint = CredentialStore.env_hint("github")
    assert "OMNI_FETCHER_GITHUB_TOKEN" in hint
    assert "OMNI_FETCHER_GITHUB_ACCESS_KEY_ID" in hint


def test_has_and_get_agree() -> None:
    store = load_credentials(KNOWN, {"OMNI_FETCHER_GITHUB_TOKEN": "t"})
    assert store.has("github") and store.get("github") is not None
    assert not store.has("jira") and store.get("jira") is None
