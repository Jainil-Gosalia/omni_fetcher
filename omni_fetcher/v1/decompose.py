"""Pure finer-than-natural text decomposition for the v1 contract.

Zoom's second half: where central pruning (``zoom.prune_result``) collapses a
tree to *coarser* semantic depths, this module expands ``Text`` atoms to
*finer* ones -- ``SECTION`` / ``PARAGRAPH`` / ``SENTENCE`` child nodes --
for connectors whose natural granularity is a whole document.

Splitting is dispatched on the atom's ``TextFormat``, because a split rule is
only meaningful for the syntax it was written for. Applying prose rules to a
JSON payload or a source file produces fragments that are lossless and
meaningless -- the failure this dispatch exists to prevent. The judgment of
which (format, level) pairs are decomposable lives in exactly one place:
:data:`_SPLIT_RULES`. A pair with no rule is not silently skipped; it returns
the atom whole and records an honest ``Gap``.

Two formats decompose at no level at all:

- ``CODE`` -- code and structured serialisations (JSON) have real structure,
  but it is *not* prose structure, and recovering it needs per-language
  parsing this module deliberately does not do.
- ``OPAQUE`` -- we do not know what the bytes are, so every split is a guess.

This is PHILOSOPHY section 4 applied: an atom is the lowest *useful*
composition artifact, not the point of absolute irreducibility. A sentence is
an atom although it reduces to words and letters; a JSON record is an atom
although it reduces to punctuation-delimited fragments. Both reductions are
possible and lossless and destroy the meaning, so both stop here.

Everything here is pure and versioned so behaviour changes are reviewable:

- :func:`split_text` -- lossless splitting for a (format, level) pair: the
  pieces concatenate exactly to the input (separators stay attached to the
  preceding piece). ``MAX`` maps to the deepest level the format supports.
- :func:`decompose_node` -- rebuild a node whose ``Text`` atoms are replaced
  by per-piece child nodes when the spec requests a finer-than-natural text
  level their format supports. Atom kinds that cannot decompose (image,
  audio, video, table) and formats that cannot decompose record an honest
  ``Gap`` when a finer level was *explicitly* requested -- never a silent
  no-op.
- :func:`decompose_result` -- the ``Result`` wrapper: a ``Success`` that
  gains gaps becomes a ``Partial``; errors pass through untouched.

Never token/character windowing: splits follow the text's own semantic
markers, and a text with no such markers stays whole.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from omni_fetcher.v1.atoms import AtomKind, Text, TextFormat
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

# Text levels that mean "decompose finer than the natural whole". MAX is
# resolved per format to the deepest level that format supports.
_FINER_TEXT_LEVELS: frozenset[DepthLevel] = frozenset(
    {DepthLevel.SECTION, DepthLevel.PARAGRAPH, DepthLevel.SENTENCE, DepthLevel.MAX}
)

# Advisory ``kind`` for the per-piece nodes, per resolved level.
_PIECE_KINDS: dict[DepthLevel, str] = {
    DepthLevel.SECTION: "section",
    DepthLevel.PARAGRAPH: "paragraph",
    DepthLevel.SENTENCE: "sentence",
}

# Named split strategies. Each is lossless: lookahead/lookbehind splits keep
# every character, so the pieces always concatenate exactly to the input.
_MARKDOWN_SECTION = "markdown_section"
_HTML_SECTION = "html_section"
_HTML_PARAGRAPH = "html_paragraph"
_BLANK_LINE = "blank_line"
_TERMINAL_PUNCTUATION = "terminal_punctuation"

# "This syntax has no marker for this level, so the content is exactly one of
# them." A true answer, not a failure -- distinct from an absent rule, which
# means "splitting here would be meaningless or unsafe; refuse and say so".
# Plain text has no heading syntax, so a plain atom IS one section; saying
# otherwise would fire a false gap on e.g. a PPTX deck, whose slides already
# supply the section layer above the atom.
_NO_MARKERS = "no_markers"

_SECTION_SPLIT = re.compile(r"(?=^#{1,6} )", re.MULTILINE)
_PARAGRAPH_SPLIT = re.compile(r"(?<=\n\n)")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_HTML_HEADING_TAGS: tuple[str, ...] = ("h1", "h2", "h3", "h4", "h5", "h6")
_HTML_BLOCK_TAGS: tuple[str, ...] = (
    *_HTML_HEADING_TAGS,
    "p",
    "div",
    "li",
    "blockquote",
    "pre",
    "section",
    "article",
)

# ---------------------------------------------------------------------------
# The single source of truth for "can this format split at this level, and
# how?". A (format, level) pair absent from this table is NOT decomposable:
# the atom is returned whole and a Gap is recorded. Adding a format to the
# vocabulary without adding it here makes it undecomposable by default --
# deliberately the safe direction.
#
# CODE and OPAQUE appear with no levels at all: see the module docstring.
# ---------------------------------------------------------------------------
_SPLIT_RULES: dict[TextFormat, dict[DepthLevel, str]] = {
    TextFormat.MARKDOWN: {
        DepthLevel.SECTION: _MARKDOWN_SECTION,
        DepthLevel.PARAGRAPH: _BLANK_LINE,
        DepthLevel.SENTENCE: _TERMINAL_PUNCTUATION,
    },
    TextFormat.PLAIN: {
        DepthLevel.SECTION: _NO_MARKERS,
        DepthLevel.PARAGRAPH: _BLANK_LINE,
        DepthLevel.SENTENCE: _TERMINAL_PUNCTUATION,
    },
    TextFormat.TRANSCRIPT: {
        DepthLevel.SECTION: _NO_MARKERS,
        DepthLevel.PARAGRAPH: _BLANK_LINE,
        DepthLevel.SENTENCE: _TERMINAL_PUNCTUATION,
    },
    TextFormat.HTML: {
        # Markup splits at element boundaries only. SENTENCE is deliberately
        # absent: prose punctuation does not respect tag boundaries, so
        # splitting there yields unbalanced fragments that still claim to be
        # HTML -- the same lie this dispatch exists to prevent.
        DepthLevel.SECTION: _HTML_SECTION,
        DepthLevel.PARAGRAPH: _HTML_PARAGRAPH,
    },
    TextFormat.RST: {
        # RST *does* have section markers (underlined titles), so unlike PLAIN
        # this is a real capability gap rather than "no such thing" -- it gaps
        # honestly until implemented. No connector emits RST today.
        DepthLevel.PARAGRAPH: _BLANK_LINE,
        DepthLevel.SENTENCE: _TERMINAL_PUNCTUATION,
    },
    TextFormat.CODE: {},
    TextFormat.OPAQUE: {},
}

# Deepest-to-shallowest, for resolving MAX against a format's own support.
_DEPTH_ORDER: tuple[DepthLevel, ...] = (
    DepthLevel.SENTENCE,
    DepthLevel.PARAGRAPH,
    DepthLevel.SECTION,
)

# Atom kinds that have no finer semantic decomposition in this module.
_UNDECOMPOSABLE: tuple[AtomKind, ...] = (
    AtomKind.IMAGE,
    AtomKind.AUDIO,
    AtomKind.VIDEO,
    AtomKind.TABLE,
)


def resolve_level(level: DepthLevel, format: TextFormat) -> Optional[DepthLevel]:
    """
    Resolve a requested level against a format's supported levels

    ``MAX`` means "as deep as this format meaningfully allows", so it
    resolves to the deepest level the format supports -- and to ``None``
    for a format that supports none. Any other level resolves to itself
    when supported, else ``None``.

    Parameters
    ----------
        level:
            The requested semantic level.
        format:
            The surface syntax of the content being split.

    Return
    ------
        resolved:
            The concrete level to split at, or ``None`` when ``format``
            has no meaningful decomposition at ``level``.
    """
    rules = _SPLIT_RULES.get(format, {})
    if not rules:
        return None
    if level is DepthLevel.MAX:
        return next((deep for deep in _DEPTH_ORDER if deep in rules), None)
    return level if level in rules else None


def _split_on_terminal_punctuation(content: str) -> list[str]:
    """Split on sentence-terminal punctuation, keeping every character."""
    # Keep the inter-sentence whitespace attached to the preceding sentence
    # so concatenation is lossless.
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
    return rebuilt or [content]


def _split_html(content: str, tags: tuple[str, ...]) -> tuple[list[str], bool]:
    """Split HTML before each *top-level* occurrence of the given block tags.

    Lossless: boundaries come from the parsed document (so ``<H2>``,
    attributes, and whitespace inside the tag are handled), but the split
    itself is done on the raw string at those offsets -- the pieces are the
    original markup, never bs4's re-serialisation of it.

    Only direct children of the parse root are cut points. Cutting at a
    *nested* element's offset would sever its ancestors' tags and leave both
    pieces unbalanced (``<div><h1>A</h1>`` | ``<h2>B</h2></div>``), producing
    fragments that claim to be HTML but are not. Splitting only between
    top-level siblings keeps every piece a well-formed fragment.

    Returns ``(pieces, declined)``. ``declined`` is ``True`` when the markup
    *does* have block structure but all of it is nested (a full ``<html>``
    page, or a single wrapper ``<div>``), so the content came back whole
    because we refused to unbalance it -- not because there was nothing to
    split. The caller turns that into an honest ``Gap``.
    """
    soup = BeautifulSoup(content, "html.parser")
    # sourcepos is 0-based on html.parser and points at the tag's "<". It is
    # None for elements the parser synthesised rather than read from the input
    # (e.g. an implied <body>), which are not usable cut points.
    positions = [
        el.sourcepos for el in soup.find_all(tags, recursive=False) if el.sourcepos is not None
    ]
    cuts = sorted(pos for pos in positions if 0 < pos < len(content))
    if not cuts:
        declined = len(soup.find_all(tags)) > 1
        return [content], declined
    bounds = [0, *cuts, len(content)]
    return [content[start:end] for start, end in zip(bounds, bounds[1:])], False


def _split_report(
    content: str,
    level: DepthLevel,
    format: TextFormat,
) -> tuple[list[str], Optional[str]]:
    """Split ``content``, reporting why it stayed whole when it did.

    Returns ``(pieces, decline_reason)``. ``decline_reason`` is ``None`` when
    the split ran normally -- including when it legitimately produced one
    piece because the content has no split markers ("this text is one
    paragraph" is an answer, not a failure). It is a human-readable string
    only when the requested decomposition *could not be delivered*: the format
    has no rule at this level, or the rule declined to act. That distinction
    is what keeps :func:`decompose_node` from either gapping noisily on
    single-paragraph prose or staying silent on a real capability gap.
    """
    resolved = resolve_level(level, format)
    if resolved is None:
        return ([content] if content else []), (
            f"{format.value} text cannot decompose to {level.value}; kept whole"
        )

    strategy = _SPLIT_RULES[format][resolved]
    declined_detail: Optional[str] = None
    if strategy == _NO_MARKERS:
        # This syntax has no marker for this level: the content is exactly one
        # of them. A true answer, so no gap.
        pieces = [content]
    elif strategy == _MARKDOWN_SECTION:
        pieces = _SECTION_SPLIT.split(content)
    elif strategy == _BLANK_LINE:
        pieces = _PARAGRAPH_SPLIT.split(content)
    elif strategy == _TERMINAL_PUNCTUATION:
        pieces = _split_on_terminal_punctuation(content)
    elif strategy in (_HTML_SECTION, _HTML_PARAGRAPH):
        # Headings are block-level too: at paragraph depth a heading is its
        # own block, not a tail on the preceding paragraph's piece.
        tags = _HTML_HEADING_TAGS if strategy == _HTML_SECTION else _HTML_BLOCK_TAGS
        pieces, declined = _split_html(content, tags)
        if declined:
            declined_detail = (
                f"html block structure is nested; cannot split to {resolved.value} "
                "without unbalancing markup; kept whole"
            )
    else:  # pragma: no cover - a rule name with no branch is a programmer error.
        raise ValueError(f"unknown split strategy {strategy!r}")
    return [piece for piece in pieces if piece], declined_detail


def split_text(
    content: str,
    level: DepthLevel,
    format: TextFormat = TextFormat.PLAIN,
) -> list[str]:
    """
    Split text at a semantic level for its format, losslessly

    The split rule is chosen by ``format`` (see :data:`_SPLIT_RULES`), because
    a rule is only meaningful for the syntax it was written for. Pieces always
    concatenate exactly to the input; separators stay attached to the
    preceding piece. A text with no split markers -- or a format with no rule
    at ``level`` -- comes back as a single piece.

    NOTE:
        1. A single-piece return does not distinguish "nothing to split" from
           "this format cannot split here". Callers that must tell those apart
           (to record a ``Gap``) consult :func:`resolve_level`;
           :func:`decompose_node` already does.

    Parameters
    ----------
        content:
            The text to split.
        level:
            The requested semantic level (``MAX`` resolves to the deepest
            level ``format`` supports).
        format:
            The surface syntax of ``content``. Defaults to ``PLAIN`` so that
            direct prose callers keep the obvious behaviour.

    Return
    ------
        pieces:
            Non-empty text pieces in document order.
    """
    return _split_report(content, level, format)[0]


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
    locator = node.metadata.source_url or node.metadata.id or ""

    new_children: list[NodeChild] = []
    for child in node.children:
        if isinstance(child, CompositionNode):
            rebuilt, child_gaps = decompose_node(child, spec)
            new_children.append(rebuilt)
            gaps.extend(child_gaps)
            continue
        if isinstance(child, Text) and decompose_text:
            pieces, declined = _split_report(child.content, text_level, child.format)
            if declined is not None:
                # The requested decomposition could not be delivered (this
                # format has no rule at this level, or the rule refused to
                # act). Keep the atom whole and say so -- splitting anyway
                # would be lossless and meaningless, and staying silent would
                # be a no-op the caller cannot see.
                gaps.append(gap(kind=ErrorKind.UNSUPPORTED, locator=locator, detail=declined))
                new_children.append(child)
                continue
            if len(pieces) > 1:
                resolved = resolve_level(text_level, child.format)
                assert resolved is not None  # declined is None => a rule ran.
                new_children.extend(
                    build_node(
                        kind=_PIECE_KINDS[resolved],
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
                    locator=locator,
                    detail=(f"{kind.value} atoms cannot decompose to {explicit.value}; kept whole"),
                )
            )

    return CompositionNode(metadata=node.metadata, children=new_children), gaps


def decompose_result(result: Result, spec: ZoomSpec) -> Result:
    """
    Apply ``decompose_node`` to a ``Result``, surfacing gaps honestly

    A ``Success`` whose decomposition produced gaps becomes a ``Partial``;
    a ``Partial`` keeps its existing gaps plus any *new* ones; an ``Error``
    passes through untouched.

    NOTE:
        1. This is idempotent: re-decomposing an already-decomposed result
           with the same spec returns an equal result. A finer-split tree is
           left alone (the ``len(pieces) > 1`` guard makes re-splitting a
           no-op), and an undecomposable atom that already recorded a gap does
           not record a *second* identical gap on the next pass -- new gaps
           equal to ones already present are dropped. This is what makes
           applying decomposition centrally safe regardless of whether a
           connector also decomposed internally.

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
        # Keep the existing gaps and add only genuinely new ones. A second
        # pass regenerates the same gaps for the same undecomposable atoms;
        # re-appending them would break idempotency and inflate the list.
        existing = list(result.gaps)
        added = [g for g in gaps if g not in existing]
        return partial(node, existing + added)
    return result
