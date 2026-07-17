"""Per-atom-type zoom specification and resolver for the v1 contract.

Zoom is **depth in the semantic composition tree** -- how far to expand the
source's natural structure -- selectable **per atom type** (e.g. text at
sentence level while images stay whole). It is *not* token or character
windowing.

A ``ZoomSpec`` maps atom types to a ``DepthLevel``. Unspecified types
default to the source's natural granularity. This module also provides the
**pure zoom resolver**: deterministic, side-effect-free functions that, given
a ``ZoomSpec`` and an atom kind (and/or a node), decide the target
``DepthLevel`` and whether the natural composition tree should be expanded
further. A companion pure function, :func:`prune_to_zoom`, limits a
``CompositionNode`` tree to the requested semantic depths and returns a new
tree.

All of this is *semantic*: it only ever expands or collapses the source's
natural composition structure. It never slices content into arbitrary
token/character windows -- that is the consumer's concern, not OmniFetcher's
(see ``PHILOSOPHY.md`` sections 5 and 8).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.node import CompositionNode, NodeChild
from omni_fetcher.v1.result import Partial, Result, Success, partial, success


class DepthLevel(str, Enum):
    """
    Semantic decomposition depth vocabulary
    ===============================================
    The ordered vocabulary describing how deeply to decompose a given atom
    type's natural structure. These are SEMANTIC tree depths, not
    token/character windows.
    ===============================================
    NOTE:
        1. Not every level is meaningful for every atom type; the zoom
           resolver reconciles a requested level against a node's natural
           structure.
        2. The vocabulary evolves additively.

    Attributes
    ----------
        NATURAL:
            The source's natural top-level granularity (the default).
        WHOLE:
            Keep the atom whole; do not decompose further.
        SECTION:
            Decompose into sections / blocks (e.g. headings, slides).
        PARAGRAPH:
            Decompose into paragraphs (primarily for text).
        SENTENCE:
            Decompose into sentences (primarily for text).
        MAX:
            Decompose as deeply as the source meaningfully allows.
    """

    NATURAL = "natural"
    WHOLE = "whole"
    SECTION = "section"
    PARAGRAPH = "paragraph"
    SENTENCE = "sentence"
    MAX = "max"


# Ordinal depth budget for each explicit, source-independent level. NATURAL
# is deliberately absent: it is a sentinel meaning "let the source's own
# structure decide", so it has no fixed structural depth. The values are an
# ordering only -- coarser levels expand fewer tree layers than finer ones.
# The vocabulary (and thus this map) evolves additively.
_DEPTH_BUDGET: dict[DepthLevel, int] = {
    DepthLevel.WHOLE: 0,
    DepthLevel.SECTION: 1,
    DepthLevel.PARAGRAPH: 2,
    DepthLevel.SENTENCE: 3,
    DepthLevel.MAX: 1_000_000,
}


class ZoomSpec(BaseModel):
    """
    Per-atom-type zoom specification
    ===============================================
    A consumer-supplied request for decomposition depth, selectable per atom
    type. Atom types absent from ``per_type`` fall back to ``default``, which
    itself defaults to the source's natural granularity.
    ===============================================
    NOTE:
        1. Zoom is semantic tree depth, never token/character windowing.
        2. An empty ``ZoomSpec`` means "use natural granularity everywhere".

    Attributes
    ----------
        default:
            The depth applied to atom types not present in ``per_type``.
        per_type:
            Mapping of atom type to the depth to apply for that type.

    Methods
    -------
        level_for:
    """

    model_config = {"frozen": True}

    default: DepthLevel = DepthLevel.NATURAL
    per_type: dict[AtomKind, DepthLevel] = Field(default_factory=dict)

    def level_for(self, atom_kind: AtomKind) -> DepthLevel:
        """
        Resolve the requested depth level for an atom type

        Parameters
        ----------
            atom_kind:
                The atom type to resolve a depth level for.

        Return
        ------
            level:
                The per-type depth if specified, else ``default``.
        """
        return self.per_type.get(atom_kind, self.default)


def parse_zoom_spec(zoom: str | None) -> ZoomSpec | None:
    """
    Parse a ``text=paragraph,image=whole`` string into a ``ZoomSpec``

    The one textual zoom syntax, shared by every host surface (the CLI and
    the MCP server) so a user learns it once. Comma-separated
    ``<atom-kind>=<depth-level>`` pairs; an empty or ``None`` input means
    "natural granularity everywhere" and returns ``None``.

    Raises a plain ``ValueError`` (naming the bad entry and the valid kinds
    and levels) rather than any framework-specific error, so each caller can
    surface it in its own idiom -- the CLI wraps it as a ``typer`` parameter
    error, the MCP server maps it onto ``Error(INVALID_INPUT)``.

    Parameters
    ----------
        zoom:
            The zoom string, or ``None`` / empty for natural granularity.

    Return
    ------
        spec:
            The parsed ``ZoomSpec``, or ``None`` when no zoom was requested.
    """
    if not zoom:
        return None
    per_type: dict[AtomKind, DepthLevel] = {}
    for pair in zoom.split(","):
        key, _, value = pair.partition("=")
        try:
            per_type[AtomKind(key.strip())] = DepthLevel(value.strip())
        except ValueError as exc:
            raise ValueError(
                f"bad zoom entry {pair!r}: expected <atom-kind>=<depth-level> "
                f"(kinds: {', '.join(k.value for k in AtomKind)}; levels: "
                f"{', '.join(level.value for level in DepthLevel)})"
            ) from exc
    return ZoomSpec(per_type=per_type)


def resolve_level(
    spec: ZoomSpec,
    atom_kind: AtomKind,
    node: CompositionNode | None = None,
) -> DepthLevel:
    """
    Resolve the target depth level for an atom kind under a spec

    The pure, deterministic entry point of the zoom resolver. It delegates
    per-atom-type resolution to ``spec.level_for`` so that an unspecified
    atom type falls back to ``spec.default`` (which itself defaults to
    ``DepthLevel.NATURAL`` -- the source's natural granularity). The
    optional ``node`` is accepted so callers may resolve "in context" of a
    concrete subtree; the resolution is structural/semantic and never
    inspects content for token/character windowing.

    Parameters
    ----------
        spec:
            The consumer-supplied per-atom-type zoom specification.
        atom_kind:
            The atom type whose target depth is being resolved.
        node:
            Optional composition node providing context; unused for the
            spec-driven decision but reserved for node-aware resolution.

    Return
    ------
        level:
            The resolved ``DepthLevel`` for ``atom_kind``.
    """
    del node  # Reserved for node-aware resolution; decision is spec-driven.
    return spec.level_for(atom_kind)


def is_natural(spec: ZoomSpec, atom_kind: AtomKind) -> bool:
    """
    Report whether an atom kind resolves to natural granularity

    Parameters
    ----------
        spec:
            The consumer-supplied per-atom-type zoom specification.
        atom_kind:
            The atom type to test.

    Return
    ------
        natural:
            ``True`` when the resolved level is ``DepthLevel.NATURAL`` (the
            source decides the granularity), else ``False``.
    """
    return resolve_level(spec, atom_kind) is DepthLevel.NATURAL


def should_expand(
    spec: ZoomSpec,
    atom_kind: AtomKind,
    current_depth: int,
) -> bool:
    """
    Decide whether to expand the tree further for an atom kind

    A pure predicate a connector can consult while growing the natural
    composition tree. ``current_depth`` is the number of semantic layers
    already expanded below the atom kind's top-level node (0 = the atom kept
    whole). The decision is by SEMANTIC tree depth only:

    - ``NATURAL`` never forces expansion: the source emits its own natural
      structure, so the resolver yields to it (returns ``False``).
    - ``WHOLE`` keeps the atom whole (expands no layers).
    - The finer levels (``SECTION`` < ``PARAGRAPH`` < ``SENTENCE``) expand
      while fewer layers have been realised than the level's budget.
    - ``MAX`` expands as deeply as the source meaningfully allows.

    Parameters
    ----------
        spec:
            The consumer-supplied per-atom-type zoom specification.
        atom_kind:
            The atom type being decomposed.
        current_depth:
            Count of semantic layers already expanded (must be ``>= 0``).

    Return
    ------
        expand:
            ``True`` if another semantic layer should be expanded, else
            ``False``.
    """
    if current_depth < 0:
        raise ValueError("current_depth must be non-negative")
    level = resolve_level(spec, atom_kind)
    if level is DepthLevel.NATURAL:
        return False
    return current_depth < _DEPTH_BUDGET[level]


def target_depth(spec: ZoomSpec, atom_kind: AtomKind) -> int | None:
    """
    Report the target semantic depth budget for an atom kind

    Parameters
    ----------
        spec:
            The consumer-supplied per-atom-type zoom specification.
        atom_kind:
            The atom type whose target depth is being reported.

    Return
    ------
        depth:
            The number of semantic layers to expand, or ``None`` when the
            resolved level is ``DepthLevel.NATURAL`` (the source decides and
            no fixed depth applies).
    """
    level = resolve_level(spec, atom_kind)
    if level is DepthLevel.NATURAL:
        return None
    return _DEPTH_BUDGET[level]


def _child_kind(child: NodeChild) -> AtomKind | None:
    """Return the atom kind of a leaf child, or ``None`` for a node."""
    if isinstance(child, CompositionNode):
        return None
    return child.kind


def _prune_node(
    node: CompositionNode,
    spec: ZoomSpec,
    depth: int,
) -> CompositionNode:
    """Recursively rebuild ``node`` limited to the spec's semantic depths."""
    new_children: list[NodeChild] = []
    for child in node.children:
        if isinstance(child, CompositionNode):
            new_children.append(_prune_node(child, spec, depth + 1))
            continue
        # Leaf atom: a node deeper than the atom type's target budget is a
        # finer-grained decomposition than requested, so the leaf is kept
        # but no deeper structure is introduced. Pruning only collapses the
        # existing semantic tree; it never windows content.
        new_children.append(child)
    pruned = CompositionNode(
        metadata=node.metadata,
        children=new_children,
    )
    return _collapse_over_budget(pruned, spec, depth)


def _collapse_over_budget(
    node: CompositionNode,
    spec: ZoomSpec,
    depth: int,
) -> CompositionNode:
    """Collapse child nodes that exceed their atom kind's depth budget."""
    kept: list[NodeChild] = []
    for child in node.children:
        kind = _child_kind(child)
        if isinstance(child, CompositionNode):
            kept.append(child)
            continue
        budget = target_depth(spec, kind) if kind is not None else None
        if budget is not None and depth > budget:
            # Beyond the requested semantic depth for this atom kind: the
            # leaf is collapsed away at this level (its parent already
            # carries the coarser whole). Never windowed.
            continue
        kept.append(child)
    return CompositionNode(metadata=node.metadata, children=kept)


def prune_to_zoom(
    node: CompositionNode,
    spec: ZoomSpec,
) -> CompositionNode:
    """
    Limit a composition tree to a spec's per-atom-type semantic depths

    A pure, deterministic transform: given a fully-decomposed natural tree
    and a ``ZoomSpec``, it returns a NEW ``CompositionNode`` collapsed to the
    requested depth per atom type. It only ever collapses the existing
    semantic structure -- it never splits content into token/character
    windows, and it never mutates the input. Atom kinds resolving to
    ``DepthLevel.NATURAL`` are left exactly as the source emitted them.

    Parameters
    ----------
        node:
            The natural composition tree to limit (left unmutated).
        spec:
            The consumer-supplied per-atom-type zoom specification.

    Return
    ------
        pruned:
            A new ``CompositionNode`` collapsed to the requested semantic
            depths.
    """
    return _prune_node(node, spec, 0)


def prune_result(result: Result, spec: ZoomSpec) -> Result:
    """
    Apply ``prune_to_zoom`` to a ``Result``'s tree, preserving its state

    A pure transform over the ``Result`` envelope: ``Success`` and
    ``Partial`` come back with their tree pruned to the spec (gaps carried
    over unchanged); an ``Error`` is returned as-is. The input is never
    mutated.

    Parameters
    ----------
        result:
            The result whose tree (if any) should be limited.
        spec:
            The consumer-supplied per-atom-type zoom specification.

    Return
    ------
        pruned:
            A new ``Result`` of the same state with a pruned tree, or the
            original ``Error``.
    """
    if isinstance(result, Success):
        return success(prune_to_zoom(result.tree, spec))
    if isinstance(result, Partial):
        return partial(prune_to_zoom(result.tree, spec), list(result.gaps))
    return result
