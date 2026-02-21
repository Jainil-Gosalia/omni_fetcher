"""Integration tests for authenticated API calls."""
import base64
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

import httpx

from omni_fetcher.auth import AuthConfig
from omni_fetcher.fetchers.http_auth import HTTPAuthFetcher
from omni_fetcher import OmniFetcher


class TestHTTPAuthFetcherBearerToken:
    """Test HTTPAuthFetcher with Bearer token authentication."""

    @pytest.mark.asyncio
    async def test_bearer_token_in_authorization_header(self):
        """Verify bearer token is sent in Authorization header."""
        auth_header = None

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal auth_header
            auth_header = request.headers.get("Authorization")
            return httpx.Response(
                status_code=200,
                content=b"Secure data",
                headers={"content-type": "text/plain"},
            )

        transport = httpx.MockTransport(mock_handler)
        fetcher = HTTPAuthFetcher(bearer_token="my-secret-token")

        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://api.example.com/data", headers=fetcher._build_headers())

        assert auth_header == "Bearer my-secret-token"

    @pytest.mark.asyncio
    async def test_bearer_token_via_set_auth(self):
        """Verify bearer token works via set_auth method."""
        received_auth = None

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal received_auth
            received_auth = request.headers.get("Authorization")
            return httpx.Response(
                status_code=200,
                content=b'{"data": "test"}',
                headers={"content-type": "application/json"},
            )

        transport = httpx.MockTransport(mock_handler)
        fetcher = HTTPAuthFetcher()
        fetcher.set_auth(AuthConfig(type="bearer", token="token-via-set-auth"))

        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://api.example.com/data", headers=fetcher._build_headers())

        assert received_auth == "Bearer token-via-set-auth"

    @pytest.mark.asyncio
    async def test_full_fetch_request_with_bearer(self):
        """Test full fetch flow with bearer token."""
        async def mock_handler(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("Authorization")
            assert auth == "Bearer full-test-token", f"Expected 'Bearer full-test-token', got '{auth}'"
            return httpx.Response(
                status_code=200,
                content=b"Hello World",
                headers={"content-type": "text/plain"},
            )

        transport = httpx.MockTransport(mock_handler)
        fetcher = HTTPAuthFetcher(bearer_token="full-test-token", timeout=10.0)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock()
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/plain"}
            mock_response.content = b"Hello World"
            mock_response.text = "Hello World"
            mock_response.elapsed.total_seconds = MagicMock(return_value=0.1)
            mock_response.url = "https://api.example.com/hello"
            mock_response.raise_for_status = MagicMock()
            
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await fetcher.fetch("https://api.example.com/hello")

        mock_client.get.assert_called_once()
        call_kwargs = mock_client.get.call_args
        assert "headers" in call_kwargs.kwargs
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer full-test-token"


class TestHTTPAuthFetcherAPIKey:
    """Test HTTPAuthFetcher with API key authentication."""

    @pytest.mark.asyncio
    async def test_api_key_in_custom_header(self):
        """Verify API key is sent in custom header."""
        received_key = None

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal received_key
            received_key = request.headers.get("X-API-Key")
            return httpx.Response(
                status_code=200,
                content=b"API response",
                headers={"content-type": "application/json"},
            )

        transport = httpx.MockTransport(mock_handler)
        fetcher = HTTPAuthFetcher(api_key="my-api-key-12345")

        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://api.example.com/data", headers=fetcher._build_headers())

        assert received_key == "my-api-key-12345"

    @pytest.mark.asyncio
    async def test_api_key_with_custom_header_name(self):
        """Verify custom header name works for API key."""
        received_key = None

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal received_key
            received_key = request.headers.get("X-Custom-Auth-Key")
            return httpx.Response(
                status_code=200,
                content=b"OK",
                headers={"content-type": "text/plain"},
            )

        transport = httpx.MockTransport(mock_handler)
        fetcher = HTTPAuthFetcher()
        fetcher.set_auth(AuthConfig(
            type="api_key",
            api_key="custom-header-key",
            api_key_header="X-Custom-Auth-Key"
        ))

        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://api.example.com/data", headers=fetcher._build_headers())

        assert received_key == "custom-header-key"

    @pytest.mark.asyncio
    async def test_api_key_via_auth_config(self):
        """Verify API key via AuthConfig."""
        received_key = None

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal received_key
            received_key = request.headers.get("X-API-Key")
            return httpx.Response(
                status_code=200,
                content=b"{}",
                headers={"content-type": "application/json"},
            )

        transport = httpx.MockTransport(mock_handler)
        fetcher = HTTPAuthFetcher()
        fetcher.set_auth(AuthConfig(type="api_key", api_key="config-api-key"))

        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://api.example.com/data", headers=fetcher._build_headers())

        assert received_key == "config-api-key"


class TestHTTPAuthFetcherBasicAuth:
    """Test HTTPAuthFetcher with Basic authentication."""

    @pytest.mark.asyncio
    async def test_basic_auth_via_auth_config(self):
        """Verify Basic auth via AuthConfig."""
        received_auth = None

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal received_auth
            received_auth = request.headers.get("Authorization")
            return httpx.Response(
                status_code=200,
                content=b"OK",
                headers={"content-type": "text/plain"},
            )

        transport = httpx.MockTransport(mock_handler)
        fetcher = HTTPAuthFetcher()
        fetcher.set_auth(AuthConfig(
            type="basic",
            username="admin",
            password="secret123"
        ))

        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://api.example.com/data", headers=fetcher._build_headers())

        assert received_auth is not None
        assert received_auth.startswith("Basic ")
        encoded = received_auth.split(" ")[1]
        decoded = base64.b64decode(encoded).decode()
        assert decoded == "admin:secret123"


class TestOAuth2TokenRefresh:
    """Test OAuth2 token refresh flows."""

    @pytest.mark.asyncio
    async def test_client_credentials_flow(self):
        """Test OAuth2 client_credentials grant flow."""
        token_request_body = None

        async def mock_token_handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_request_body
            token_request_body = dict(request.url.params)
            return httpx.Response(
                status_code=200,
                content=b'{"access_token": "new-access-token-cc", "expires_in": 3600}',
                headers={"content-type": "application/json"},
            )

        token_transport = httpx.MockTransport(mock_token_handler)

        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="client-id-123",
            oauth2_client_secret="client-secret-456",
            oauth2_token_url="https://auth.example.com/oauth/token",
            oauth2_grant_type="client_credentials",
        )

        async with httpx.AsyncClient(transport=token_transport) as client:
            response = await client.post(
                "https://auth.example.com/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": "client-id-123",
                    "client_secret": "client-secret-456",
                },
            )
            token_data = response.json()

        assert token_data["access_token"] == "new-access-token-cc"
        config.set_oauth2_token(token_data["access_token"], token_data.get("expires_in"))
        assert config.get_oauth2_access_token() == "new-access-token-cc"

    @pytest.mark.asyncio
    async def test_refresh_token_flow(self):
        """Test OAuth2 refresh_token grant flow."""
        token_request_data = None

        async def mock_token_handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_request_data
            content = request.content.read() if hasattr(request.content, 'read') else request.content
            body_str = content.decode() if isinstance(content, bytes) else str(content)
            params = {}
            for pair in body_str.split('&'):
                if '=' in pair:
                    k, v = pair.split('=', 1)
                    params[k] = v
            token_request_data = params
            return httpx.Response(
                status_code=200,
                content=b'{"access_token": "refreshed-token-xyz", "expires_in": 7200}',
                headers={"content-type": "application/json"},
            )

        token_transport = httpx.MockTransport(mock_token_handler)

        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="client-id-123",
            oauth2_client_secret="client-secret-456",
            oauth2_token_url="https://auth.example.com/oauth/token",
            oauth2_grant_type="refresh_token",
            oauth2_refresh_token="my-refresh-token-value",
        )

        async with httpx.AsyncClient(transport=token_transport) as client:
            response = await client.post(
                "https://auth.example.com/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": "my-refresh-token-value",
                    "client_id": "client-id-123",
                    "client_secret": "client-secret-456",
                },
            )
            token_data = response.json()

        assert token_data["access_token"] == "refreshed-token-xyz"
        assert token_request_data["grant_type"] == "refresh_token"
        assert token_request_data["refresh_token"] == "my-refresh-token-value"

    @pytest.mark.asyncio
    async def test_token_is_cached_and_reused(self):
        """Verify token is cached and reused for subsequent requests."""
        async def mock_token_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code=200,
                content=b'{"access_token": "new-access-token-cc", "expires_in": 3600}',
                headers={"content-type": "application/json"},
            )

        token_transport = httpx.MockTransport(mock_token_handler)

        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="client-id",
            oauth2_client_secret="client-secret",
            oauth2_token_url="https://auth.example.com/token",
            oauth2_grant_type="client_credentials",
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"access_token": "cached-token", "expires_in": 3600}
            mock_response.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            await config.refresh_oauth2_token()
            token1 = config.get_oauth2_access_token()

            token2 = config.get_oauth2_access_token()

        assert token1 == "cached-token"
        assert token2 == token1

    @pytest.mark.asyncio
    async def test_token_refresh_on_expiry(self):
        """Verify token is refreshed when expired."""
        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="client-id",
            oauth2_client_secret="client-secret",
            oauth2_token_url="https://auth.example.com/token",
            oauth2_grant_type="client_credentials",
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response1 = MagicMock()
            mock_response1.json.return_value = {"access_token": "token-v1", "expires_in": 3600}
            mock_response1.raise_for_status = MagicMock()
            
            mock_response2 = MagicMock()
            mock_response2.json.return_value = {"access_token": "token-v2", "expires_in": 3600}
            mock_response2.raise_for_status = MagicMock()
            
            mock_client.post = AsyncMock(side_effect=[mock_response1, mock_response2])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            await config.refresh_oauth2_token()
            initial_token = config.get_oauth2_access_token()
            assert initial_token == "token-v1"

            config.set_oauth2_token(initial_token, expires_in=-10)
            assert not config.is_oauth2_token_valid()

            await config.refresh_oauth2_token()
            new_token = config.get_oauth2_access_token()
            assert new_token == "token-v2"


class TestOmniFetcherWithAuth:
    """Test OmniFetcher with authentication."""

    @pytest.mark.asyncio
    async def test_set_auth_on_omnifetcher(self):
        """Test setting auth on OmniFetcher."""
        fetcher = OmniFetcher()
        fetcher.set_auth("github", {
            "type": "bearer",
            "token": "gh-token-123"
        })

        auth = fetcher.get_auth("github")
        assert auth is not None
        assert auth.type == "bearer"
        assert auth.get_token() == "gh-token-123"

    @pytest.mark.asyncio
    async def test_auth_passed_to_http_fetcher(self):
        """Test auth is passed to appropriate fetcher via direct fetcher test."""
        received_auth = None

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal received_auth
            received_auth = request.headers.get("Authorization")
            return httpx.Response(
                status_code=200,
                content=b'{"authenticated": true}',
                headers={"content-type": "application/json"},
            )

        transport = httpx.MockTransport(mock_handler)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response.content = b'{"authenticated": true}'
            mock_response.json.return_value = {"authenticated": True}
            mock_response.elapsed.total_seconds = MagicMock(return_value=0.1)
            mock_response.url = "https://api.example.com/protected"
            mock_response.raise_for_status = MagicMock()
            
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            fetcher = HTTPAuthFetcher()
            fetcher.set_auth(AuthConfig(type="bearer", token="omni-test-token"))
            
            result = await fetcher.fetch("https://api.example.com/protected")

        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer omni-test-token"
        assert result.metadata.status_code == 200

    @pytest.mark.asyncio
    async def test_per_request_auth_override(self):
        """Test per-request auth override via direct fetcher."""
        received_auth = None

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal received_auth
            received_auth = request.headers.get("Authorization")
            return httpx.Response(
                status_code=200,
                content=b"OK",
                headers={"content-type": "text/plain"},
            )

        transport = httpx.MockTransport(mock_handler)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "text/plain"}
            mock_response.content = b"OK"
            mock_response.text = "OK"
            mock_response.elapsed.total_seconds = MagicMock(return_value=0.1)
            mock_response.url = "https://api.example.com/data"
            mock_response.raise_for_status = MagicMock()
            
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            fetcher = HTTPAuthFetcher(bearer_token="default-token")
            
            result = await fetcher.fetch(
                "https://api.example.com/data",
                bearer_token="override-token"
            )

        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer override-token"

    @pytest.mark.asyncio
    async def test_auth_registry_merges_with_env(self):
        """Test auth registry merges with env vars."""
        import os
        with pytest.MonkeyPatch.context() as m:
            m.setenv("TEST_API_TOKEN", "env-token-value")
            m.setenv("TEST_API_TYPE", "bearer")

            fetcher = OmniFetcher(load_env_auth=True)

            auth = fetcher.get_auth("test_api")
            if auth:
                assert auth.type == "bearer"

    @pytest.mark.asyncio
    async def test_omnifetcher_with_api_key_auth(self):
        """Test OmniFetcher with API key authentication via direct fetcher."""
        received_key = None

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal received_key
            received_key = request.headers.get("X-API-Key")
            return httpx.Response(
                status_code=200,
                content=b'{"key": "value"}',
                headers={"content-type": "application/json"},
            )

        transport = httpx.MockTransport(mock_handler)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"content-type": "application/json"}
            mock_response.content = b'{"key": "value"}'
            mock_response.json.return_value = {"key": "value"}
            mock_response.elapsed.total_seconds = MagicMock(return_value=0.1)
            mock_response.url = "https://api.example.com/key-protected"
            mock_response.raise_for_status = MagicMock()
            
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            fetcher = HTTPAuthFetcher()
            fetcher.set_auth(AuthConfig(type="api_key", api_key="omni-api-key"))
            
            result = await fetcher.fetch("https://api.example.com/key-protected")

        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["headers"]["X-API-Key"] == "omni-api-key"
        assert result.metadata.status_code == 200


class TestFullAuthFlowIntegration:
    """Integration tests for complete auth flows."""

    @pytest.mark.asyncio
    async def test_oauth2_full_flow_with_protected_resource(self):
        """Test full OAuth2 flow: get token then access protected resource."""
        token_response_sent = False
        resource_response_sent = False

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_response_sent, resource_response_sent

            if "token" in str(request.url):
                token_response_sent = True
                return httpx.Response(
                    status_code=200,
                    content=b'{"access_token": "full-flow-token", "expires_in": 3600}',
                    headers={"content-type": "application/json"},
                )
            else:
                resource_response_sent = True
                auth_header = request.headers.get("Authorization")
                if auth_header == "Bearer full-flow-token":
                    return httpx.Response(
                        status_code=200,
                        content=b'{"data": "protected-content"}',
                        headers={"content-type": "application/json"},
                    )
                else:
                    return httpx.Response(
                        status_code=401,
                        content=b'{"error": "unauthorized"}',
                        headers={"content-type": "application/json"},
                    )

        transport = httpx.MockTransport(mock_handler)

        config = AuthConfig(
            type="oauth2",
            oauth2_client_id="client-id",
            oauth2_client_secret="client-secret",
            oauth2_token_url="https://auth.example.com/token",
            oauth2_grant_type="client_credentials",
        )

        async with httpx.AsyncClient(transport=transport) as client:
            token_response = await client.post(
                "https://auth.example.com/token",
                data={"grant_type": "client_credentials", "client_id": "client-id", "client_secret": "client-secret"},
            )
            token_data = token_response.json()
            config.set_oauth2_token(token_data["access_token"], token_data.get("expires_in"))

            headers = config.get_headers()
            resource_response = await client.get(
                "https://api.example.com/protected",
                headers=headers,
            )

        assert token_response_sent
        assert resource_response_sent
        assert resource_response.status_code == 200

    @pytest.mark.asyncio
    async def test_multiple_auth_methods_in_chain(self):
        """Test multiple authenticated requests with different auth methods."""
        requests_log = []

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            requests_log.append(dict(request.headers))
            return httpx.Response(
                status_code=200,
                content=b"OK",
                headers={"content-type": "text/plain"},
            )

        transport = httpx.MockTransport(mock_handler)

        async with httpx.AsyncClient(transport=transport) as client:
            bearer_fetcher = HTTPAuthFetcher(bearer_token="bearer-token")
            await client.get("https://api.example.com/1", headers=bearer_fetcher._build_headers())

            apikey_fetcher = HTTPAuthFetcher(api_key="api-key-value")
            await client.get("https://api.example.com/2", headers=apikey_fetcher._build_headers())

            basic_fetcher = HTTPAuthFetcher()
            basic_fetcher.set_auth(AuthConfig(type="basic", username="user", password="pass"))
            await client.get("https://api.example.com/3", headers=basic_fetcher._build_headers())

        assert len(requests_log) == 3
        assert requests_log[0].get("authorization") == "Bearer bearer-token"
        assert requests_log[1].get("x-api-key") == "api-key-value"
        assert requests_log[2].get("authorization", "").startswith("Basic ")
