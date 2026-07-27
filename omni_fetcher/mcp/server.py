"""The OmniFetcher MCP server: two tools over stdio, wrapping the v1 contract.

``build_server`` wires a ``FastMCP`` with ``fetch`` and ``list_sources`` around
a stateless ``OmniFetcher(builtin_registry())`` and a boot-time credential
store. It is a thin adapter: every hard decision (routing, typed errors,
bounded-vs-unbounded, zoom) already lives in the contract, and the server only
translates between MCP tool calls and ``Result`` values (see the v1.7 MCP PRD).

``run`` is the entry point: build the server and serve stdio.
"""

from __future__ import annotations

import asyncio
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
from omni_fetcher.v1.fetcher import COLLECTION_KIND, BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Error,
    Gap,
    Partial,
    Result,
    Success,
    error,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec, parse_zoom_spec

logger = logging.getLogger("omni_fetcher.mcp")

# Default cap on a serialised tool result. A fetched tree lands straight in the
# model's context, so an unbounded document must degrade honestly rather than
# flood it (PRD D10).
DEFAULT_MAX_BYTES = 1024 * 1024  # 1 MiB

# ``sample`` defaults: a small window and a short wall-clock budget, tuned for
# an agent peeking at an unbounded stream rather than draining it.
DEFAULT_MAX_ITEMS = 10
DEFAULT_TIMEOUT_SECONDS = 30.0


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


def _fold_sample(
    trees: list[CompositionNode],
    gaps: list[Gap],
    first_error: Optional[Error],
    uri: str,
    *,
    max_items: int,
    timeout: float,
    stopped: str,
) -> Result:
    """Fold sampled stream items into one ``Result``.

    Non-empty samples become one synthetic ``"collection"`` root whose children
    are the sampled trees in arrival order, with the stop condition recorded in
    ``source_extra["sample"]`` (count, the requested cap and window, and why it
    stopped: ``max_items`` / ``timeout`` / ``stream_end`` / ``error``). Errors
    fold into the ``gaps`` channel, so a sample that hit an error still returns
    what it collected. An empty sample is never a silent success: it is the
    first error if one occurred, a ``NOT_FOUND`` if the stream simply ended, or
    a retryable ``TRANSIENT`` if the window elapsed with the stream idle.
    """
    if not trees:
        if first_error is not None:
            return first_error
        if stopped == "timeout":
            return error(
                kind=ErrorKind.TRANSIENT,
                message=(
                    f"sampled 0 items from {uri} within {timeout}s; the stream "
                    "was idle in the window -- retry, or raise timeout_seconds"
                ),
                locator=uri,
            )
        return error(
            kind=ErrorKind.NOT_FOUND,
            message="stream produced no items",
            locator=uri,
        )

    root = build_node(
        kind=COLLECTION_KIND,
        children=trees,
        source_url=uri,
        source_namespace="sample",
        source_fields={
            "count": len(trees),
            "max_items": max_items,
            "timeout_seconds": timeout,
            "stopped": stopped,
        },
    )
    if gaps:
        return partial(root, gaps)
    return success(root)


async def _sample_stream(
    orchestrator: OmniFetcher,
    uri: str,
    *,
    auth: Any,
    zoom: Optional[ZoomSpec],
    tags: Optional[list[str]],
    max_items: int,
    timeout: float,
) -> Result:
    """Collect up to ``max_items`` items from an unbounded stream, then stop.

    The bounded window over an unbounded source: iterate the orchestrator's
    stream (which already applies zoom, merges tags, and cleans up), taking
    node-bearing items until ``max_items`` is reached, the wall-clock
    ``timeout`` elapses, the stream ends, or an error item arrives. The stream
    is always closed on the way out (``aclose``), so the connector releases its
    broker consumer / file handle / socket whether we finished, timed out, or
    the caller's task was cancelled.

    Timeout is enforced per pending item against a single overall deadline, so
    an idle stream cannot hang the tool: ``wait_for`` cancels the outstanding
    ``__anext__`` and the deadline caps the total wait.
    """
    trees: list[CompositionNode] = []
    gaps: list[Gap] = []
    first_error: Optional[Error] = None
    stopped = "max_items"

    stream = orchestrator.stream(uri, auth=auth, zoom=zoom, tags=tags)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout if timeout and timeout > 0 else None
    try:
        while len(trees) < max_items:
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                stopped = "timeout"
                break
            try:
                if remaining is None:
                    item = await stream.__anext__()
                else:
                    item = await asyncio.wait_for(stream.__anext__(), remaining)
            except StopAsyncIteration:
                stopped = "stream_end"
                break
            except (asyncio.TimeoutError, TimeoutError):
                stopped = "timeout"
                break

            if isinstance(item, Success):
                trees.append(item.tree)
            elif isinstance(item, Partial):
                trees.append(item.tree)
                gaps.extend(item.gaps)
            else:  # Error -- terminates the sample, never dropped.
                first_error = item
                gaps.append(gap(kind=item.kind, locator=item.locator, detail=item.message))
                stopped = "error"
                break
    finally:
        # The orchestrator's stream is an async generator; closing it releases
        # the connector's resources (mirrors the CLI's stream command).
        await stream.aclose()  # type: ignore[attr-defined]

    return _fold_sample(
        trees, gaps, first_error, uri, max_items=max_items, timeout=timeout, stopped=stopped
    )


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
                github://, jira://, s3://, ...). For an http(s) page, append
                "?__omni_text_format=plain" to get flattened prose instead of
                the default markdown -- use it when markdown syntax is noise
                (embedding, keyword indexing, speech). The key is stripped
                before the request and never reaches the site. Markdown is a
                better default for anything that quotes or cites a passage:
                it drops nav/footer chrome and is the only format that can be
                addressed by section as well as paragraph and sentence.
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
        result = _enrich_unbounded_fetch(result, definition)
        return result.model_dump(mode="json")

    @server.tool()
    async def sample(
        uri: str,
        max_items: int = DEFAULT_MAX_ITEMS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        zoom: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> dict:
        """Sample a bounded window of items from an unbounded stream.

        For stream-only sources (kafka://, tail://, redis://, ws://, sse://,
        postgres-cdc://) that `fetch` refuses, `sample` collects up to
        `max_items` items -- or whatever arrives within `timeout_seconds` --
        into one `Result`: a `collection` tree whose children are the sampled
        items, with the stop reason in `source_extra.sample`. Also works on a
        bounded source (it just returns up to `max_items`). Credentials come
        from the server's environment -- never pass a token here.

        Args:
            uri: The source URI to sample. An http(s) URI accepts the same
                "?__omni_text_format=plain" key documented on `fetch`.
            max_items: Maximum items to collect (default 10).
            timeout_seconds: Wall-clock budget; stop even if fewer arrive
                (default 30). An idle stream yields a retryable `transient`.
            zoom: Optional decomposition depth, e.g. "text=sentence".
            tags: Optional advisory labels merged into each item's metadata.
        """
        try:
            spec = parse_zoom_spec(zoom)
        except ValueError as exc:
            return error(kind=ErrorKind.INVALID_INPUT, message=str(exc), locator=uri).model_dump(
                mode="json"
            )
        if max_items < 1:
            return error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"max_items must be >= 1, got {max_items}",
                locator=uri,
            ).model_dump(mode="json")

        definition = reg.definition_for(uri)
        source = definition.name if definition is not None else None
        credential = creds.get(source) if source is not None else None

        result = await _sample_stream(
            orchestrator,
            uri,
            auth=credential,
            zoom=spec,
            tags=tags,
            max_items=max_items,
            timeout=timeout_seconds,
        )
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


def _enrich_unbounded_fetch(result: Result, definition: Any) -> Result:
    """Point an unbounded ``fetch`` at the ``sample`` tool.

    An unbounded source refuses ``fetch`` with a typed ``UNSUPPORTED`` (its own
    message names the CLI's ``stream``). Over MCP the better next step is the
    ``sample`` tool, so the message is extended to name it -- but only when the
    source is genuinely stream-only, never for a bounded source that returned
    ``UNSUPPORTED`` for an unrelated (recognised-but-unsupported) reason.
    """
    if (
        isinstance(result, Error)
        and result.kind is ErrorKind.UNSUPPORTED
        and definition is not None
        and not _is_bounded(definition)
    ):
        extra = "use the `sample` tool for a bounded window of this stream"
        message = f"{result.message}; {extra}" if result.message else extra
        return error(kind=ErrorKind.UNSUPPORTED, message=message, locator=result.locator)
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
