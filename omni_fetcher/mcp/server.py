"""The OmniFetcher MCP server: two tools over stdio, wrapping the v1 contract.

``build_server`` wires a ``FastMCP`` with ``fetch`` and ``list_sources`` around
a stateless ``OmniFetcher(builtin_registry())`` and a boot-time credential
store. It is a thin adapter: every hard decision (routing, typed errors,
bounded-vs-unbounded, zoom) already lives in the contract, and the server only
translates between MCP tool calls and ``Result`` values (see the v1.7 MCP PRD).

``run`` is the entry point: build the server and serve stdio.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from omni_fetcher.mcp.credentials import CredentialStore, load_credentials
from omni_fetcher.v1 import (
    ErrorKind,
    OmniFetcher,
    Registry,
    builtin_registry,
)
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Error,
    Partial,
    Result,
    error,
    gap,
    partial,
)
from omni_fetcher.v1.zoom import parse_zoom_spec

logger = logging.getLogger("omni_fetcher.mcp")

# Default cap on a serialised tool result. A fetched tree lands straight in the
# model's context, so an unbounded document must degrade honestly rather than
# flood it (PRD D10).
DEFAULT_MAX_BYTES = 1024 * 1024  # 1 MiB


def _result_bytes(result: Result) -> int:
    """Serialised size of a result, in UTF-8 bytes."""
    return len(result.model_dump_json().encode("utf-8"))


def _drop_child(node: CompositionNode) -> CompositionNode:
    """Return a copy of ``node`` with its last child removed."""
    return CompositionNode(metadata=node.metadata, children=list(node.children[:-1]))


def _apply_size_guard(result: Result, max_bytes: int) -> Result:
    """Bound a result's serialised size, degrading to a ``Partial`` honestly.

    Whole child subtrees are dropped from the root, last first, until the
    payload fits -- each drop recorded as a typed ``Gap`` so the omission is
    never silent. If the root fits only when empty and still overflows (a
    single oversized atom on the root itself), the honest answer is a typed
    ``UNSUPPORTED`` demanding a coarser zoom, not a truncated atom.
    """
    if isinstance(result, Error) or _result_bytes(result) <= max_bytes:
        return result

    tree = result.tree
    base_gaps = list(result.gaps) if isinstance(result, Partial) else []
    dropped = 0
    while tree.children and _result_bytes(partial(tree, base_gaps)) > max_bytes:
        tree = _drop_child(tree)
        dropped += 1

    if dropped and _result_bytes(partial(tree, base_gaps)) <= max_bytes:
        overflow_gap = gap(
            kind=ErrorKind.UNSUPPORTED,
            locator=tree.metadata.source_url,
            detail=(
                f"result exceeded the {max_bytes}-byte cap; dropped {dropped} "
                "trailing subtree(s) from the root -- request a coarser zoom to "
                "keep them"
            ),
        )
        return partial(tree, base_gaps + [overflow_gap])

    # Even an empty root overflows: the content is one oversized node. Refuse
    # honestly rather than emit a truncated atom.
    return error(
        kind=ErrorKind.UNSUPPORTED,
        message=(
            f"result exceeds the {max_bytes}-byte cap and cannot be reduced by "
            "dropping subtrees; request a coarser zoom"
        ),
        locator=tree.metadata.source_url,
    )


def _is_bounded(definition: Any) -> bool:
    """Report whether a source is bounded (its ``fetch`` is the base sugar).

    A stream-only (unbounded) connector overrides ``fetch`` to return a typed
    ``UNSUPPORTED``; a bounded one inherits ``BaseFetcher.fetch``. The registry
    holds lazy proxies, so the real class is reached by instantiating (which
    the stateless design makes cheap and side-effect-free) and inspecting its
    type. Any failure is treated as bounded -- the common case -- with a note.
    """
    try:
        instance = definition.fetcher_class()
        return type(instance).fetch is BaseFetcher.fetch
    except Exception:  # pragma: no cover - defensive; connectors construct freely
        logger.debug("could not probe boundedness for %r; assuming bounded", definition.name)
        return True


def build_server(
    registry: Optional[Registry] = None,
    credentials: Optional[CredentialStore] = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    name: str = "omni-fetcher",
) -> FastMCP:
    """
    Build the OmniFetcher MCP server

    Wires ``fetch`` and ``list_sources`` around a stateless orchestrator and a
    credential store. Injectable ``registry`` and ``credentials`` make the
    server drivable in-process by tests without a live MCP client or network.

    Parameters
    ----------
        registry:
            The source registry to route through. Defaults to
            ``builtin_registry()``.
        credentials:
            The boot-time credential store. Defaults to one loaded from the
            environment for the registry's sources.
        max_bytes:
            Cap on a serialised tool result before the size guard degrades it.
        name:
            The MCP server name advertised to clients.

    Return
    ------
        server:
            The configured ``FastMCP`` instance.
    """
    reg = registry if registry is not None else builtin_registry()
    known = frozenset(d.name for d in reg.definitions())
    creds = credentials if credentials is not None else load_credentials(known)
    orchestrator = OmniFetcher(reg)

    server = FastMCP(name)

    @server.tool()
    async def fetch(uri: str, zoom: Optional[str] = None, tags: Optional[list[str]] = None) -> dict:
        """Fetch a URI through the canonical contract and return the typed result.

        Routes ``uri`` to its source, fetches it, and returns the canonical
        ``Result`` as JSON (a ``success`` / ``partial`` tree, or a typed
        ``error`` to branch on). Credentials are supplied by the server from
        its environment -- never pass a token here. Bounded sources only; an
        unbounded source (kafka://, tail://, ...) returns ``unsupported``.

        Args:
            uri: The source URI (a file path, https URL, or a scheme like
                github://, jira://, s3://, ...).
            zoom: Optional decomposition depth, e.g. "text=paragraph" or
                "text=sentence,image=whole".
            tags: Optional advisory labels merged into the result's metadata.
        """
        try:
            spec = parse_zoom_spec(zoom)
        except ValueError as exc:
            return error(kind=ErrorKind.INVALID_INPUT, message=str(exc), locator=uri).model_dump(
                mode="json"
            )

        definition = reg.definition_for(uri)
        source = definition.name if definition is not None else None
        credential = creds.get(source) if source is not None else None

        result = await orchestrator.fetch(uri, auth=credential, zoom=spec, tags=tags)
        result = _apply_size_guard(result, max_bytes)
        result = _enrich_unconfigured_auth(result, source, creds)
        return result.model_dump(mode="json")

    @server.tool()
    def list_sources() -> dict:
        """List the sources this server can route, and how to reach each.

        Returns each source's name, the URI patterns it claims, and whether it
        is bounded (fetchable via `fetch`) or unbounded (stream-only -- use the
        `omni-fetcher v1 stream` CLI). Sources whose optional extra is not
        installed are absent.
        """
        sources = [
            {
                "name": d.name,
                "uri_patterns": list(d.uri_patterns),
                "bounded": _is_bounded(d),
            }
            for d in sorted(reg.definitions(), key=lambda d: d.name)
        ]
        return {"sources": sources}

    return server


def _enrich_unconfigured_auth(
    result: Result,
    source: Optional[str],
    creds: CredentialStore,
) -> Result:
    """Name the missing env var when auth failed and nothing was configured.

    A connector that needs credentials and got ``None`` returns a typed
    ``AUTH_FAILED`` from the upstream 401. When the server had no credential
    configured for that source, the actionable cause is the missing env var,
    so the message is enriched with the convention hint (PRD story 3). When a
    credential *was* configured, the failure is a real rejection and the
    connector's own message stands.
    """
    if (
        isinstance(result, Error)
        and result.kind is ErrorKind.AUTH_FAILED
        and source is not None
        and not creds.has(source)
    ):
        hint = CredentialStore.env_hint(source)
        message = f"{result.message}; {hint}" if result.message else hint
        return error(kind=ErrorKind.AUTH_FAILED, message=message, locator=result.locator)
    return result


def run(*, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    """
    Build the server from the environment and serve over stdio

    The production entry point: builds the server around ``builtin_registry()``
    and environment-resolved credentials, then serves the stdio transport that
    Claude Desktop and Claude Code use.

    Parameters
    ----------
        max_bytes:
            Cap on a serialised tool result before the size guard degrades it.
    """
    logging.basicConfig(level=logging.INFO)
    server = build_server(max_bytes=max_bytes)
    server.run(transport="stdio")
