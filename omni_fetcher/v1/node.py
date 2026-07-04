"""The canonical composition tree node for the OmniFetcher v1 contract.

``CompositionNode`` is the single structural output type for every source. A
node is a recursive tree: its ``children`` are other ``CompositionNode``s
and/or canonical atoms, and it always carries a typed ``Metadata``.

A node's semantic meaning ("this is an issue / page / message / file") is an
advisory ``kind`` string on its ``Metadata`` -- never a distinct type. You
write ingestion code once against this shape and it works for every source.
"""

from __future__ import annotations

import base64
import hashlib
import json
from enum import Enum
from typing import Any, Iterator, Union

from pydantic import BaseModel, Field

from omni_fetcher.v1.atoms import (
    Atom,
    Audio,
    AtomKind,
    Image,
    Table,
    Text,
    Video,
)
from omni_fetcher.v1.metadata import Metadata

# A child of a node is either another node or a canonical atom. ``Atom`` is
# spelled out as the explicit union (rather than imported ``AnyAtom``) so the
# forward reference to ``CompositionNode`` resolves cleanly under Pydantic v2.
NodeChild = Union[
    "CompositionNode",
    Text,
    Image,
    Audio,
    Video,
    Table,
]


class CompositionNode(BaseModel):
    """
    A node in the canonical composition tree
    ===============================================
    The single structural output type for every source. Recursive: a node
    has zero or more ``children`` that are themselves nodes and/or canonical
    atoms, plus an attached typed ``Metadata`` describing it. Semantic
    meaning is an advisory ``kind`` string on the metadata, not a type.
    ===============================================
    NOTE:
        1. A node with atom children and no node children is the common
           "leaf-bearing" case (e.g. a page whose content is text/images).
        2. Tree depth is the unit of zoom (see ``zoom.py``): it is semantic
           decomposition depth, not token/character windowing.
        3. ``content_hash`` population is opt-in via ``populate_hashes`` /
           ``compute_content_hash``; it is not auto-run on construction so
           large binary content is not hashed unless the caller asks.

    Attributes
    ----------
        metadata:
            The typed metadata describing this node.
        children:
            Ordered child nodes and/or canonical atoms.

    Methods
    -------
        iter_children:
        iter_descendants:
        iter_atoms:
        find_by_kind:
        find_atoms:
        merged_tags:
        compute_content_hash:
        populate_hashes:
    """

    metadata: Metadata = Field(default_factory=Metadata)
    children: list[NodeChild] = Field(default_factory=list)

    def iter_children(self) -> Iterator[NodeChild]:
        """
        Iterate this node's direct children (nodes and atoms) in order.

        Return
        ------
            children:
                The node's immediate children, in declared order.
        """
        yield from self.children

    def iter_descendants(self) -> Iterator["CompositionNode"]:
        """
        Iterate all descendant nodes (not atoms), depth-first, pre-order.

        Return
        ------
            descendants:
                Every ``CompositionNode`` below this node, excluding self.
        """
        for child in self.children:
            if isinstance(child, CompositionNode):
                yield child
                yield from child.iter_descendants()

    def iter_atoms(self) -> Iterator[Atom]:
        """
        Iterate every atom leaf in the subtree, depth-first, in order.

        Return
        ------
            atoms:
                All atom leaves under this node, in document order.
        """
        for child in self.children:
            if isinstance(child, CompositionNode):
                yield from child.iter_atoms()
            else:
                yield child

    def find_by_kind(self, kind: str) -> list["CompositionNode"]:
        """
        Collect descendant nodes whose advisory metadata kind matches.

        Parameters
        ----------
            kind:
                The advisory semantic ``Metadata.kind`` label to match.

        Return
        ------
            nodes:
                Descendant nodes (and self, if it matches) with that kind.
        """
        matches: list[CompositionNode] = []
        if self.metadata.kind == kind:
            matches.append(self)
        for node in self.iter_descendants():
            if node.metadata.kind == kind:
                matches.append(node)
        return matches

    def find_atoms(self, kind: AtomKind) -> list[Atom]:
        """
        Collect every atom leaf of a given canonical atom kind.

        Parameters
        ----------
            kind:
                The canonical ``AtomKind`` to filter on.

        Return
        ------
            atoms:
                All matching atom leaves under this node, in order.
        """
        return [atom for atom in self.iter_atoms() if atom.kind == kind]

    def merged_tags(self) -> list[str]:
        """
        Aggregate this node's tags with every descendant node's tags.

        A composite node's effective tag set is the deduplicated union of
        its own ``metadata.tags`` and those of all descendant nodes, in
        first-seen depth-first order. This does not mutate any node.

        Return
        ------
            tags:
                The merged, deduplicated tag list for the subtree.
        """
        seen: set[str] = set()
        merged: list[str] = []
        nodes: list[CompositionNode] = [self, *self.iter_descendants()]
        for node in nodes:
            for tag in node.metadata.tags:
                if tag in seen:
                    continue
                seen.add(tag)
                merged.append(tag)
        return merged

    def compute_content_hash(self) -> str:
        """
        Compute a deterministic Merkle-style hash over content only.

        The hash folds in each child: an atom contributes a hash of its
        canonical content (excluding all metadata), and a child node
        contributes its own recursively computed ``content_hash``. Metadata
        is deliberately excluded so the hash is a pure content fingerprint:
        identical content yields an identical hash regardless of timestamps,
        ids, or other descriptive fields. The computation is pure and does
        not mutate the tree.

        Return
        ------
            content_hash:
                Hex-encoded SHA-256 digest over the subtree's content.
        """
        child_digests: list[str] = []
        for child in self.children:
            if isinstance(child, CompositionNode):
                child_digests.append(child.compute_content_hash())
            else:
                child_digests.append(_atom_digest(child))
        payload = json.dumps(
            {"node": child_digests},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def populate_hashes(self) -> "CompositionNode":
        """
        Populate ``metadata.content_hash`` for this node and all descendants.

        Walks the subtree bottom-up, setting each node's
        ``metadata.content_hash`` to its computed Merkle hash. ``prev_hash``
        is left untouched (reserved). Mutates the tree in place and returns
        self for chaining. Call explicitly; hashing is never auto-run on
        construction.

        Return
        ------
            node:
                This node, with hashes populated, for chaining.
        """
        for child in self.children:
            if isinstance(child, CompositionNode):
                child.populate_hashes()
        self.metadata.content_hash = self.compute_content_hash()
        return self


def _json_default(value: Any) -> str:
    """JSON fallback for non-serialisable content (raw bytes).

    Atom content may include non-UTF-8 ``bytes`` (e.g. binary image data).
    Encoding such bytes as base64 keeps the digest deterministic and
    lossless without attempting (and failing) a UTF-8 decode.
    """
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"cannot serialise {type(value).__name__} for content hashing")


def _atom_digest(atom: Atom) -> str:
    """Hex SHA-256 over an atom's canonical content (bytes-safe).

    Dumps in ``python`` mode (which keeps ``bytes`` as ``bytes`` rather than
    eagerly UTF-8-decoding them) and serialises with a base64 fallback, so
    arbitrary binary atom content hashes deterministically.
    """
    content = atom.model_dump(mode="python")
    payload = json.dumps(
        content,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
        default=_json_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Resolve the forward reference to ``CompositionNode`` inside ``NodeChild``.
CompositionNode.model_rebuild()
