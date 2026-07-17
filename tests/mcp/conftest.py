"""Test bootstrap for the ``omni_fetcher.mcp`` server package.

Mirrors ``tests/v1/conftest.py``: it registers a bare ``omni_fetcher`` parent
package (whose eager legacy ``__init__`` is never executed) so that importing
``omni_fetcher.mcp`` -- which pulls in ``omni_fetcher.v1.*`` -- does not drag in
the legacy connector tree, and it shadows the repo-root autouse
``reset_registry`` fixture with a no-op. The MCP server wraps the v1 contract
only, so it needs the same isolation.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PKG_DIR = os.path.join(_REPO_ROOT, "omni_fetcher")


def _install_bare_parent() -> None:
    """Register a no-eager-init ``omni_fetcher`` package for v1/mcp imports."""
    existing = sys.modules.get("omni_fetcher")
    if existing is not None and getattr(existing, "__file__", None):
        return
    pkg = types.ModuleType("omni_fetcher")
    pkg.__path__ = [_PKG_DIR]
    sys.modules["omni_fetcher"] = pkg


_install_bare_parent()


@pytest.fixture(autouse=True)
def reset_registry():
    """No-op shadow of the repo-root autouse registry-reset fixture."""
    yield
