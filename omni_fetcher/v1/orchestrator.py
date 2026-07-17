"""The stateless ``OmniFetcher`` orchestrator for the v1 contract.

``OmniFetcher`` is the boundary object a host wires once and calls per
request. It owns no per-request state: every call to ``fetch()`` / ``stream()``
routes through the immutable registry, instantiates the matching fetcher
fresh, invokes its ``stream()`` / ``fetch()`` with the *per-call* ``auth`` and
``zoom``, and returns/yields the resulting ``Result`` value(s). Nothing is
cached, accumulated, or mutated between calls.

Statelessness contract (see ``PHILOSOPHY.md`` sections 6 and 7, and the v1
plan's registry/auth decisions):

- **No retained state.** The orchestrator keeps only its (immutable) registry
  and (stateless) auth resolver. It never stores credentials, fetched trees,
  routing results, or any other per-call data on the instance. Two calls in
  either order produce identical results; concurrent calls cannot observe
  each other's data or auth.
- **Fresh fetcher per call.** The resolved ``BaseFetcher`` subclass is
  instantiated anew for each call, so a connector accidentally holding
  per-instance state cannot leak it across requests.
- **Per-call auth only.** Credentials are accepted as an explicit ``auth``
  argument and passed straight through to the fetcher. The orchestrator never
  reads ambient environment variables, never loads a ``.env`` file, and never
  caches or mutates tokens onto shared objects.
- **Routing via the registry.** ``registry.resolve(uri)`` selects the fetcher.
  An unrouted URI yields ``error(ErrorKind.NOT_FOUND)`` -- a returned value,
  never a raised exception.
- **Caller tags merge into metadata.** When ``tags`` are supplied they are
  unioned into each returned node's ``Metadata.tags`` using the contract's own
  deduplicating tag handling. The merge is applied to a copy; the fetcher's
  own returned tree is never mutated in place.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional, Sequence

from omni_fetcher.v1.auth import AuthCredential, AuthResolver
from omni_fetcher.v1.decompose import decompose_result
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.registry import Registry
from omni_fetcher.v1.result import (
    Partial,
    Result,
    Success,
    error,
)
from omni_fetcher.v1.zoom import ZoomSpec, prune_result


class OmniFetcher:
    """
    The stateless orchestrator for the v1 contract
    ===============================================
    Routes a URI to its fetcher via the immutable registry, instantiates that
    fetcher fresh per call, invokes its ``stream()`` / ``fetch()`` with the
    per-call ``auth`` and ``zoom``, optionally merges caller ``tags`` into the
    returned node metadata, and returns/yields the resulting ``Result``(s).
    Holds no state between calls.
    ===============================================
    NOTE:
        1. The orchestrator retains only its immutable ``Registry`` and a
           stateless ``AuthResolver``; it stores no credentials, no fetched
           data, and no routing results across calls. Calls are independent
           and safe to interleave concurrently.
        2. Credentials are passed per call via ``auth`` and forwarded
           transiently to the fetcher. No ambient environment / ``.env`` is
           ever read, and no token is cached or mutated on a shared object.
        3. A URI no source claims returns ``error(ErrorKind.NOT_FOUND)`` as a
           value; expected failures are never raised.
        4. Caller ``tags`` are merged into each returned node's
           ``Metadata.tags`` via the contract's deduplicating tag handling,
           on a copy -- the fetcher's own tree is never mutated.

    Attributes
    ----------
        _registry:
            The immutable source-definition registry used for routing.
        _auth_resolver:
            The optional stateless auth resolver (unused for routing; held so
            hosts can supply a single shared resolver).

    Methods
    -------
        fetch:
        stream:
    """

    __slots__ = ("_registry", "_auth_resolver")

    def __init__(
        self,
        registry: Registry,
        auth_resolver: Optional[AuthResolver] = None,
    ) -> None:
        """
        Wire the orchestrator with a registry and optional auth resolver

        Parameters
        ----------
            registry:
                The immutable source-definition registry that routes a URI to
                its fetcher class. Held read-only; never mutated.
            auth_resolver:
                Optional stateless ``AuthResolver``. The orchestrator forwards
                per-call credentials to fetchers directly, so the resolver is
                retained only for hosts that share one explicit instance; it
                is never used to load ambient credentials.
        """
        self._registry = registry
        self._auth_resolver = auth_resolver

    async def fetch(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> Result:
        """
        Route, fetch, and return a single ``Result`` for a URI

        Resolves the fetcher for ``uri`` via the registry, instantiates it
        fresh, and awaits its base-provided ``fetch()`` with the per-call
        ``auth`` and ``zoom``. If ``tags`` are supplied they are merged into
        the returned node's metadata. No state is retained between calls.

        NOTE:
            1. An unrouted URI returns ``error(ErrorKind.NOT_FOUND)``; this is
               a returned value, not a raised exception.

        Parameters
        ----------
            uri:
                The source URI to fetch.
            auth:
                The per-call credential, or ``None`` for unauthenticated
                sources. Passed straight to the fetcher; never read from the
                ambient environment.
            zoom:
                Optional per-atom-type zoom spec; ``None`` means each source's
                natural granularity.
            tags:
                Optional free-form advisory labels to merge into the returned
                node's ``Metadata.tags``.

        Return
        ------
            result:
                A single ``Result`` -- ``success`` / ``partial`` with the
                (optionally tag-merged) tree, or ``error`` (including
                ``NOT_FOUND`` when no source claims ``uri``).
        """
        fetcher = self._resolve(uri)
        if fetcher is None:
            return self._not_found(uri)

        result = await fetcher.fetch(uri, auth=auth, zoom=zoom)
        return _merge_tags(result, tags)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> AsyncIterator[Result]:
        """
        Route and stream ``Result`` items for a URI

        Resolves the fetcher for ``uri`` via the registry, instantiates it
        fresh, and yields each item from its ``stream()`` with the per-call
        ``auth`` and ``zoom``. If ``tags`` are supplied they are merged into
        each yielded node's metadata. No state is retained between calls.

        NOTE:
            1. An unrouted URI yields a single ``error(ErrorKind.NOT_FOUND)``
               item and then terminates; no exception is raised.

        Parameters
        ----------
            uri:
                The source URI to stream.
            auth:
                The per-call credential, or ``None``. Passed straight to the
                fetcher; never read from the ambient environment.
            zoom:
                Optional per-atom-type zoom spec; ``None`` means natural
                granularity.
            tags:
                Optional free-form advisory labels to merge into each yielded
                node's ``Metadata.tags``.

        Return
        ------
            results:
                An async iterator of ``Result`` items. Terminates for bounded
                sources; does not for unbounded ones.
        """
        fetcher = self._resolve(uri)
        if fetcher is None:
            yield self._not_found(uri)
            return

        inner = fetcher.stream(uri, auth=auth, zoom=zoom)
        try:
            async for item in inner:
                # Central zoom application for the streaming path, mirroring
                # BaseFetcher.fetch(): expand to the requested depth, then
                # trim past each kind's budget. Both halves are pure and
                # idempotent, so connectors that already decomposed are
                # unaffected.
                if zoom is not None:
                    item = prune_result(decompose_result(item, zoom), zoom)
                yield _merge_tags(item, tags)
        finally:
            # Deterministically close the connector's generator when the
            # consumer stops iterating: unbounded sources hold real
            # resources (file handles, broker consumers) that must not
            # wait for garbage collection.
            await inner.aclose()

    def _resolve(self, uri: str):
        """Instantiate a fresh fetcher for ``uri``, or ``None`` if unrouted."""
        fetcher_class = self._registry.resolve(uri)
        if fetcher_class is None:
            return None
        return fetcher_class()

    @staticmethod
    def _not_found(uri: str):
        """Build the typed NOT_FOUND error for an unrouted URI."""
        return error(
            kind=ErrorKind.NOT_FOUND,
            message="no source handles this uri",
            locator=uri,
        )


def _merge_tags(
    result: Result,
    tags: Optional[Sequence[str]],
) -> Result:
    """Merge caller tags into a result's root node metadata (on a copy).

    Returns ``result`` unchanged when there is no node to tag (an ``Error``)
    or when no tags were supplied. Otherwise returns a new ``Result`` of the
    same arm whose root node carries the union of its existing tags and the
    caller's, deduplicated by the contract's own tag validator. The original
    result and tree are never mutated.
    """
    if not tags:
        return result
    if isinstance(result, Success):
        return result.model_copy(update={"tree": _with_tags(result.tree, tags)})
    if isinstance(result, Partial):
        return result.model_copy(update={"tree": _with_tags(result.tree, tags)})
    return result


def _with_tags(
    node: CompositionNode,
    tags: Sequence[str],
) -> CompositionNode:
    """Return a copy of ``node`` whose metadata tags include ``tags``.

    The new tag list is the node's existing tags followed by the caller's.
    The merged metadata is re-validated through the ``Metadata`` constructor
    so the contract's own tag handling -- the ``_dedupe_tags`` validator that
    drops blanks and deduplicates, preserving first-seen order -- defines the
    final set (``model_copy`` alone would bypass that validator).
    """
    fields = node.metadata.model_dump()
    fields["tags"] = [*node.metadata.tags, *tags]
    new_metadata = type(node.metadata)(**fields)
    return node.model_copy(update={"metadata": new_metadata})
