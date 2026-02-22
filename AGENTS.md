# AGENTS.md - OmniFetcher Development Guide

This file provides development guidelines for agents working on the OmniFetcher codebase.

## Project Overview

OmniFetcher (v0.4.0) is a universal data fetcher library that retrieves data from various sources (local files, HTTP APIs, S3, YouTube, RSS, etc.) and returns it as validated Pydantic models. It uses Python 3.10+ with async/await patterns.

## Build, Test, and Lint Commands

### Installation

```bash
# Install package with all dependencies
pip install -e ".[dev]"

# Install optional dependencies
pip install -e ".[office]"   # DOCX, PPTX support
pip install -e ".[web]"       # Web scraping support
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=omni_fetcher

# Run a specific test file
pytest tests/fetchers/test_fetchers.py

# Run a specific test class
pytest tests/fetchers/test_fetchers.py::TestLocalFileFetcher

# Run a single test (most specific)
pytest tests/fetchers/test_fetchers.py::TestLocalFileFetcher::test_local_file_fetcher_creation

# Run tests matching a pattern
pytest -k "test_fetch"

# Run with verbose output
pytest -v

# Run with pdb debugger on failures
pytest --pdb
```

### Code Quality Tools

```bash
# Format code (in-place)
ruff format .

# Check formatting without changes
ruff format --check .

# Lint all files (auto-fix where possible)
ruff check . --fix

# Run mypy type checking
mypy omni_fetcher/
```

## Code Style Guidelines

### Imports

The project uses Ruff for import organization. Follow these rules:

- Use `from __future__ import annotations` at the top of every file
- Group imports in this order: stdlib, third-party, local/application
- Within each group, sort alphabetically
- Use `TYPE_CHECKING` block for type-only imports when needed
- Avoid wildcard imports (`from x import *`)

```python
# Correct import order
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
```

### Formatting

- Maximum line length: 100 characters
- Use 4 spaces for indentation (not tabs)
- Add blank lines between top-level class/function definitions
- Use trailing commas in multi-line imports and calls
- Prefer double quotes for strings unless single quotes are more appropriate

```python
# Good
result = await fetcher.fetch(
    "https://api.example.com/users",
    timeout=30.0,
)

# Bad - line too long
result = await fetcher.fetch("https://api.example.com/users/very/long/path/that/exceeds/limit")
```

### Type Annotations

The project uses mypy with Python 3.10+ style annotations:

- Use built-in types instead of typing module when possible (`list` not `List`)
- Use union syntax (`X | Y`) instead of `Optional[X]` or `Union[X, Y]`
- Add return type annotations to all functions
- Use `Any` sparingly; prefer concrete types when possible
- Mark async functions with `async def` and await the return

```python
# Good
def process_data(data: dict[str, Any]) -> list[str]:
    ...

async def fetch(uri: str) -> dict[str, Any] | None:
    ...

# Avoid
def process_data(data):  # Missing type
    ...

def process_data(data: Dict[str, Any]) -> List[str]:  # Use built-in types
    ...
```

### Naming Conventions

- **Classes**: `PascalCase` (e.g., `OmniFetcher`, `BaseFetcher`)
- **Functions/methods**: `snake_case` (e.g., `fetch_data`, `get_auth`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `DEFAULT_TIMEOUT`)
- **Private members**: Prefix with underscore (e.g., `_auth_registry`)
- **Modules**: `snake_case` (e.g., `local_file.py`)
- **Package**: lowercase (e.g., `omni_fetcher`)

### Pydantic Models

- Use Pydantic v2 syntax (`model_validate`, `model_dump`)
- Use `Field()` for field customization with descriptions
- Prefer `field_validator` over `validator` (Pydantic v2)
- Use `ConfigDict` instead of inner `Config` class

```python
# Good (Pydantic v2)
class FetchMetadata(BaseModel):
    source_uri: str = Field(default="", description="Original URI")
    fetched_at: datetime = Field(default_factory=datetime.now)
    
    model_config = ConfigDict(use_enum_values=True)

# Avoid (Pydantic v1 style)
class FetchMetadata(BaseModel):
    source_uri: str = ""
    fetched_at: datetime = None
    
    class Config:
        use_enum_values = True
```

### Error Handling

- Use custom exceptions from `omni_fetcher.core.exceptions`
- Catch specific exceptions, avoid bare `except Exception`
- Use `raise ... from err` to chain exceptions properly
- Wrap external library calls in try/except when needed

```python
# Good
from omni_fetcher.core.exceptions import FetchError, SourceNotFoundError

try:
    result = await fetcher.fetch(uri)
except httpx.HTTPError as e:
    raise FetchError(uri, str(e)) from e

# Avoid
try:
    result = await fetcher.fetch(uri)
except Exception:  # Too broad
    pass
```

### Documentation

- Use Google-style docstrings for all public functions/classes
- Include Examples section in class docstrings
- Document Args, Returns, and Raises in method docstrings
- Keep descriptions concise but informative

```python
class OmniFetcher:
    """Universal fetcher that can fetch data from any source.
    
    OmniFetcher automatically detects the appropriate handler for a given
    URI and returns data in a Pydantic model.
    
    Example:
        fetcher = OmniFetcher()
        result = await fetcher.fetch("https://api.example.com/users")
    """
    
    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch data from the given URI.
        
        Args:
            uri: The URI to fetch (file path, URL, etc.)
            **kwargs: Additional options to pass to the fetcher
            
        Returns:
            Pydantic model with the fetched data
            
        Raises:
            SourceNotFoundError: No suitable handler found
            FetchError: Fetching failed
        """
```

### Async/Await Patterns

- All fetcher methods should be async
- Use `async with` for context managers
- Use `asyncio.gather` for concurrent operations
- Set appropriate timeouts on HTTP clients (default 30s recommended)

```python
# Good
async with httpx.AsyncClient(timeout=30.0) as client:
    response = await client.get(uri)
    return response.json()

# Bad - no timeout
async with httpx.AsyncClient() as client:
    ...
```

### Testing Guidelines

- Test files go in `tests/` directory mirroring the source structure
- Name test files as `test_<module>.py`
- Use pytest with asyncio mode (already configured)
- Use `pytest.mark.asyncio` for async tests
- Test naming: `test_<what>_<expected_behavior>`
- Use mocks from `unittest.mock` for external dependencies
- Use the `reset_registry` fixture to clean state between tests

```python
@pytest.mark.asyncio
async def test_fetch_returns_valid_data():
    """Test that fetch returns properly validated data."""
    fetcher = LocalFileFetcher()
    result = await fetcher.fetch("/path/to/data.json")
    assert result.metadata.source_name == "local_file"
```

## Architecture Patterns

### Creating a Custom Fetcher

Use the `@source` decorator to register new fetchers:

```python
from omni_fetcher import source, BaseFetcher

@source(
    name="my_source",
    uri_patterns=["example.com"],
    priority=50,
)
class MyFetcher(BaseFetcher):
    name = "my_source"
    priority = 50
    
    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return "example.com" in uri
    
    async def fetch(self, uri: str, **kwargs):
        # Implementation
        pass
```

### Registry Reset in Tests

The `SourceRegistry` is a singleton. Use the `reset_registry` fixture:

```python
@pytest.fixture(autouse=True)
def reset_registry():
    from omni_fetcher.core.registry import SourceRegistry
    SourceRegistry.reset_instance()
    # ... reload modules if needed
```

## Project Structure

```
omni_fetcher/
├── omni_fetcher/           # Main package
│   ├── fetcher.py          # Main OmniFetcher class
│   ├── core/
 omni_fetcher/
├── omni_fetcher/           # Main package
│   ├── fetcher.py          # Main OmniFetcher class
│   ├── core/
│   │   ├── registry.py     # SourceRegistry, @source decorator
│   │   └── exceptions.py   # Custom exceptions
│   ├── fetchers/           # Built-in fetchers
│   │   ├── base.py         # BaseFetcher abstract class
│   │   ├── local_file.py   # Local file fetcher
│   │   ├── http_url.py     # HTTP fetcher
│   │   └── ...
│   ├── schemas/            # Pydantic models
│   │   ├── base.py         # BaseFetchedData, FetchMetadata
│   │   ├── atomics.py      # TextDocument, ImageDocument, etc.
│   │   └── ...
│   ├── auth/               # Authentication
│   └── cache/              # Caching backends
├── tests/                  # Test suite
│   ├── fetchers/           # Fetcher tests
│   ├── schemas/            # Schema tests
│   └── conftest.py         # Pytest fixtures
└── examples/               # Usage examples
```

## Common Tasks

### Adding a New Fetcher

1. Create `omni_fetcher/fetchers/<name>.py`
2. Inherit from `BaseFetcher`
3. Add `@source` decorator with name, uri_patterns, priority
4. Implement `fetch()` method
5. Add tests in `tests/fetchers/test_<name>.py`

### Adding a New Schema

1. Add to appropriate module in `omni_fetcher/schemas/`
2. Inherit from `BaseFetchedData` or appropriate base class
3. Export in `omni_fetcher/schemas/__init__.py`
4. Export in `omni_fetcher/__init__.py`
5. Add tests in `tests/schemas/`

### Running Full CI Locally

```bash
# Run all checks
ruff format --check . && ruff check . && mypy omni_fetcher/ && pytest
```
