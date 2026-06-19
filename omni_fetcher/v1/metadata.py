"""The typed metadata channel for the OmniFetcher v1 contract.

Content and description are separate channels. Atoms (see ``atoms.py``)
carry content only; everything that *describes* an artifact lives here, on
the ``Metadata`` attached to every ``CompositionNode``.

The metadata core is a small, stable, typed set of common fields. The long
tail of source-specific descriptive data lives under the namespaced
``source_extra`` mapping, keyed by source namespace (e.g. ``"github"``,
``"notion"``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class TemporalPosition(BaseModel):
    """
    Temporal ordering position for a node
    ===============================================
    Carries the ordering information needed to reconstruct a stream's
    "order line" without OmniFetcher holding any state. A wall-clock
    ``timestamp`` plus a host-monotonic ``sequence`` together preserve
    temporal order of streamed/event nodes (see ``PHILOSOPHY.md`` section 6).
    ===============================================
    NOTE:
        1. The core is stateless; the monotonic sequence is assigned per
           emitted node so the host can accumulate the order line.

    Attributes
    ----------
        timestamp:
            Wall-clock time associated with the node, if known.
        sequence:
            Monotonically increasing sequence number within a stream, if
            applicable.
    """

    timestamp: Optional[datetime] = None
    sequence: Optional[int] = None


class Permissions(BaseModel):
    """
    Permission descriptor for a node
    ===============================================
    Records the access context under which the node was procured. The
    contract is permission-faithful and never escalates (see
    ``PHILOSOPHY.md`` section 7); this is a descriptive record, not an
    enforcement mechanism.
    ===============================================
    NOTE:
        1. This is intentionally minimal at v1; richer ACL modelling is
           additive and deferred.

    Attributes
    ----------
        visibility:
            Advisory visibility label (e.g. ``"public"``, ``"private"``,
            ``"internal"``), if known.
        roles:
            Advisory list of roles/principals with access, if known.
    """

    visibility: Optional[str] = None
    roles: list[str] = Field(default_factory=list)


class Metadata(BaseModel):
    """
    The typed metadata core attached to every composition node
    ===============================================
    The stable, typed channel that *describes* a node's content. Carries a
    common core (identity, timestamps, author, source url, permissions,
    temporal position, reserved hash fields, advisory semantic kind) plus a
    namespaced ``source_extra`` mapping for the long tail of source-specific
    descriptive fields.
    ===============================================
    NOTE:
        1. ``content_hash`` and ``prev_hash`` are RESERVED for future
           content-addressed hash-linking (Merkle integrity). They are
           unpopulated at v1; no verification logic ships. See
           ``PHILOSOPHY.md`` section 6.
        2. ``kind`` is an ADVISORY semantic label (e.g. ``"issue"``,
           ``"page"``, ``"message"``, ``"file"``), never a distinct type.
        3. ``source_extra`` is keyed by source namespace; values are
           validated per source by the owning connector, not here. This
           class only enforces the *namespacing* invariant: top-level keys
           are non-empty source names mapping to a dict of fields.
        4. ``tags`` are free-form advisory labels. They aggregate up a tree:
           a composite node's effective tag set is the union of its own
           ``tags`` and its descendants' (see ``CompositionNode``).

    Attributes
    ----------
        id:
            Stable identifier for the artifact this node represents.
        kind:
            Advisory semantic label for the node (default ``"unknown"``).
        tags:
            Free-form advisory labels for the node. Deduplicated, order
            preserved.
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
            Temporal ordering position (timestamp + monotonic sequence).
        content_hash:
            RESERVED. Merkle hash over the node's children; unpopulated at
            v1.
        prev_hash:
            RESERVED. Hash link to the previous event node; unpopulated at
            v1.
        source_extra:
            Namespaced mapping of source-specific descriptive data, keyed by
            source namespace.
    """

    id: Optional[str] = None
    kind: str = "unknown"
    tags: list[str] = Field(default_factory=list)

    created: Optional[datetime] = None
    updated: Optional[datetime] = None
    author: Optional[str] = None
    source_url: Optional[str] = None

    permissions: Optional[Permissions] = None
    temporal: TemporalPosition = Field(default_factory=TemporalPosition)

    # Reserved for future content-addressed integrity (Merkle). Unpopulated
    # at v1; no verification ships.
    content_hash: Optional[str] = None
    prev_hash: Optional[str] = None

    # Namespaced long-tail descriptive data, keyed by source namespace.
    source_extra: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("tags")
    @classmethod
    def _dedupe_tags(cls, value: list[str]) -> list[str]:
        """Drop blank tags and deduplicate, preserving first-seen order."""
        seen: set[str] = set()
        deduped: list[str] = []
        for tag in value:
            if not tag or not tag.strip():
                continue
            if tag in seen:
                continue
            seen.add(tag)
            deduped.append(tag)
        return deduped

    @field_validator("source_extra")
    @classmethod
    def _validate_namespaced(
        cls, value: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Require non-empty source names mapping to field dicts."""
        for namespace, fields in value.items():
            if not isinstance(namespace, str) or not namespace.strip():
                raise ValueError(
                    "source_extra keys must be non-empty source names"
                )
            if not isinstance(fields, dict):
                raise ValueError(
                    f"source_extra['{namespace}'] must be a dict of fields, "
                    f"got {type(fields).__name__}"
                )
        return value
