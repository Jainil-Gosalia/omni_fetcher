"""Tests for authentication system."""

import pytest
import os
from unittest.mock import patch

from omni_fetcher.auth import AuthConfig, load_auth_from_env
from omni_fetcher import OmniFetcher
from omni_fetcher.fetchers.base import BaseFetcher


class TestAuthConfig:
    def test_bearer_token(self):
        """Bearer token auth."""
        config = AuthConfig(type="bearer", token="my-token")
        headers = config.get_headers()
        assert headers["Authorization"] == "Bearer my-token"

    def test_bearer_token_from_env(self):
        """Bearer token from env var."""
        with patch.dict(os.environ, {"MY_TOKEN": "env-token"}):
            config = AuthConfig(type="bearer", token_env="MY_TOKEN")
            token = config.get_token()
            assert token == "env-token"

    def test_api_key(self):
        """API key auth."""
        config = AuthConfig(type="api_key", api_key="my-key", api_key_header="X-Custom-Key")
        headers = config.get_headers()
        assert headers["X-Custom-Key"] == "my-key"

    def test_basic_auth(self):
        """Basic auth."""
        config = AuthConfig(type="basic", username="user", password="pass")
        headers = config.get_headers()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")

    def test_aws_credentials(self):
        """AWS credentials."""
        config = AuthConfig(
            type="aws",
            aws_access_key_id="AKIA123",
            aws_secret_access_key="secret123",
            aws_region="us-west-2",
        )
        creds = config.get_aws_credentials()
        assert creds["aws_access_key_id"] == "AKIA123"
        assert creds["region_name"] == "us-west-2"

    def test_mask_sensitive_in_to_dict(self):
        """Sensitive values are masked in to_dict."""
        config = AuthConfig(type="bearer", token="my-secret-token-12345")
        d = config.to_dict()

        # Token should be masked
        assert "****" in d["token"]
        assert "my-s" in d["token"]  # First 4 chars visible


class TestOmniFetcherAuth:
    def test_auth_via_constructor(self):
        """Auth via constructor."""
        fetcher = OmniFetcher(auth={"github": {"type": "bearer", "token": "test-token"}})

        auth = fetcher.get_auth("github")
        assert auth is not None
        assert auth.type == "bearer"

    def test_set_auth_method(self):
        """set_auth method."""
        fetcher = OmniFetcher()
        fetcher.set_auth("custom", {"type": "api_key", "api_key": "my-key"})

        auth = fetcher.get_auth("custom")
        assert auth is not None
        assert auth.type == "api_key"

    def test_get_auth_not_found(self):
        """get_auth returns None for unknown source."""
        fetcher = OmniFetcher()
        auth = fetcher.get_auth("nonexistent")
        assert auth is None

    def test_auth_in_source_info(self):
        """Auth config stored in source info."""

        fetcher = OmniFetcher()
        info = fetcher.get_source_info("http_auth")
        assert info is not None


class TestBaseFetcherAuth:
    def test_set_auth_on_fetcher(self):
        """set_auth on base fetcher."""

        class TestFetcher(BaseFetcher):
            name = "test"

            async def fetch(self, uri, **kwargs):
                pass

        fetcher = TestFetcher()
        config = AuthConfig(type="bearer", token="test")
        fetcher.set_auth(config)

        assert fetcher._auth_config is not None
        headers = fetcher.get_auth_headers()
        assert "Authorization" in headers

    def test_get_auth_headers(self):
        """get_auth_headers method."""

        class TestFetcher(BaseFetcher):
            name = "test"

            async def fetch(self, uri, **kwargs):
                pass

        fetcher = TestFetcher()
        headers = fetcher.get_auth_headers()
        assert headers == {}

    def test_get_aws_credentials(self):
        """get_aws_credentials method."""

        class TestFetcher(BaseFetcher):
            name = "test"

            async def fetch(self, uri, **kwargs):
                pass

        fetcher = TestFetcher()
        creds = fetcher.get_auth_aws_credentials()
        assert creds == {}


class TestOAuth2:
    def test_oauth2_credentials_direct(self):
        """OAuth2 credentials from direct values."""
        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="my-client-id",
            oauth2_client_secret="my-secret",
            oauth2_token_url="https://auth.example.com/token",
        )
        creds = config.get_oauth2_credentials()
        assert creds["client_id"] == "my-client-id"
        assert creds["client_secret"] == "my-secret"

    def test_oauth2_credentials_from_env(self):
        """OAuth2 credentials from env vars."""
        with patch.dict(
            os.environ, {"OAUTH2_CLIENT_ID": "env-client-id", "OAUTH2_CLIENT_SECRET": "env-secret"}
        ):
            config = AuthConfig(
                type="oauth2",
                oauth2_client_id_env="OAUTH2_CLIENT_ID",
                oauth2_client_secret_env="OAUTH2_CLIENT_SECRET",
                oauth2_token_url="https://auth.example.com/token",
            )
            creds = config.get_oauth2_credentials()
            assert creds["client_id"] == "env-client-id"
            assert creds["client_secret"] == "env-secret"

    def test_oauth2_get_token_url(self):
        """OAuth2 token URL getter."""
        config = AuthConfig(type="oauth2", oauth2_token_url="https://auth.example.com/token")
        assert config.get_oauth2_token_url() == "https://auth.example.com/token"

    def test_oauth2_get_headers_with_access_token(self):
        """OAuth2 headers with access token."""
        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="my-client-id",
            oauth2_client_secret="my-secret",
            oauth2_token_url="https://auth.example.com/token",
        )
        config.set_oauth2_token("my-access-token")
        headers = config.get_headers()
        assert headers["Authorization"] == "Bearer my-access-token"

    def test_oauth2_get_headers_no_token(self):
        """OAuth2 headers without access token."""
        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="my-client-id",
            oauth2_client_secret="my-secret",
            oauth2_token_url="https://auth.example.com/token",
        )
        headers = config.get_headers()
        assert "Authorization" not in headers

    def test_oauth2_set_and_get_access_token(self):
        """OAuth2 set and get access token."""
        config = AuthConfig(type="oauth2")
        config.set_oauth2_token("test-token", expires_in=3600)
        assert config.get_oauth2_access_token() == "test-token"
        assert config.oauth2_token_expiry is not None

    def test_oauth2_is_token_valid(self):
        """OAuth2 token validity check."""
        config = AuthConfig(type="oauth2")
        assert not config.is_oauth2_token_valid()

        config.set_oauth2_token("test-token", expires_in=3600)
        assert config.is_oauth2_token_valid()

    def test_oauth2_refresh_token_from_env(self):
        """OAuth2 refresh token from env var."""
        with patch.dict(os.environ, {"MY_REFRESH_TOKEN": "env-refresh-token"}):
            config = AuthConfig(
                type="oauth2",
                oauth2_grant_type="refresh_token",
                oauth2_refresh_token_env="MY_REFRESH_TOKEN",
            )
            token = config.get_oauth2_refresh_token()
            assert token == "env-refresh-token"

    def test_oauth2_mask_secret_in_to_dict(self):
        """OAuth2 client secret is masked in to_dict."""
        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="my-client-id",
            oauth2_client_secret="my-very-secret-value123",
            oauth2_token_url="https://auth.example.com/token",
        )
        d = config.to_dict()
        assert "****" in d["oauth2_client_secret"]
        assert "my-v" in d["oauth2_client_secret"]

    @pytest.mark.asyncio
    async def test_oauth2_refresh_client_credentials(self):
        """OAuth2 token refresh with client_credentials grant."""
        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="my-client-id",
            oauth2_client_secret="my-secret",
            oauth2_token_url="https://auth.example.com/token",
            oauth2_grant_type="client_credentials",
        )

        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"access_token": "new-access-token", "expires_in": 3600}

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, data=None):
                return MockResponse()

        with patch("httpx.AsyncClient", MockAsyncClient):
            token = await config.refresh_oauth2_token()
            assert token == "new-access-token"
            assert config.get_oauth2_access_token() == "new-access-token"

    @pytest.mark.asyncio
    async def test_oauth2_refresh_missing_credentials(self):
        """OAuth2 refresh returns None without credentials."""
        config = AuthConfig(type="oauth2", oauth2_token_url="https://auth.example.com/token")
        token = await config.refresh_oauth2_token()
        assert token is None

    @pytest.mark.asyncio
    async def test_oauth2_refresh_missing_token_url(self):
        """OAuth2 refresh returns None without token URL."""
        config = AuthConfig(
            type="oauth2", oauth2_client_id="my-client-id", oauth2_client_secret="my-secret"
        )
        token = await config.refresh_oauth2_token()
        assert token is None

    @pytest.mark.asyncio
    async def test_oauth2_refresh_with_refresh_token_grant(self):
        """OAuth2 token refresh with refresh_token grant."""
        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="my-client-id",
            oauth2_client_secret="my-secret",
            oauth2_token_url="https://auth.example.com/token",
            oauth2_grant_type="refresh_token",
            oauth2_refresh_token="my-refresh-token",
        )

        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"access_token": "new-access-token-from-refresh", "expires_in": 7200}

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, data=None):
                return MockResponse()

        with patch("httpx.AsyncClient", MockAsyncClient):
            token = await config.refresh_oauth2_token()
            assert token == "new-access-token-from-refresh"
            assert config.get_oauth2_access_token() == "new-access-token-from-refresh"


class TestLoadAuthFromEnvOAuth2:
    def test_load_oauth2_from_env(self):
        """Load OAuth2 config from environment variables."""
        env = {
            "OMNI_MYAPI_TYPE": "oauth2",
            "OMNI_MYAPI_OAUTH2_CLIENT_ID_ENV": "MY_CLIENT_ID",
            "OMNI_MYAPI_OAUTH2_CLIENT_SECRET_ENV": "MY_CLIENT_SECRET",
            "OMNI_MYAPI_OAUTH2_TOKEN_URL": "https://auth.example.com/token",
            "OMNI_MYAPI_OAUTH2_SCOPE": "read write",
            "OMNI_MYAPI_OAUTH2_GRANT_TYPE": "client_credentials",
        }
        with patch.dict(os.environ, env, clear=True):
            auth_configs = load_auth_from_env()
            assert "myapi" in auth_configs
            config = auth_configs["myapi"]
            assert config.type == "oauth2"
            assert config.oauth2_client_id_env == "MY_CLIENT_ID"
            assert config.oauth2_client_secret_env == "MY_CLIENT_SECRET"
            assert config.oauth2_token_url == "https://auth.example.com/token"
            assert config.oauth2_scope == "read write"
            assert config.oauth2_grant_type == "client_credentials"

    def test_load_oauth2_refresh_token_from_env(self):
        """Load OAuth2 with refresh token from environment."""
        env = {
            "OMNI_MYAPI_TYPE": "oauth2",
            "OMNI_MYAPI_OAUTH2_CLIENT_ID": "my-client-id",
            "OMNI_MYAPI_OAUTH2_CLIENT_SECRET": "my-secret",
            "OMNI_MYAPI_OAUTH2_TOKEN_URL": "https://auth.example.com/token",
            "OMNI_MYAPI_OAUTH2_GRANT_TYPE": "refresh_token",
            "OMNI_MYAPI_OAUTH2_REFRESH_TOKEN_ENV": "MY_REFRESH_TOKEN",
        }
        with patch.dict(os.environ, env, clear=True):
            auth_configs = load_auth_from_env()
            config = auth_configs["myapi"]
            assert config.oauth2_grant_type == "refresh_token"
            assert config.oauth2_refresh_token_env == "MY_REFRESH_TOKEN"
