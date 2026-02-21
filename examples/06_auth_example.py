"""Authentication examples for OmniFetcher.

This file demonstrates various authentication methods supported by OmniFetcher:
- Bearer token authentication
- API key authentication
- Basic authentication
- Loading auth from environment variables
- Setting auth via OmniFetcher constructor

Run with: python 06_auth_example.py
"""

import asyncio
import os

from omni_fetcher import OmniFetcher
from omni_fetcher.auth import AuthConfig, load_auth_from_env


async def bearer_token_example():
    """Example 1: Bearer token authentication using set_auth()."""
    print("\n" + "=" * 60)
    print("Example 1: Bearer Token Auth with set_auth()")
    print("=" * 60)
    
    fetcher = OmniFetcher(load_env_auth=False)
    
    # Set auth for a source using set_auth()
    fetcher.set_auth("http", {
        "type": "bearer",
        "token": "YOUR_BEARER_TOKEN_HERE",  # Replace with actual token
    })
    
    # Get the auth config to verify
    auth = fetcher.get_auth("http")
    if auth:
        headers = auth.get_headers()
        print(f"Auth type: {auth.type}")
        print(f"Auth headers: {headers}")
        print("\nNote: In production, use token_env instead of token for security")
    else:
        print("No auth configured")


async def api_key_example():
    """Example 2: API key authentication."""
    print("\n" + "=" * 60)
    print("Example 2: API Key Authentication")
    print("=" * 60)
    
    fetcher = OmniFetcher(load_env_auth=False)
    
    # API key with custom header
    fetcher.set_auth("http", {
        "type": "api_key",
        "api_key": "YOUR_API_KEY_HERE",  # Replace with actual key
        "api_key_header": "X-API-Key",    # Custom header name
    })
    
    auth = fetcher.get_auth("http")
    if auth:
        headers = auth.get_headers()
        print(f"Auth type: {auth.type}")
        print(f"API key header: {auth.api_key_header}")
        print(f"Auth headers: {headers}")
        print("\nNote: Use api_key_env for environment variable based keys")


async def basic_auth_example():
    """Example 3: Basic authentication."""
    print("\n" + "=" * 60)
    print("Example 3: Basic Authentication")
    print("=" * 60)
    
    fetcher = OmniFetcher(load_env_auth=False)
    
    # Basic auth with username/password
    fetcher.set_auth("http", {
        "type": "basic",
        "username": "admin",
        "password": "password123",  # In production, use env vars
    })
    
    auth = fetcher.get_auth("http")
    if auth:
        headers = auth.get_headers()
        print(f"Auth type: {auth.type}")
        print(f"Username: {auth.get_username()}")
        print(f"Auth headers (first 50 chars): {headers.get('Authorization', '')[:50]}...")
        print("\nNote: Use username_env and password_env for security")


async def env_variable_auth_example():
    """Example 4: Loading auth from environment variables."""
    print("\n" + "=" * 60)
    print("Example 4: Auth from Environment Variables")
    print("=" * 60)
    
    # Set environment variables for demonstration
    os.environ["OMNI_MYSERVICE_TYPE"] = "bearer"
    os.environ["OMNI_MYSERVICE_TOKEN_ENV"] = "MY_SERVICE_TOKEN"
    os.environ["MY_SERVICE_TOKEN"] = "my_secret_token_from_env"
    
    # Create fetcher with env loading enabled (default)
    fetcher = OmniFetcher(load_env_auth=True)
    
    # Check what auth was loaded
    auth = fetcher.get_auth("myservice")
    if auth:
        print(f"Loaded auth for source: 'myservice'")
        print(f"Auth type: {auth.type}")
        print(f"Token env var: {auth.token_env}")
        print(f"Actual token: {auth.get_token()}")
        print("\nEnvironment variables used:")
        print("  OMNI_MYSERVICE_TYPE=bearer")
        print("  OMNI_MYSERVICE_TOKEN_ENV=MY_SERVICE_TOKEN")
        print("  MY_SERVICE_TOKEN=<secret>")
    else:
        print("No auth loaded from environment")
    
    # Clean up
    del os.environ["OMNI_MYSERVICE_TYPE"]
    del os.environ["OMNI_MYSERVICE_TOKEN_ENV"]
    del os.environ["MY_SERVICE_TOKEN"]


async def constructor_auth_example():
    """Example 5: Setting auth via OmniFetcher constructor."""
    print("\n" + "=" * 60)
    print("Example 5: Auth via OmniFetcher Constructor")
    print("=" * 60)
    
    # Auth configs passed directly to constructor
    fetcher = OmniFetcher(load_env_auth=False, auth={
        "github": {
            "type": "bearer",
            "token_env": "GITHUB_TOKEN",  # Will read from this env var
        },
        "api": {
            "type": "api_key",
            "api_key_env": "API_KEY",
            "api_key_header": "Authorization",
        },
    })
    
    # Set the env var for demo
    os.environ["GITHUB_TOKEN"] = "ghp_example_token"
    
    github_auth = fetcher.get_auth("github")
    api_auth = fetcher.get_auth("api")
    
    print("Auth configs set via constructor:")
    if github_auth:
        print(f"  - github: type={github_auth.type}, token_env={github_auth.token_env}")
    if api_auth:
        print(f"  - api: type={api_auth.type}, header={api_auth.api_key_header}")
    
    # Demonstrate auth hierarchy (per-request > source > env)
    print("\nAuth resolution order:")
    print("  1. Per-request auth (passed to fetch())")
    print("  2. Explicit source auth (set_auth())")
    print("  3. Constructor auth")
    print("  4. Environment variable auth")
    
    # Clean up
    del os.environ["GITHUB_TOKEN"]


async def auth_config_direct_usage():
    """Example 6: Using AuthConfig directly."""
    print("\n" + "=" * 60)
    print("Example 6: Direct AuthConfig Usage")
    print("=" * 60)
    
    # Create AuthConfig directly
    auth = AuthConfig(
        type="bearer",
        token="my_direct_token",
    )
    
    # Get headers for HTTP requests
    headers = auth.get_headers()
    print(f"Auth type: {auth.type}")
    print(f"Headers: {headers}")
    
    # Convert to dict (with masked values)
    auth_dict = auth.to_dict()
    print(f"\nSerialized auth (masked): {auth_dict}")
    
    # Test with environment variable fallback
    os.environ["TEST_TOKEN"] = "token_from_env"
    auth2 = AuthConfig(
        type="bearer",
        token_env="TEST_TOKEN",
    )
    print(f"\nWith token_env fallback:")
    print(f"  Token: {auth2.get_token()}")
    del os.environ["TEST_TOKEN"]


async def main():
    """Run all authentication examples."""
    print("OmniFetcher Authentication Examples")
    print("=" * 60)
    
    await bearer_token_example()
    await api_key_example()
    await basic_auth_example()
    await env_variable_auth_example()
    await constructor_auth_example()
    await auth_config_direct_usage()
    
    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
