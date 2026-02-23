# Authentication

OmniFetcher supports multiple authentication methods to access protected resources.

## Authentication Methods

### Bearer Token

```python
from omni_fetcher import OmniFetcher

# Direct token
fetcher = OmniFetcher(auth={
    "github": {"type": "bearer", "token": "your-token"}
})

# From environment variable
fetcher = OmniFetcher(auth={
    "github": {"type": "bearer", "token_env": "GITHUB_TOKEN"}
})
```

### API Key

```python
fetcher = OmniFetcher(auth={
    "myapi": {
        "type": "api_key",
        "api_key": "your-api-key",
        "api_key_header": "X-API-Key"  # default header
    }
})

# From environment
fetcher = OmniFetcher(auth={
    "myapi": {
        "type": "api_key",
        "api_key_env": "API_KEY"
    }
})
```

### Basic Authentication

```python
fetcher = OmniFetcher(auth={
    "myapi": {
        "type": "basic",
        "username": "user",
        "password": "pass"
    }
})

# From environment
fetcher = OmniFetcher(auth={
    "myapi": {
        "type": "basic",
        "username_env": "BASIC_USER",
        "password_env": "BASIC_PASS"
    }
})
```

### AWS Credentials

```python
fetcher = OmniFetcher(auth={
    "s3": {
        "type": "aws",
        "aws_access_key_id": "your-key",
        "aws_secret_access_key": "your-secret",
        "aws_region": "us-east-1"
    }
})

# From environment (uses AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
fetcher = OmniFetcher(auth={
    "s3": {"type": "aws"}
})
```

### OAuth2

```python
fetcher = OmniFetcher(auth={
    "myapi": {
        "type": "oauth2",
        "oauth2_client_id": "client-id",
        "oauth2_client_secret": "client-secret",
        "oauth2_token_url": "https://api.example.com/oauth/token",
        "oauth2_scope": "read write",
        "oauth2_grant_type": "client_credentials"  # or "refresh_token"
    }
})

# With refresh token
fetcher = OmniFetcher(auth={
    "myapi": {
        "type": "oauth2",
        "oauth2_client_id": "client-id",
        "oauth2_client_secret": "client-secret",
        "oauth2_token_url": "https://api.example.com/oauth/token",
        "oauth2_refresh_token": "existing-refresh-token",
        "oauth2_grant_type": "refresh_token"
    }
})
```

### Google Service Account

```python
fetcher = OmniFetcher(auth={
    "google_drive": {
        "type": "google_service_account",
        "google_service_account_json": "/path/to/service-account.json"
    }
})

# Or set GOOGLE_APPLICATION_CREDENTIALS environment variable
```

## Environment Variable Loading

OmniFetcher can automatically load authentication from environment variables with the `OMNI_` prefix:

```bash
# Bearer token
export OMNI_GITHUB_TYPE=bearer
export OMNI_GITHUB_TOKEN_ENV=GITHUB_TOKEN

# API Key
export OMNI_MYAPI_TYPE=api_key
export OMNI_MYAPI_API_KEY_ENV=API_KEY

# Basic Auth
export OMNI_MYAPI_TYPE=basic
export OMNI_MYAPI_USERNAME_ENV=BASIC_USER
export OMNI_MYAPI_PASSWORD_ENV=BASIC_PASS

# AWS
export OMNI_S3_TYPE=aws

# OAuth2
export OMNI_API_TYPE=oauth2
export OMNI_API_OAUTH2_CLIENT_ID_ENV=CLIENT_ID
export OMNI_API_OAUTH2_CLIENT_SECRET_ENV=CLIENT_SECRET
export OMNI_API_OAUTH2_TOKEN_URL=https://api.example.com/token
```

Then create the fetcher:

```python
fetcher = OmniFetcher(load_env_auth=True)  # Enabled by default
```

## Source-Specific Auth

Authentication is tied to source names. The auth config key should match the source name:

```python
fetcher = OmniFetcher(auth={
    "github": {"type": "bearer", "token": "xxx"},
    "s3": {"type": "aws", "aws_access_key_id": "xxx"},
    "jira": {"type": "basic", "username": "xxx", "password": "xxx"},
})
```

## Setting Auth Dynamically

```python
fetcher = OmniFetcher()
fetcher.set_auth("github", {"type": "bearer", "token": "xxx"})
```

## Getting Auth Config

```python
auth = fetcher.get_auth("github")
if auth:
    print(auth.type)  # "bearer"
    print(auth.get_token())  # token value
```

## AuthConfig Reference

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | str | Auth type: `bearer`, `api_key`, `basic`, `aws`, `oauth2`, `google_service_account` |
| `token` | str | Bearer token |
| `token_env` | str | Environment variable name for token |
| `api_key` | str | API key value |
| `api_key_env` | str | Environment variable name for API key |
| `api_key_header` | str | Header name for API key (default: `X-API-Key`) |
| `username` | str | Username for basic auth |
| `username_env` | str | Environment variable for username |
| `password` | str | Password for basic auth |
| `password_env` | str | Environment variable for password |
| `aws_access_key_id` | str | AWS access key |
| `aws_access_key_id_env` | str | Env var for AWS access key |
| `aws_secret_access_key` | str | AWS secret key |
| `aws_secret_access_key_env` | str | Env var for AWS secret key |
| `aws_region` | str | AWS region |
| `aws_region_env` | str | Env var for AWS region |
| `oauth2_client_id` | str | OAuth2 client ID |
| `oauth2_client_id_env` | str | Env var for client ID |
| `oauth2_client_secret` | str | OAuth2 client secret |
| `oauth2_client_secret_env` | str | Env var for client secret |
| `oauth2_token_url` | str | OAuth2 token endpoint URL |
| `oauth2_scope` | str | OAuth2 scopes (space-separated) |
| `oauth2_grant_type` | str | Grant type: `client_credentials` or `refresh_token` |
| `oauth2_refresh_token` | str | Refresh token |
| `oauth2_refresh_token_env` | str | Env var for refresh token |
| `google_service_account_json` | str | Path to service account JSON file |
| `google_service_account_env` | str | Env var for service account JSON |
