"""External-behaviour tests for the v1 normalized auth resolver.

These tests exercise only the public contract of
``NormalizedAuthResolver`` -- the headers each canonical credential maps
onto, that resolution is stateless and never reads the ambient
environment, that credentials are never mutated, ``supports`` correctness,
and the documented ``google_service_account`` -> OAuth2 reconciliation.
"""

from __future__ import annotations

import base64

import pytest

from omni_fetcher.v1.auth import (
    ApiKeyAuth,
    AuthResolver,
    AuthType,
    AwsAuth,
    BasicAuth,
    BearerAuth,
    NormalizedAuthResolver,
    OAuth2Auth,
)


@pytest.fixture
def resolver() -> NormalizedAuthResolver:
    """Return a fresh resolver for each test."""
    return NormalizedAuthResolver()


class TestResolveHeaders:
    """Each canonical credential maps onto the correct HTTP headers."""

    def test_bearer_maps_to_authorization_bearer(self, resolver):
        headers = resolver.resolve_headers(BearerAuth(token="abc123"))
        assert headers == {"Authorization": "Bearer abc123"}

    def test_api_key_uses_default_header(self, resolver):
        headers = resolver.resolve_headers(ApiKeyAuth(api_key="k-1"))
        assert headers == {"X-API-Key": "k-1"}

    def test_api_key_uses_custom_header(self, resolver):
        cred = ApiKeyAuth(api_key="k-1", header="Authorization")
        headers = resolver.resolve_headers(cred)
        assert headers == {"Authorization": "k-1"}

    def test_basic_maps_to_base64_authorization(self, resolver):
        cred = BasicAuth(username="alice", password="s3cret")
        headers = resolver.resolve_headers(cred)
        expected = base64.b64encode(b"alice:s3cret").decode("ascii")
        assert headers == {"Authorization": f"Basic {expected}"}

    def test_basic_round_trips_to_credentials(self, resolver):
        cred = BasicAuth(username="bob", password="p@ss:word")
        headers = resolver.resolve_headers(cred)
        token = headers["Authorization"].removeprefix("Basic ")
        decoded = base64.b64decode(token).decode("utf-8")
        # The first colon separates user from password; password may
        # itself contain colons.
        username, _, password = decoded.partition(":")
        assert username == "bob"
        assert password == "p@ss:word"

    def test_oauth2_maps_to_authorization_bearer(self, resolver):
        headers = resolver.resolve_headers(OAuth2Auth(access_token="tok"))
        assert headers == {"Authorization": "Bearer tok"}

    def test_none_credential_yields_empty_headers(self, resolver):
        assert resolver.resolve_headers(None) == {}

    def test_aws_yields_no_static_header(self, resolver):
        cred = AwsAuth(access_key_id="AKIA", secret_access_key="secret")
        # AWS signing is request-specific; no static header is produced.
        assert resolver.resolve_headers(cred) == {}


class TestResolveAws:
    """AWS credential parts are surfaced for a request-specific signer."""

    def test_resolve_aws_returns_all_parts(self, resolver):
        cred = AwsAuth(
            access_key_id="AKIA",
            secret_access_key="secret",
            session_token="sess",
            region="us-east-1",
        )
        assert resolver.resolve_aws(cred) == {
            "access_key_id": "AKIA",
            "secret_access_key": "secret",
            "session_token": "sess",
            "region": "us-east-1",
        }

    def test_resolve_aws_optional_parts_default_none(self, resolver):
        cred = AwsAuth(access_key_id="AKIA", secret_access_key="secret")
        parts = resolver.resolve_aws(cred)
        assert parts["session_token"] is None
        assert parts["region"] is None


class TestNoAmbientState:
    """The resolver never reads ambient env vars or shared state."""

    def test_env_vars_do_not_change_behaviour(self, resolver, monkeypatch):
        # Set plausible ambient credentials that an env-loading resolver
        # might pick up. They must have zero effect.
        monkeypatch.setenv("AUTHORIZATION", "Bearer leaked")
        monkeypatch.setenv("API_KEY", "leaked-key")
        monkeypatch.setenv("X_API_KEY", "leaked-key")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-LEAK")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-secret")
        monkeypatch.setenv("OPENAI_API_KEY", "leaked")
        # With no credential, output is empty regardless of the env.
        assert resolver.resolve_headers(None) == {}
        # With a credential, only the injected value is reflected.
        headers = resolver.resolve_headers(BearerAuth(token="injected"))
        assert headers == {"Authorization": "Bearer injected"}

    def test_resolution_is_stateless_across_calls(self, resolver):
        first = resolver.resolve_headers(BearerAuth(token="one"))
        second = resolver.resolve_headers(BearerAuth(token="two"))
        # No carry-over: the second call is unaffected by the first.
        assert first == {"Authorization": "Bearer one"}
        assert second == {"Authorization": "Bearer two"}

    def test_returned_dict_is_not_shared(self, resolver):
        cred = BearerAuth(token="abc")
        first = resolver.resolve_headers(cred)
        second = resolver.resolve_headers(cred)
        assert first == second
        # Mutating one result must not affect a subsequent resolution.
        first["Authorization"] = "tampered"
        third = resolver.resolve_headers(cred)
        assert third == {"Authorization": "Bearer abc"}


class TestNoCredentialMutation:
    """Resolving a credential must never mutate it."""

    def test_bearer_unchanged_after_resolution(self, resolver):
        cred = BearerAuth(token="abc")
        resolver.resolve_headers(cred)
        resolver.resolve_headers(cred)
        assert cred.token == "abc"

    def test_api_key_unchanged_after_resolution(self, resolver):
        cred = ApiKeyAuth(api_key="k", header="X-Custom")
        resolver.resolve_headers(cred)
        assert cred.api_key == "k"
        assert cred.header == "X-Custom"

    def test_basic_unchanged_after_resolution(self, resolver):
        cred = BasicAuth(username="u", password="p")
        resolver.resolve_headers(cred)
        assert cred.username == "u"
        assert cred.password == "p"

    def test_aws_unchanged_after_resolution(self, resolver):
        cred = AwsAuth(access_key_id="AKIA", secret_access_key="s")
        resolver.resolve_aws(cred)
        assert cred.access_key_id == "AKIA"
        assert cred.secret_access_key == "s"

    def test_frozen_credential_rejects_mutation(self, resolver):
        # The frozen contract is what guarantees no in-place mutation.
        cred = BearerAuth(token="abc")
        with pytest.raises(Exception):
            cred.token = "mutated"


class TestSupports:
    """``supports`` reports coverage of every canonical auth type."""

    @pytest.mark.parametrize("auth_type", list(AuthType))
    def test_supports_all_canonical_types(self, resolver, auth_type):
        assert resolver.supports(auth_type) is True

    def test_supports_each_named_type(self, resolver):
        assert resolver.supports(AuthType.BEARER)
        assert resolver.supports(AuthType.API_KEY)
        assert resolver.supports(AuthType.BASIC)
        assert resolver.supports(AuthType.OAUTH2)
        assert resolver.supports(AuthType.AWS)


class TestProtocolConformance:
    """The concrete resolver conforms to the frozen protocol."""

    def test_is_auth_resolver_instance(self, resolver):
        assert isinstance(resolver, AuthResolver)


class TestGoogleServiceAccountReconciliation:
    """``google_service_account`` is reconciled onto the OAuth2 path.

    A service-account flow is the host's job: the host exchanges the
    service-account key for a short-lived OAuth2 access token and injects
    it as ``OAuth2Auth``. There is intentionally no canonical
    ``google_service_account`` auth type, and the token resolves through
    the standard OAuth2 bearer path.
    """

    def test_no_canonical_service_account_type(self):
        names = {member.name for member in AuthType}
        values = {member.value for member in AuthType}
        assert "GOOGLE_SERVICE_ACCOUNT" not in names
        assert "google_service_account" not in values

    def test_service_account_token_resolves_via_oauth2(self, resolver):
        # The host has already exchanged the service-account key for this
        # access token; OmniFetcher only ever sees the resulting token.
        host_exchanged_token = "ya29.sa-exchanged-token"
        cred = OAuth2Auth(access_token=host_exchanged_token)
        headers = resolver.resolve_headers(cred)
        assert headers == {
            "Authorization": f"Bearer {host_exchanged_token}"
        }
