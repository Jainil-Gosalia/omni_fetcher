"""The fetcher interface for the OmniFetcher v1 contract.

``stream()`` is the primitive: an async iterator of ``Result`` items. A
bounded source yields a terminating stream; an unbounded source does not.
Connectors implement only ``stream()``.

``fetch()`` is base-provided sugar: it collects a bounded stream into the
final tree and returns a single ``Result``. Single-document consumers never
have to iterate a stream.

Collection semantics for ``fetch()`` (the documented contract):

- It consumes ``stream()`` to completion. ``fetch()`` is for *bounded*
  sources only; an unbounded stream never terminates and ``fetch()`` would
  conceptually never return. Unbounded sources are consumed via ``stream()``.
- An empty stream (no items) yields ``error(NOT_FOUND)`` -- a fetch that
  produced nothing is a failure, never a silent empty success.
- ``Error`` items are never dropped (the philosophy forbids silently
  swallowing failures). If the stream yields *only* errors, the first error
  is surfaced as the result. If errors are mixed with node-bearing results,
  the result is a ``Partial`` whose ``gaps`` capture every error (and every
  upstream partial's gaps), so the caller sees both what was built and what
  failed.
- A single terminal node-bearing item is returned as-is: a bounded source
  that yields exactly one ``Success`` has ``fetch() == that Success`` (and a
  lone ``Partial`` is returned unchanged). This is the common single-document
  case.
- Multiple node-bearing items are assembled under one synthetic root
  ``CompositionNode`` (advisory ``kind`` ``"collection"``) whose children are
  the yielded trees in stream order. The result is a ``Success`` when no gaps
  were collected, otherwise a ``Partial`` aggregating them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.decompose import decompose_result
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.metadata import Metadata
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
from omni_fetcher.v1.zoom import ZoomSpec, prune_result

# Advisory ``kind`` for the synthetic root that wraps multiple streamed trees
# when ``fetch()`` collects a multi-item bounded stream.
COLLECTION_KIND = "collection"


class BaseFetcher(ABC):
    """
    Abstract base class for all v1 connectors
    ===============================================
    Defines the streaming primitive every connector implements and provides
    the concrete ``fetch()`` sugar that collects a bounded stream into the
    final tree. Connectors override ``stream()`` only.
    ===============================================
    NOTE:
        1. Expected failures are returned as ``Result`` values, never raised.
        2. Credentials are passed per call via ``auth`` and used
           transiently; nothing is stored on the instance.

    Methods
    -------
        stream:
        fetch:
    """

    @abstractmethod
    def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream canonical results for a URI (the primitive)

        Parameters
        ----------
            uri:
                The source URI to fetch.
            auth:
                The per-call credential, or ``None`` for unauthenticated
                sources.
            zoom:
                Optional per-atom-type zoom spec; ``None`` means natural
                granularity.

        Return
        ------
            results:
                An async iterator of ``Result`` items. Terminates for
                bounded sources; does not for unbounded ones.
        """
        raise NotImplementedError

    async def fetch(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> Result:
        """
        Fetch a bounded source into a single result (sugar)

        Collects the bounded stream produced by ``stream()`` into the final
        composition tree and returns one ``Result``. See the module docstring
        for the full collection semantics. In brief: an empty stream is a
        ``NOT_FOUND`` error; errors are never dropped (surfaced as an
        ``Error`` alone, or folded into a ``Partial`` alongside built trees);
        a single node-bearing item is returned as-is; multiple node-bearing
        items are assembled under one synthetic ``"collection"`` root.

        NOTE:
            1. This is for *bounded* sources only. An unbounded ``stream()``
               never terminates, so ``fetch()`` would never return -- callers
               of unbounded sources must iterate ``stream()`` instead.

        Parameters
        ----------
            uri:
                The source URI to fetch.
            auth:
                The per-call credential, or ``None``.
            zoom:
                Optional per-atom-type zoom spec; ``None`` means natural
                granularity.

        Return
        ------
            result:
                A single ``Result`` collecting the bounded stream's items.
        """
        trees: list[CompositionNode] = []
        gaps: list[Gap] = []
        first_error: Optional[Error] = None

        # Central zoom application: the collected tree is decomposed and then
        # pruned to the spec below, so connectors that ignore ``zoom`` still
        # honor both finer- and coarser-than-natural requests. Both halves are
        # pure and idempotent, so a connector that already decomposed to the
        # spec is unaffected.
        async for item in self.stream(uri, auth=auth, zoom=zoom):
            if isinstance(item, Success):
                trees.append(item.tree)
            elif isinstance(item, Partial):
                trees.append(item.tree)
                gaps.extend(item.gaps)
            else:  # Error -- never dropped.
                if first_error is None:
                    first_error = item
                gaps.append(
                    gap(
                        kind=item.kind,
                        locator=item.locator,
                        detail=item.message,
                    )
                )

        result = self._collect(trees, gaps, first_error, uri)
        if zoom is None:
            return result
        # Expand first, then collapse: decomposition grows the tree to the
        # requested depth and pruning trims anything past each atom kind's
        # budget. The reverse order would trim structure that decomposition
        # was about to create.
        return prune_result(decompose_result(result, zoom), zoom)

    @staticmethod
    def _collect(
        trees: list[CompositionNode],
        gaps: list[Gap],
        first_error: Optional[Error],
        uri: str,
    ) -> Result:
        """Fold collected stream items into one final ``Result``."""
        if not trees:
            # Nothing was built. Surface the first real error if one
            # occurred, else the stream was simply empty.
            if first_error is not None:
                return first_error
            return error(
                kind=ErrorKind.NOT_FOUND,
                message="stream produced no results",
                locator=uri,
            )

        if len(trees) == 1 and not gaps:
            # Single terminal node-bearing item with no gaps: return as-is so
            # a bounded source's fetch() equals collecting its stream().
            return success(trees[0])

        if len(trees) == 1:
            root = trees[0]
        else:
            # Assemble multiple trees under one synthetic collection root.
            root = CompositionNode(
                metadata=Metadata(kind=COLLECTION_KIND, source_url=uri),
                children=list(trees),
            )

        if gaps:
            return partial(root, gaps)
        return success(root)

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether this fetcher can handle a URI

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` if this fetcher claims ``uri``.
        """
        return False
