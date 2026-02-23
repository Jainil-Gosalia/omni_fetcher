# OmniFetcher Documentation

Welcome to the OmniFetcher documentation. OmniFetcher is a universal data fetcher library that retrieves data from various sources and returns it as validated Pydantic models.

## Quick Links

- [Installation](installation.md)
- [Quick Start](quickstart.md)
- [Built-in Fetchers](fetchers.md)
- [Schemas](schemas.md)
- [Authentication](auth.md)
- [Caching & Retry](caching.md)
- [Custom Fetchers](custom.md)
- [CLI Usage](cli.md)
- [API Reference](api.md)

## Features

- **20+ Built-in Fetchers**: Local files, HTTP APIs, YouTube, S3, PDF, CSV, Office documents, GitHub, Notion, Jira, Confluence, Slack, and more
- **Type Safety**: All fetched data is validated against Pydantic v2 models
- **Extensible**: Create custom fetchers using the `@source` decorator
- **Authentication**: Bearer tokens, API keys, Basic Auth, AWS credentials, OAuth2
- **Caching**: In-memory and file-based caching with TTL support
- **Retry Logic**: Exponential backoff with configurable retry attempts
- **Rate Limiting**: Built-in rate limiter for API compliance

## Version

Current version: **v0.11.2**

## Installation

```bash
pip install omni_fetcher

# With all dependencies
pip install omni_fetcher[dev]

# Optional: Office document support
pip install omni_fetcher[office]

# Optional: Web scraping support
pip install omni_fetcher[web]
```

## Basic Usage

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

## Examples

See the `examples/` directory for 17 comprehensive examples:

| Example | Description |
|---------|-------------|
| 01_basic_usage.py | Basic fetching from APIs and files |
| 02_custom_fetcher.py | Creating a custom fetcher |
| 03_custom_schema.py | Using custom Pydantic schemas |
| 04_cli_example.py | Building a CLI with OmniFetcher |
| 05_media_example.py | Fetching media (YouTube, images) |
| 06_auth_example.py | Various authentication methods |
| 07_oauth2_example.py | OAuth2 authentication flow |
| 08_s3_auth_example.py | AWS S3 authentication |
| 09_atomic_schemas_example.py | Atomic schema primitives |
| 10_office_webpage_example.py | Office documents and web pages |
| 11_audio_containers_example.py | Container schemas for feeds and playlists |
| 12_github_example.py | GitHub API integration |
| 13_google_drive_example.py | Google Drive file fetching |
| 14_notion_example.py | Notion workspace integration |
| 15_confluence_example.py | Confluence pages and spaces |
| 16_slack_example.py | Slack messaging integration |
| 17_jira_example.py | Jira issues and projects |
