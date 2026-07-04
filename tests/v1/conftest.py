"""Test bootstrap for the isolated ``omni_fetcher.v1`` contract package.

The legacy top-level ``omni_fetcher/__init__.py`` eagerly imports every
connector (google_drive, docx, ...), several of which require optional
third-party packages that are not installed in a minimal contract-only
environment. Importing ``omni_fetcher.v1.*`` would therefore trigger that
heavy ``__init__`` and fail before any v1 code runs.

The v1 package is intentionally self-contained: it imports nothing from the
legacy connector tree. To exercise it in isolation we register a *bare*
``omni_fetcher`` package object -- one whose ``__path__`` points at the real
source directory but whose eager ``__init__`` body is never executed. Python
then resolves ``omni_fetcher.v1`` and its submodules from disk normally,
without the connector imports.

This shim is a test-only concern; it does not touch shipped code.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PKG_DIR = os.path.join(_REPO_ROOT, "omni_fetcher")


def _install_bare_parent() -> None:
    """Register a no-eager-init ``omni_fetcher`` package for v1 imports."""
    existing = sys.modules.get("omni_fetcher")
    # If the real (heavy) package already imported cleanly, leave it alone.
    if existing is not None and getattr(existing, "__file__", None):
        return
    pkg = types.ModuleType("omni_fetcher")
    pkg.__path__ = [_PKG_DIR]
    sys.modules["omni_fetcher"] = pkg


_install_bare_parent()


@pytest.fixture(autouse=True)
def reset_registry():
    """Override the legacy autouse registry-reset fixture as a no-op.

    The repo-root ``tests/conftest.py`` defines an autouse ``reset_registry``
    fixture that imports the heavy legacy ``omni_fetcher.fetcher`` module
    (and its connector tree). The v1 contract package has no such global
    registry singleton, so for v1 tests we shadow that fixture with a no-op
    of the same name to keep these tests self-contained.
    """
    yield
