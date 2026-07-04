"""Shared node-mapping helpers for OmniFetcher v1 connectors.

Connectors implement only ``stream()`` (see ``fetcher.py``); to keep the
*output contract* uniform across every source, they should not hand-assemble
``CompositionNode`` / ``Metadata`` ad hoc. This module is the single
ergonomic seam they go through to emit canonical nodes consistently:

- :func:`build_node` makes a ``CompositionNode`` with a given advisory
  semantic ``kind``, attaches content atoms, populates the ``Metadata`` common
  core (id, timestamps, author, source url, permissions), and places
  source-specific descriptive fields into the namespaced ``source_extra``
  mapping -- never inline on atoms.
- :class:`SequenceCounter` allocates the monotonic ``sequence`` numbers a
  streamed source stamps onto each emitted node. It is a *per-stream* counter
  the connector owns, not global state -- the stateless core holds no cursors
  (see ``PHILOSOPHY.md`` sections 6 and 11).
- :func:`stamp_temporal` writes the temporal-ordering position (wall-clock
  ``timestamp`` + monotonic ``sequence``) onto a node's metadata so the host
  can reconstruct the stream's order line.

Everything here is deterministic except where a real wall-clock timestamp is
explicitly requested -- and timestamps only ever live in ``Metadata``, never
on an atom (atoms are content-only; see ``atoms.py``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from omni_fetcher.v1.metadata import (
    Metadata,
    Permissions,
    TemporalPosition,
)
from omni_fetcher.v1.node import CompositionNode, NodeChild


class SequenceCounter:
    """
    A per-stream monotonic sequence allocator
    ===============================================
    Hands out the strictly increasing ``sequence`` numbers a streamed source
    stamps onto each emitted node so the host can reconstruct the stream's
    temporal "order line". The counter is owned by the connector for the
    lifetime of one ``stream()`` call and discarded afterwards -- it is *not*
    global or shared state, keeping the core stateless across calls (see
    ``PHILOSOPHY.md`` sections 6 and 11).
    ===============================================
    NOTE:
        1. Allocation is deterministic: starting from ``start`` (default 0),
           successive :meth:`next` calls return ``start``, ``start + 1``, ...
        2. Not thread-safe by design; a single stream is consumed
           sequentially by one host loop.

    Attributes
    ----------
        start:
            The first sequence value :meth:`next` will return.

    Methods
    -------
        next:
        peek:
        reset:
    """

    def __init__(self, start: int = 0) -> None:
        """
        Create a per-stream sequence counter

        Parameters
        ----------
            start:
                The first sequence value to hand out (default ``0``).
        """
        self.start = start
        self._next = start

    def next(self) -> int:
        """
        Allocate and return the next monotonic sequence number

        Return
        ------
            sequence:
                The next strictly increasing sequence value for this stream.
        """
        value = self._next
        self._next += 1
        return value

    def peek(self) -> int:
        """
        Report the sequence value the next :meth:`next` call will return

        Return
        ------
            sequence:
                The upcoming sequence value, without consuming it.
        """
        return self._next

    def reset(self) -> None:
        """
        Reset the counter back to its starting value

        Return
        ------
            none:
                Nothing; the counter is reset in place.
        """
        self._next = self.start


def _build_metadata(
    *,
    kind: str,
    id: Optional[str],
    created: Optional[datetime],
    updated: Optional[datetime],
    author: Optional[str],
    source_url: Optional[str],
    permissions: Optional[Permissions],
    temporal: Optional[TemporalPosition],
    source_extra: Optional[dict[str, dict[str, Any]]],
) -> Metadata:
    """Assemble a canonical ``Metadata`` core from common-core fields."""
    return Metadata(
        id=id,
        kind=kind,
        created=created,
        updated=updated,
        author=author,
        source_url=source_url,
        permissions=permissions,
        temporal=temporal if temporal is not None else TemporalPosition(),
        source_extra=dict(source_extra) if source_extra else {},
    )


def build_metadata(
    *,
    kind: str = "unknown",
    id: Optional[str] = None,
    created: Optional[datetime] = None,
    updated: Optional[datetime] = None,
    author: Optional[str] = None,
    source_url: Optional[str] = None,
    permissions: Optional[Permissions] = None,
    temporal: Optional[TemporalPosition] = None,
    source_namespace: Optional[str] = None,
    source_fields: Optional[dict[str, Any]] = None,
    source_extra: Optional[dict[str, dict[str, Any]]] = None,
) -> Metadata:
    """
    Build a canonical metadata core for a node

    Populates the stable common core (advisory ``kind``, identity,
    timestamps, author, source url, permissions, temporal position) and
    namespaces any source-specific descriptive fields under ``source_extra``.
    Source-specific fields are passed either as a ``source_namespace`` +
    ``source_fields`` pair (the common single-source case) or as a fully
    formed ``source_extra`` mapping; the two may be combined.

    NOTE:
        1. Descriptive source data belongs here, never inline on an atom
           (atoms are content-only; see ``atoms.py``).
        2. ``source_extra`` keys must be non-empty source names; this is
           enforced by ``Metadata``.

    Parameters
    ----------
        kind:
            Advisory semantic label for the node (e.g. ``"issue"``).
        id:
            Stable identifier for the artifact, if known.
        created:
            Creation timestamp of the source artifact, if known.
        updated:
            Last-updated timestamp of the source artifact, if known.
        author:
            Author/owner of the source artifact, if known.
        source_url:
            Canonical source URL of the artifact, if known.
        permissions:
            Access/permission descriptor for the node, if known.
        temporal:
            Temporal ordering position; defaults to an empty position.
        source_namespace:
            Source namespace key (e.g. ``"github"``) for ``source_fields``.
        source_fields:
            Source-specific descriptive fields stored under
            ``source_namespace``.
        source_extra:
            A pre-built namespaced mapping, merged with the namespace/fields
            pair above.

    Return
    ------
        metadata:
            A canonical ``Metadata`` core.
    """
    merged_extra: dict[str, dict[str, Any]] = {}
    if source_extra:
        for namespace, fields in source_extra.items():
            merged_extra[namespace] = dict(fields)
    if source_namespace is not None:
        if not source_namespace.strip():
            raise ValueError("source_namespace must be a non-empty name")
        existing = merged_extra.get(source_namespace, {})
        merged_extra[source_namespace] = {
            **existing,
            **(source_fields or {}),
        }
    return _build_metadata(
        kind=kind,
        id=id,
        created=created,
        updated=updated,
        author=author,
        source_url=source_url,
        permissions=permissions,
        temporal=temporal,
        source_extra=merged_extra,
    )


def build_node(
    *,
    kind: str = "unknown",
    atoms: Optional[Iterable[NodeChild]] = None,
    children: Optional[Iterable[CompositionNode]] = None,
    id: Optional[str] = None,
    created: Optional[datetime] = None,
    updated: Optional[datetime] = None,
    author: Optional[str] = None,
    source_url: Optional[str] = None,
    permissions: Optional[Permissions] = None,
    temporal: Optional[TemporalPosition] = None,
    source_namespace: Optional[str] = None,
    source_fields: Optional[dict[str, Any]] = None,
    source_extra: Optional[dict[str, dict[str, Any]]] = None,
) -> CompositionNode:
    """
    Build a canonical composition node

    The single ergonomic builder a connector uses to emit a node
    consistently: it sets the advisory semantic ``kind``, attaches content
    ``atoms`` and/or child ``children`` (in that order), and populates the
    metadata common core, placing source-specific descriptive data into the
    namespaced ``source_extra`` mapping.

    NOTE:
        1. ``atoms`` and ``children`` are both ordered children of the node;
           atoms are appended first, then child nodes.
        2. Content lives in atoms; description lives in metadata. This
           builder never puts descriptive fields onto atoms.

    Parameters
    ----------
        kind:
            Advisory semantic label for the node.
        atoms:
            Ordered content atoms to attach as leaf children.
        children:
            Ordered child composition nodes to attach after the atoms.
        id:
            Stable identifier for the artifact, if known.
        created:
            Creation timestamp of the source artifact, if known.
        updated:
            Last-updated timestamp of the source artifact, if known.
        author:
            Author/owner of the source artifact, if known.
        source_url:
            Canonical source URL of the artifact, if known.
        permissions:
            Access/permission descriptor for the node, if known.
        temporal:
            Temporal ordering position; defaults to an empty position.
        source_namespace:
            Source namespace key for ``source_fields``.
        source_fields:
            Source-specific descriptive fields stored under
            ``source_namespace``.
        source_extra:
            A pre-built namespaced mapping, merged with the pair above.

    Return
    ------
        node:
            A canonical ``CompositionNode``.
    """
    metadata = build_metadata(
        kind=kind,
        id=id,
        created=created,
        updated=updated,
        author=author,
        source_url=source_url,
        permissions=permissions,
        temporal=temporal,
        source_namespace=source_namespace,
        source_fields=source_fields,
        source_extra=source_extra,
    )
    ordered: list[NodeChild] = []
    if atoms is not None:
        ordered.extend(atoms)
    if children is not None:
        ordered.extend(children)
    return CompositionNode(metadata=metadata, children=ordered)


def now_utc() -> datetime:
    """
    Return the current wall-clock time as a timezone-aware UTC datetime

    The one deliberately non-deterministic helper: a connector calls this to
    stamp a real event timestamp into a node's metadata. Timestamps live in
    metadata only, never on an atom.

    Return
    ------
        now:
            The current time, in UTC.
    """
    return datetime.now(timezone.utc)


def stamp_temporal(
    node: CompositionNode,
    *,
    sequence: int,
    timestamp: Optional[datetime] = None,
) -> CompositionNode:
    """
    Stamp temporal ordering position onto a streamed node's metadata

    Writes the monotonic ``sequence`` (typically from a
    :class:`SequenceCounter`) and an optional wall-clock ``timestamp`` into
    the node's ``Metadata.temporal`` so the host can reconstruct the stream's
    order line. Mutates the node in place and returns it for chaining.

    NOTE:
        1. The ``sequence`` is host-monotonic and per-stream; OmniFetcher
           holds no cross-call state (see ``PHILOSOPHY.md`` section 6).
        2. ``timestamp`` is wall-clock and may be supplied (deterministic in
           tests) or sourced from :func:`now_utc`.

    Parameters
    ----------
        node:
            The node to stamp.
        sequence:
            The monotonic sequence number for this node within the stream.
        timestamp:
            Optional wall-clock timestamp to record; left unset when
            ``None``.

    Return
    ------
        node:
            The same node, with temporal position stamped, for chaining.
    """
    node.metadata.temporal = TemporalPosition(
        timestamp=timestamp,
        sequence=sequence,
    )
    return node
