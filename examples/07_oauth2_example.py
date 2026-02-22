"""OAuth2 authentication examples for OmniFetcher.

This file demonstrates OAuth2 authentication flows:
- Client credentials grant flow
- Refresh token flow
- Using OAuth2 with a fetcher

Note: These examples use placeholder URLs. In production, replace with
actual OAuth2 provider endpoints (e.g., Auth0, Okta, Keycloak, etc.)

Run with: python 07_oauth2_example.py
"""

import asyncio
import os

from omni_fetcher import OmniFetcher
from omni_fetcher.auth import AuthConfig


async def oauth2_client_credentials_example():
    """Example 1: OAuth2 Client Credentials Grant Flow."""
    print("\n" + "=" * 60)
    print("Example 1: OAuth2 Client Credentials Grant")
    print("=" * 60)

    # Create OAuth2 config with client credentials
    auth = AuthConfig(
        type="oauth2",
        oauth2_client_id="YOUR_CLIENT_ID",  # Replace with actual client ID
        oauth2_client_secret="YOUR_CLIENT_SECRET",  # Replace with actual secret
        oauth2_token_url="https://auth.example.com/oauth/token",
        oauth2_grant_type="client_credentials",
        oauth2_scope="read write",
    )

    print("OAuth2 Configuration:")
    print(f"  Grant type: {auth.oauth2_grant_type}")
    print(f"  Token URL: {auth.oauth2_token_url}")
    print(
        f"  Client ID: {auth.oauth2_client_id[:8]}..."
        if auth.oauth2_client_id
        else "  Client ID: None"
    )
    print(f"  Scope: {auth.oauth2_scope}")

    # Note: In production, don't hardcode credentials
    # Use environment variables instead:
    # os.environ["OAUTH_CLIENT_ID"] = "your_client_id"
    # os.environ["OAUTH_CLIENT_SECRET"] = "your_client_secret"
    # auth = AuthConfig(
    #     type="oauth2",
    #     oauth2_client_id_env="OAUTH_CLIENT_ID",
    #     oauth2_client_secret_env="OAUTH_CLIENT_SECRET",
    #     oauth2_token_url="https://auth.example.com/oauth/token",
    # )


async def oauth2_refresh_token_example():
    """Example 2: OAuth2 Refresh Token Flow."""
    print("\n" + "=" * 60)
    print("Example 2: OAuth2 Refresh Token Flow")
    print("=" * 60)

    # OAuth2 config with refresh token grant
    auth = AuthConfig(
        type="oauth2",
        oauth2_client_id="YOUR_CLIENT_ID",
        oauth2_client_secret="YOUR_CLIENT_SECRET",
        oauth2_token_url="https://auth.example.com/oauth/token",
        oauth2_grant_type="refresh_token",
        oauth2_refresh_token="YOUR_REFRESH_TOKEN",  # Replace with actual refresh token
    )

    print("OAuth2 Refresh Token Configuration:")
    print(f"  Grant type: {auth.oauth2_grant_type}")
    print(f"  Token URL: {auth.oauth2_token_url}")
    print(f"  Has refresh token: {bool(auth.get_oauth2_refresh_token())}")

    # Get credentials
    creds = auth.get_oauth2_credentials()
    print(f"  Client ID set: {bool(creds.get('client_id'))}")
    print(f"  Client secret set: {bool(creds.get('client_secret'))}")


async def oauth2_token_refresh_demo():
    """Example 3: Demonstrating OAuth2 token refresh."""
    print("\n" + "=" * 60)
    print("Example 3: OAuth2 Token Refresh Demo")
    print("=" * 60)

    # Set up with mock token URL for demo
    # In production, this would be a real OAuth2 server
    auth = AuthConfig(
        type="oauth2",
        oauth2_client_id="demo_client_id",
        oauth2_client_secret="demo_client_secret",
        oauth2_token_url="https://httpbin.org/post",  # Mock endpoint for demo
    )

    # Check initial state
    print("Initial state:")
    print(f"  Has access token: {bool(auth.get_oauth2_access_token())}")
    print(f"  Token valid: {auth.is_oauth2_token_valid()}")

    # Simulate setting a token (in production, this comes from the OAuth2 server)
    auth.set_oauth2_token("demo_access_token_12345", expires_in=3600)

    print("\nAfter setting token:")
    print(f"  Access token: {auth.get_oauth2_access_token()}")
    print(f"  Token valid: {auth.is_oauth2_token_valid()}")
    print(f"  Token expiry: {auth.oauth2_token_expiry}")

    # Get headers for requests
    headers = auth.get_headers()
    print(f"  Request headers: {headers}")


async def oauth2_with_fetcher_example():
    """Example 4: Using OAuth2 with OmniFetcher."""
    print("\n" + "=" * 60)
    print("Example 4: OAuth2 with OmniFetcher")
    print("=" * 60)

    # Create fetcher with OAuth2 auth
    fetcher = OmniFetcher(
        load_env_auth=False,
        auth={
            "myapi": {
                "type": "oauth2",
                "oauth2_client_id_env": "OAUTH_CLIENT_ID",
                "oauth2_client_secret_env": "OAUTH_CLIENT_SECRET",
                "oauth2_token_url": "https://api.example.com/oauth/token",
                "oauth2_scope": "api:read",
            },
        },
    )

    # Check the auth config
    auth = fetcher.get_auth("myapi")
    if auth:
        print("OAuth2 auth configured for 'myapi':")
        print(f"  Type: {auth.type}")
        print(f"  Grant type: {auth.oauth2_grant_type}")
        print(f"  Token URL: {auth.oauth2_token_url}")
        print(f"  Scope: {auth.oauth2_scope}")
        print(f"  Has client ID: {bool(auth.get_oauth2_credentials().get('client_id'))}")

        # Note: The actual token fetch happens when making requests
        # The fetcher will call refresh_oauth2_token() if needed
        print("\n  To use with fetch():")
        print("  1. Set environment variables: OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET")
        print("  2. Call fetch() - auth will be applied automatically")
        print("  3. Token will be refreshed automatically when expired")


async def oauth2_env_variable_example():
    """Example 5: OAuth2 with environment variables."""
    print("\n" + "=" * 60)
    print("Example 5: OAuth2 from Environment Variables")
    print("=" * 60)

    # Set up environment variables (for demo purposes)
    os.environ["OMNI_MYAPI_TYPE"] = "oauth2"
    os.environ["OMNI_MYAPI_OAUTH2_CLIENT_ID_ENV"] = "MYAPI_CLIENT_ID"
    os.environ["OMNI_MYAPI_OAUTH2_CLIENT_SECRET_ENV"] = "MYAPI_CLIENT_SECRET"
    os.environ["OMNI_MYAPI_OAUTH2_TOKEN_URL"] = "https://api.example.com/token"
    os.environ["OMNI_MYAPI_OAUTH2_SCOPE"] = "read write"
    os.environ["MYAPI_CLIENT_ID"] = "env_client_id_123"
    os.environ["MYAPI_CLIENT_SECRET"] = "env_secret_456"

    # Create fetcher - auth will be loaded from environment
    fetcher = OmniFetcher(load_env_auth=True)

    auth = fetcher.get_auth("myapi")
    if auth:
        print("OAuth2 loaded from environment variables:")
        print(f"  Type: {auth.type}")
        print(f"  Client ID: {auth.get_oauth2_credentials().get('client_id')}")
        print(f"  Token URL: {auth.oauth2_token_url}")
        print(f"  Scope: {auth.oauth2_scope}")

        print("\nEnvironment variables set:")
        print("  OMNI_MYAPI_TYPE=oauth2")
        print("  OMNI_MYAPI_OAUTH2_CLIENT_ID_ENV=MYAPI_CLIENT_ID")
        print("  OMNI_MYAPI_OAUTH2_CLIENT_SECRET_ENV=MYAPI_CLIENT_SECRET")
        print("  OMNI_MYAPI_OAUTH2_TOKEN_URL=https://api.example.com/token")
        print("  OMNI_MYAPI_OAUTH2_SCOPE=read write")

    # Clean up
    for key in [
        "OMNI_MYAPI_TYPE",
        "OMNI_MYAPI_OAUTH2_CLIENT_ID_ENV",
        "OMNI_MYAPI_OAUTH2_CLIENT_SECRET_ENV",
        "OMNI_MYAPI_OAUTH2_TOKEN_URL",
        "OMNI_MYAPI_OAUTH2_SCOPE",
        "MYAPI_CLIENT_ID",
        "MYAPI_CLIENT_SECRET",
    ]:
        if key in os.environ:
            del os.environ[key]


async def main():
    """Run all OAuth2 examples."""
    print("OmniFetcher OAuth2 Authentication Examples")
    print("=" * 60)

    await oauth2_client_credentials_example()
    await oauth2_refresh_token_example()
    await oauth2_token_refresh_demo()
    await oauth2_with_fetcher_example()
    await oauth2_env_variable_example()

    print("\n" + "=" * 60)
    print("All OAuth2 examples completed!")
    print("=" * 60)
    print("\nNote: For actual API calls, OAuth2 tokens are automatically")
    print("refreshed when expired. Set up credentials via env vars for security.")


if __name__ == "__main__":
    asyncio.run(main())
