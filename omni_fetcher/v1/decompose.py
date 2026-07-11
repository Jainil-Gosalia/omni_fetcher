"""Pure finer-than-natural text decomposition for the v1 contract.

Zoom's second half: where central pruning (``zoom.prune_result``) collapses a
tree to *coarser* semantic depths, this module expands ``Text`` atoms to
*finer* ones -- ``SECTION`` / ``PARAGRAPH`` / ``SENTENCE`` child nodes --
for connectors whose natural granularity is a whole document.

Everything here is pure and versioned so behaviour changes are reviewable:

- :func:`split_text` -- lossless splitting: the pieces concatenate exactly
  to the input (separators stay attached to the preceding piece). SECTION
  splits before markdown headings, PARAGRAPH on blank-line runs, SENTENCE
  (best-effort) on terminal punctuation; ``MAX`` maps to SENTENCE for text.
- :func:`decompose_node` -- rebuild a node whose ``Text`` atoms are replaced
  by per-piece child nodes when the spec requests a finer-than-natural text
  level. Atom kinds that cannot decompose (image, audio, video, table)
  record an honest ``Gap`` when a finer level was *explicitly* requested for
  them -- never a silent no-op.
- :func:`decompose_result` -- the ``Result`` wrapper: a ``Success`` that
  gains gaps becomes a ``Partial``; errors pass through untouched.

Never token/character windowing: splits follow the text's own semantic
markers, and a text with no such markers stays whole.
"""

from __future__ import annotations

import re

from omni_fetcher.v1.atoms import AtomKind, Text
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode, NodeChild
from omni_fetcher.v1.result import (
    Gap,
    Partial,
    Result,
    Success,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import DepthLevel, ZoomSpec

# Text levels that mean "decompose finer than the natural whole". MAX maps
# to the deepest text level (sentence).
_FINER_TEXT_LEVELS: frozenset[DepthLevel] = frozenset(
    {DepthLevel.SECTION, DepthLevel.PARAGRAPH, DepthLevel.SENTENCE, DepthLevel.MAX}
)

# Advisory ``kind`` for the per-piece nodes, per requested level.
_PIECE_KINDS: dict[DepthLevel, str] = {
    DepthLevel.SECTION: "section",
    DepthLevel.PARAGRAPH: "paragraph",
    DepthLevel.SENTENCE: "sentence",
    DepthLevel.MAX: "sentence",
}

# Lossless split points. Lookahead/lookbehind splits keep every character:
# the pieces always concatenate exactly to the input.
_SECTION_SPLIT = re.compile(r"(?=^#{1,6} )", re.MULTILINE)
_PARAGRAPH_SPLIT = re.compile(r"(?<=\n\n)")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Atom kinds that have no finer semantic decomposition in this module.
_UNDECOMPOSABLE: tuple[AtomKind, ...] = (
    AtomKind.IMAGE,
    AtomKind.AUDIO,
    AtomKind.VIDEO,
    AtomKind.TABLE,
)


def split_text(content: str, level: DepthLevel) -> list[str]:
    """
    Split text at a semantic level, losslessly

    Pieces always concatenate exactly to the input; separators stay
    attached to the preceding piece (SENTENCE keeps trailing whitespace on
    the sentence before it via a capturing split). A text with no split
    markers comes back as a single piece.

    Parameters
    ----------
        content:
            The text to split.
        level:
            The requested semantic level (``MAX`` behaves as ``SENTENCE``).

    Return
    ------
        pieces:
            Non-empty text pieces in document order.
    """
    if level is DepthLevel.SECTION:
        pieces = _SECTION_SPLIT.split(content)
    elif level is DepthLevel.PARAGRAPH:
        pieces = _PARAGRAPH_SPLIT.split(content)
    elif level in (DepthLevel.SENTENCE, DepthLevel.MAX):
        # Keep the inter-sentence whitespace attached to the preceding
        # sentence so concatenation is lossless.
        raw = _SENTENCE_SPLIT.split(content)
        rebuilt: list[str] = []
        cursor = 0
        for piece in raw:
            start = content.index(piece, cursor)
            end = start + len(piece)
            rebuilt.append(content[cursor:end] if rebuilt else content[:end])
            cursor = end
        if rebuilt:
            rebuilt[-1] = rebuilt[-1] + content[cursor:]
        pieces = rebuilt or [content]
    else:
        pieces = [content]
    return [piece for piece in pieces if piece]


def decompose_node(
    node: CompositionNode,
    spec: ZoomSpec,
) -> tuple[CompositionNode, list[Gap]]:
    """
    Rebuild a node with its Text atoms decomposed per a zoom spec

    ``Text`` atoms are replaced by one child node per piece (advisory
    ``kind`` matching the requested level) when the spec asks for a
    finer-than-natural text level and the text actually splits. Atom kinds
    that cannot decompose record an honest ``Gap`` when the spec explicitly
    requests a finer level for them. The input node is never mutated.

    Parameters
    ----------
        node:
            The natural node to decompose (left unmutated).
        spec:
            The consumer-supplied per-atom-type zoom specification.

    Return
    ------
        decomposed:
            A ``(node, gaps)`` pair: the rebuilt node and any typed gaps.
    """
    gaps: list[Gap] = []
    text_level = spec.level_for(AtomKind.TEXT)
    decompose_text = text_level in _FINER_TEXT_LEVELS

    new_children: list[NodeChild] = []
    for child in node.children:
        if isinstance(child, CompositionNode):
            rebuilt, child_gaps = decompose_node(child, spec)
            new_children.append(rebuilt)
            gaps.extend(child_gaps)
            continue
        if isinstance(child, Text) and decompose_text:
            pieces = split_text(child.content, text_level)
            if len(pieces) > 1:
                new_children.extend(
                    build_node(
                        kind=_PIECE_KINDS[text_level],
                        atoms=[
                            Text(
                                content=piece,
                                format=child.format,
                                language=child.language,
                                encoding=child.encoding,
                            )
                        ],
                    )
                    for piece in pieces
                )
                continue
        new_children.append(child)

    present_kinds = {
        child.kind for child in node.children if not isinstance(child, CompositionNode)
    }
    for kind in _UNDECOMPOSABLE:
        explicit = spec.per_type.get(kind)
        if explicit in _FINER_TEXT_LEVELS and kind in present_kinds:
            gaps.append(
                gap(
                    kind=ErrorKind.UNSUPPORTED,
                    locator=node.metadata.source_url or node.metadata.id or "",
                    detail=(f"{kind.value} atoms cannot decompose to {explicit.value}; kept whole"),
                )
            )

    return CompositionNode(metadata=node.metadata, children=new_children), gaps


def decompose_result(result: Result, spec: ZoomSpec) -> Result:
    """
    Apply ``decompose_node`` to a ``Result``, surfacing gaps honestly

    A ``Success`` whose decomposition produced gaps becomes a ``Partial``;
    a ``Partial`` keeps its existing gaps plus any new ones; an ``Error``
    passes through untouched.

    Parameters
    ----------
        result:
            The result whose tree (if any) should be decomposed.
        spec:
            The consumer-supplied per-atom-type zoom specification.

    Return
    ------
        decomposed:
            A new ``Result`` with the decomposed tree, or the original
            ``Error``.
    """
    if isinstance(result, Success):
        node, gaps = decompose_node(result.tree, spec)
        if gaps:
            return partial(node, gaps)
        return success(node)
    if isinstance(result, Partial):
        node, gaps = decompose_node(result.tree, spec)
        return partial(node, list(result.gaps) + gaps)
    return result
