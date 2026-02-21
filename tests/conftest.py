"""Pytest configuration."""
import importlib
import pytest


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the SourceRegistry singleton before each test, then reload fetcher modules to re-run decorators."""
    from omni_fetcher.core.registry import SourceRegistry
    SourceRegistry.reset_instance()
    
    fetcher_modules = [
        "omni_fetcher.fetchers.local_file",
        "omni_fetcher.fetchers.http_url",
        "omni_fetcher.fetchers.http_json",
        "omni_fetcher.fetchers.http_auth",
        "omni_fetcher.fetchers.youtube",
        "omni_fetcher.fetchers.s3",
        "omni_fetcher.fetchers.rss",
        "omni_fetcher.fetchers.pdf",
        "omni_fetcher.fetchers.csv",
    ]
    
    for module_name in fetcher_modules:
        importlib.import_module(module_name)
        importlib.reload(importlib.import_module(module_name))
