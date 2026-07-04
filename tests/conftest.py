"""Pytest configuration."""

import importlib

import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the SourceRegistry singleton before each test."""
    from omni_fetcher.core.registry import SourceRegistry
    from omni_fetcher import fetcher as fetcher_module

    SourceRegistry.reset_instance()
    yield
    # Re-import fetcher to re-register sources after test
    importlib.reload(fetcher_module)
    SourceRegistry.reset_instance()
