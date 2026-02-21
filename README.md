# OmniFetcher

[![PyPI Version](https://img.shields.io/pypi/v/omni_fetcher.svg)](https://pypi.org/project/omni_fetcher/)
[![Python Versions](https://img.shields.io/pypi/pyversions/omni_fetcher.svg)](https://pypi.org/project/omni_fetcher/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![pytest](https://github.com/yourusername/omni_fetcher/actions/workflows/test.yml/badge.svg)](https://github.com/yourusername/omni_fetcher/actions/workflows/test.yml)

Universal data fetcher that can fetch data from any source and return it as predefined Pydantic objects.

## Project Overview

OmniFetcher is a powerful, flexible data fetching library that provides a unified interface for retrieving data from various sources. Whether you need to fetch data from local files, HTTP APIs, cloud storage, or media platforms, OmniFetcher handles them all with a consistent, type-safe API backed by Pydantic v2 for robust data validation.

### Key Benefits

- **Unified Interface**: Fetch from any source using the same API
- **Type Safety**: All fetched data is validated against Pydantic models
- **Extensible**: Create custom fetchers using the `@source` decorator
- **Built-in Features**: Caching, retry with exponential backoff, and rate limiting
- **Authentication**: Support for bearer tokens, API keys, basic auth, AWS, and OAuth2

## Features

- **Multiple Data Sources**: Built-in support for local files, HTTP URLs, JSON APIs, YouTube, RSS feeds, S3, PDFs, and CSV files
- **Plugin Architecture**: Register custom fetchers via the `@source` decorator
- **Authentication**: Multiple auth methods (bearer, API key, basic, AWS, OAuth2)
- **Caching**: In-memory and file-based caching backends with TTL support
- **Retry Logic**: Exponential backoff with configurable retry attempts
- **Rate Limiting**: Built-in rate limiter for API compliance
- **Pydantic Validation**: All data is validated and returned as typed Pydantic models

## Installation

```bash
pip install omni_fetcher
```

### Install with development dependencies

```bash
pip install omni_fetcher[dev]
```

### Dependencies

- `pydantic>=2.0` - Data validation
- `httpx>=0.24.0` - HTTP client
- `python-magic>=0.4.27` - File type detection
- `beautifulsoup4>=4.12.0` - HTML/XML parsing
- `pillow>=10.0.0` - Image processing
- `python-dateutil>=2.8.0` - Date utilities
- `yt-dlp>=2023.0.0` - YouTube downloading
- `feedparser>=6.0.0` - RSS/Atom feed parsing
- `boto3>=1.28.0` - AWS S3 access
- `pypdf>=3.0.0` - PDF parsing

## Quick Start

```python
import asyncio
from omni_fetcher import OmniFetcher

async def main():
    fetcher = OmniFetcher()
    
    # Fetch JSON from an API
    result = await fetcher.fetch("https://jsonplaceholder.typicode.com/users/1")
    print(result.data)
    
    # Fetch from a local file
    result = await fetcher.fetch("/path/to/data.json")
    print(result.data)

asyncio.run(main())
```

### Using Authentication

```python
import asyncio
from omni_fetcher import OmniFetcher

async def main():
    fetcher = OmniFetcher(auth={
        "github": {"type": "bearer", "token_env": "GITHUB_TOKEN"}
    })
    
    # Fetch authenticated data
    result = await fetcher.fetch("https://api.github.com/user")
    print(result.data)

asyncio.run(main())
```

## Architecture

OmniFetcher is built on a plugin/registry pattern that allows seamless addition of new data sources:

```
┌─────────────────────────────────────────────────────────────┐
│                      OmniFetcher                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Registry  │  │ Auth Config │  │   Cache Backends    │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ BaseFetcher   │   │   AuthConfig  │   │ RetryConfig  │
│   (abstract)  │   │               │   │  RateLimiter │
└───────────────┘   └───────────────┘   └───────────────┘
        │
        ▼
┌───────────────┬───────────────┬───────────────┬──────────────┐
│LocalFileFetcher│HTTPURLFetcher│YouTubeFetcher │ ... more     │
└───────────────┴───────────────┴───────────────┴──────────────┘
```

### Core Components

- **OmniFetcher**: Main entry point for fetching data
- **SourceRegistry**: Singleton registry that manages all registered fetchers
- **BaseFetcher**: Abstract base class for all fetchers
- **AuthConfig**: Authentication configuration for secured sources
- **Cache Backends**: In-memory or file-based caching with TTL

## Built-in Fetchers

| Fetcher | URI Patterns | Description |
|---------|--------------|-------------|
| `local_file` | File paths (`/path/to/file`, `file://...`) | Read local files (JSON, CSV, PDF, text) |
| `http_url` | `http://*`, `https://*` | Generic HTTP/HTTPS fetcher |
| `http_json` | URLs ending in `.json` | Specialized JSON API fetcher |
| `http_auth` | Auth-enabled HTTP URLs | HTTP with authentication |
| `youtube` | `youtube.com`, `youtu.be` | YouTube video metadata |
| `rss` | RSS/Atom feed URLs | RSS and Atom feed parsing |
| `s3` | `s3://bucket/key` | AWS S3 object retrieval |
| `pdf` | PDF file URLs or paths | PDF document parsing |
| `csv` | CSV file URLs or paths | CSV data extraction |

### URI Pattern Examples

```python
# Local files
result = await fetcher.fetch("/path/to/data.json")
result = await fetcher.fetch("file:///path/to/document.pdf")

# HTTP resources
result = await fetcher.fetch("https://api.example.com/data")
result = await fetcher.fetch("https://api.example.com/data.json")

# YouTube
result = await fetcher.fetch("https://youtube.com/watch?v=xyz123")
result = await fetcher.fetch("https://youtu.be/xyz123")

# RSS Feeds
result = await fetcher.fetch("https://blog.example.com/feed.xml")

# AWS S3
result = await fetcher.fetch("s3://my-bucket/data.json")

# Documents
result = await fetcher.fetch("/path/to/document.pdf")
result = await fetcher.fetch("/path/to/data.csv")
```

## Authentication

OmniFetcher supports multiple authentication methods:

### Bearer Token

```python
from omni_fetcher import OmniFetcher, AuthConfig

fetcher = OmniFetcher(auth={
    "myapi": {"type": "bearer", "token": "your-token"}
})

# Or load from environment variable
fetcher = OmniFetcher(auth={
    "myapi": {"type": "bearer", "token_env": "API_TOKEN"}
})
```

### API Key

```python
fetcher = OmniFetcher(auth={
    "myapi": {
        "type": "api_key",
        "api_key": "your-api-key",
        "api_key_header": "X-API-Key"  # default
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

### AWS Authentication

```python
fetcher = OmniFetcher(auth={
    "s3": {
        "type": "aws",
        "aws_access_key_id": "your-key",
        "aws_secret_access_key": "your-secret",
        "aws_region": "us-east-1"
    }
})

# From environment (AWS credentials)
fetcher = OmniFetcher(auth={
    "s3": {"type": "aws"}  # Uses AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY env vars
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
```

### Environment Variable Loading

Load auth configs from environment variables with the `OMNI_` prefix:

```bash
export OMNI_GITHUB_TYPE=bearer
export OMNI_GITHUB_TOKEN_ENV=GITHUB_TOKEN

export OMNI_S3_TYPE=aws
export OMNI_S3_AWS_ACCESS_KEY_ID_ENV=AWS_ACCESS_KEY_ID

export OMNI_API_TYPE=oauth2
export OMNI_API_OAUTH2_CLIENT_ID_ENV=CLIENT_ID
export OMNI_API_OAUTH2_CLIENT_SECRET_ENV=CLIENT_SECRET
export OMNI_API_OAUTH2_TOKEN_URL=https://api.example.com/token
```

```python
fetcher = OmniFetcher(load_env_auth=True)  # Enabled by default
```

## Caching & Retry

### Caching

OmniFetcher provides two cache backends:

```python
from omni_fetcher.cache import FileCacheBackend, MemoryCacheBackend

# File-based cache (persists across runs)
file_cache = FileCacheBackend(cache_dir=".cache", default_ttl=3600)

# In-memory cache (fast, ephemeral)
memory_cache = MemoryCacheBackend(default_ttl=1800)

# Use with fetcher (implement in your custom fetcher)
result = await memory_cache.get(cache_key)
if result is None:
    result = await fetch_data(uri)
    await memory_cache.set(cache_key, result, ttl=600)
```

### Retry with Exponential Backoff

```python
from omni_fetcher.utils.retry import RetryConfig, with_retry

# Configure retry behavior
config = RetryConfig(
    max_attempts=3,        # Maximum 3 attempts
    initial_delay=1.0,     # Start with 1 second delay
    max_delay=30.0,        # Cap at 30 seconds
    exponential_base=2.0,  # Delay doubles each attempt
    retry_on=(httpx.HTTPError,),
)

# Use as decorator
@with_retry(config)
async def fetch_with_retry(uri: str):
    async with httpx.AsyncClient() as client:
        return await client.get(uri)
```

### Rate Limiting

```python
from omni_fetcher.utils.retry import RateLimiter

# 10 calls per second
limiter = RateLimiter(calls_per_second=10)

async def limited_fetch(uri: str):
    async with limiter:
        return await fetcher.fetch(uri)
```

## Custom Fetchers

Create custom fetchers using the `@source` decorator:

```python
import asyncio
from datetime import datetime
from typing import Optional

import httpx

from omni_fetcher import source, BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata
from omni_fetcher.schemas.structured import JSONData


@source(
    name="github",
    uri_patterns=["github.com", "api.github.com"],
    mime_types=["application/json"],
    priority=15,
    description="Fetch data from GitHub API"
)
class GitHubFetcher(BaseFetcher):
    """Fetcher for GitHub API endpoints."""
    
    name = "github"
    priority = 15
    
    def __init__(self, token: Optional[str] = None):
        super().__init__()
        self.token = token
    
    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return "github.com" in uri.lower()
    
    async def fetch(self, uri: str, **kwargs):
        api_url = self._convert_to_api_url(uri)
        
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "OmniFetcher-GitHub"
        }
        
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(api_url, headers=headers)
            response.raise_for_status()
            data = response.json()
        
        metadata = FetchMetadata(
            source_uri=uri,
            fetched_at=datetime.now(),
            source_name=self.name,
            mime_type="application/json",
            status_code=response.status_code,
        )
        
        return JSONData(
            metadata=metadata,
            data=data,
            root_keys=list(data.keys()) if isinstance(data, dict) else None,
        )
```

### Decorator Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Unique name for the source |
| `uri_patterns` | `list[str]` | URI patterns this fetcher handles (glob or regex) |
| `mime_types` | `list[str]` | MIME types this fetcher handles |
| `priority` | `int` | Lower = higher priority (default: 100) |
| `description` | `str` | Human-readable description |
| `auth` | `dict` | Default auth configuration |

### BaseFetcher Methods

Override these methods in your custom fetcher:

```python
class BaseFetcher:
    name: str = "base"
    priority: int = 100
    
    def __init__(self):
        self._auth: Optional[AuthConfig] = None
    
    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this fetcher can handle the URI."""
        raise NotImplementedError
    
    async def fetch(self, uri: str, **kwargs) -> BaseFetchedData:
        """Fetch data from the URI."""
        raise NotImplementedError
    
    async def fetch_metadata(self, uri: str) -> dict:
        """Fetch only metadata (optional)."""
        return {}
    
    def set_auth(self, auth: AuthConfig) -> None:
        """Set authentication config."""
        self._auth = auth
```

## API Reference

### OmniFetcher

Main class for fetching data from any source.

```python
from omni_fetcher import OmniFetcher

fetcher = OmniFetcher(
    auto_register_builtins: bool = True,  # Register built-in fetchers
    auth: Optional[Dict[str, Dict]] = None,  # Auth configs per source
    load_env_auth: bool = True,  # Load from environment
)
```

#### Methods

| Method | Description |
|--------|-------------|
| `fetch(uri, **kwargs)` | Fetch data from URI, returns Pydantic model |
| `fetch_metadata(uri)` | Fetch only metadata |
| `list_sources()` | List all registered source names |
| `get_source_info(name)` | Get `SourceInfo` for a source |
| `register_source(...)` | Register a custom source |
| `set_auth(source, auth)` | Set auth for a source |
| `get_auth(source)` | Get auth config for a source |
| `unregister_source(name)` | Unregister a source |

### Source Registry

Singleton registry for managing fetchers.

```python
from omni_fetcher import SourceRegistry, source

registry = SourceRegistry()
registry.register(
    name="my_source",
    fetcher_class=MyFetcher,
    uri_patterns=["pattern1", "pattern2"],
    mime_types=["application/json"],
    priority=50,
)
```

### Decorator

```python
from omni_fetcher import source

@source(
    name="my_source",
    uri_patterns=["example.com"],
    priority=50
)
class MyFetcher(BaseFetcher):
    pass
```

### Schemas

OmniFetcher provides Pydantic models for different data types:

#### Base Schemas

```python
from omni_fetcher import (
    BaseFetchedData,      # Base class for all fetched data
    FetchMetadata,        # Metadata about the fetch operation
    MediaType,            # Enum for media types
    DataCategory,         # Enum for data categories
)
```

#### Media Schemas

```python
from omni_fetcher import (
    BaseMedia,
    Video, Audio, Image,
    YouTubeVideo, LocalVideo,
    StreamAudio, LocalAudio,
    WebImage, LocalImage,
)
```

#### Document Schemas

```python
from omni_fetcher import (
    BaseDocument,
    TextDocument, MarkdownDocument, HTMLDocument,
    PDFDocument, CSVData,
)
```

#### Structured Data Schemas

```python
from omni_fetcher import (
    BaseStructuredData,
    JSONData, YAMLData, XMLData,
    GraphQLResponse,
)
```

### Exceptions

```python
from omni_fetcher import (
    OmniFetcherError,      # Base exception
    SourceNotFoundError,   # No handler found for URI
    FetchError,            # Fetching failed
    ValidationError,       # Pydantic validation failed
    SourceRegistrationError,  # Registration failed
    SchemaError,           # Schema-related error
)
```

## Examples

The `examples/` directory contains comprehensive examples:

| Example | Description |
|---------|-------------|
| `01_basic_usage.py` | Basic fetching from APIs and files |
| `02_custom_fetcher.py` | Creating a custom GitHub fetcher |
| `03_custom_schema.py` | Using custom Pydantic schemas |
| `04_cli_example.py` | Building a CLI with OmniFetcher |
| `05_media_example.py` | Fetching media (YouTube, images) |
| `06_auth_example.py` | Various authentication methods |
| `07_oauth2_example.py` | OAuth2 authentication flow |
| `08_s3_auth_example.py` | AWS S3 authentication |

Run examples:

```bash
python examples/01_basic_usage.py
python examples/02_custom_fetcher.py
```

## Testing

Run tests with pytest:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=omni_fetcher

# Run specific test file
pytest tests/test_fetchers.py

# Run with verbose output
pytest -v
```

### Test Structure

```
tests/
├── conftest.py              # Pytest fixtures
├── test_auth.py             # Authentication tests
├── test_auth_integration.py # Auth integration tests
├── core/
│   └── test_registry.py     # Registry tests
├── fetchers/
│   ├── test_fetchers.py    # Fetcher tests
│   ├── test_pdf.py         # PDF fetcher tests
│   ├── test_rss.py         # RSS fetcher tests
│   ├── test_s3.py          # S3 fetcher tests
│   └── test_youtube.py     # YouTube fetcher tests
└── schemas/
    ├── test_base.py        # Base schema tests
    ├── test_media.py       # Media schema tests
    └── test_structured.py  # Structured data tests
```

## License

MIT License

Copyright (c) 2024 OmniFetcher Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
