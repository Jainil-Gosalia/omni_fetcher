"""The OmniFetcher MCP server: the canonical contract, over stdio, for agents.

A thin adapter that wraps ``OmniFetcher(builtin_registry())`` and exposes it as
a Model Context Protocol server so Claude (or any MCP client) can fetch any
built-in source through the v1 contract with no user-written glue. Two tools:

- ``fetch(uri, zoom?, tags?)`` -- route a URI through the built-in registry and
  return the canonical ``Result`` as JSON. Bounded sources only; an unbounded
  source returns the connector's own typed ``UNSUPPORTED`` pointing at the CLI.
- ``list_sources()`` -- the registered sources, their URI patterns, and whether
  each is bounded (fetchable) or unbounded (stream-only).

Credentials never enter the model's context: the server reads them once at
startup from its own environment (``OMNI_FETCHER_<SOURCE>_<FIELD>``) and injects
them per call into the stateless orchestrator. The model only supplies a URI.

This package lives behind the optional ``mcp`` extra. Importing it without the
MCP SDK installed raises a clear ``ImportError`` naming the extra; the core
install and ``builtin_registry()`` are untouched (see the v1.7 MCP PRD, D12).
"""

from __future__ import annotations

try:
    import mcp as _mcp  # noqa: F401  (probe only)
except ImportError as exc:  # pragma: no cover - exercised via a subprocess test
    raise ImportError(
        "The OmniFetcher MCP server requires the 'mcp' extra. "
        "Install it with:  pip install omni_fetcher[mcp]"
    ) from exc

from omni_fetcher.mcp.credentials import CredentialStore, load_credentials
from omni_fetcher.mcp.server import build_server, run

__all__ = [
    "build_server",
    "run",
    "CredentialStore",
    "load_credentials",
]
