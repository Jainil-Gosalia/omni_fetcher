"""Unit tests for the pure zoom resolver (``omni_fetcher.v1.zoom``).

These tests assert EXTERNAL behaviour only: per-atom-type depth resolution,
the natural-granularity default for omitted types, the absence of any
token/character windowing, determinism, and edge cases (unknown/all atom
kinds, leaf atoms, MAX depth, and the empty spec).
"""

from __future__ import annotations

import pytest

from omni_fetcher.v1 import DepthLevel, ZoomSpec
from omni_fetcher.v1.atoms import AtomKind, Text
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.zoom import (
    is_natural,
    prune_to_zoom,
    resolve_level,
    should_expand,
    target_depth,
)

ALL_KINDS = list(AtomKind)


# ---------------------------------------------------------------------------
# Per-atom-type resolution
# ---------------------------------------------------------------------------


def test_per_type_resolution_text_vs_image():
    """Text resolves to SENTENCE while image stays WHOLE."""
    spec = ZoomSpec(
        per_type={
            AtomKind.TEXT: DepthLevel.SENTENCE,
            AtomKind.IMAGE: DepthLevel.WHOLE,
        }
    )
    assert resolve_level(spec, AtomKind.TEXT) is DepthLevel.SENTENCE
    assert resolve_level(spec, AtomKind.IMAGE) is DepthLevel.WHOLE


def test_level_for_matches_resolve_level():
    """The frozen ``level_for`` method agrees with ``resolve_level``."""
    spec = ZoomSpec(per_type={AtomKind.TABLE: DepthLevel.SECTION})
    for kind in ALL_KINDS:
        assert spec.level_for(kind) is resolve_level(spec, kind)


def test_explicit_default_applies_to_omitted_types():
    """A non-NATURAL default applies to every unspecified atom type."""
    spec = ZoomSpec(
        default=DepthLevel.PARAGRAPH,
        per_type={AtomKind.IMAGE: DepthLevel.WHOLE},
    )
    assert resolve_level(spec, AtomKind.IMAGE) is DepthLevel.WHOLE
    for kind in ALL_KINDS:
        if kind is AtomKind.IMAGE:
            continue
        assert resolve_level(spec, kind) is DepthLevel.PARAGRAPH


# ---------------------------------------------------------------------------
# Natural-granularity default for omitted types
# ---------------------------------------------------------------------------


def test_omitted_types_fall_back_to_natural_default():
    """Omitted types resolve to NATURAL when the default is NATURAL."""
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
    assert resolve_level(spec, AtomKind.IMAGE) is DepthLevel.NATURAL
    assert resolve_level(spec, AtomKind.AUDIO) is DepthLevel.NATURAL
    assert is_natural(spec, AtomKind.IMAGE)
    assert not is_natural(spec, AtomKind.TEXT)


def test_empty_spec_is_natural_everywhere():
    """An empty ZoomSpec means natural granularity for every atom kind."""
    spec = ZoomSpec()
    assert spec.default is DepthLevel.NATURAL
    for kind in ALL_KINDS:
        assert resolve_level(spec, kind) is DepthLevel.NATURAL
        assert is_natural(spec, kind)


def test_natural_has_no_fixed_target_depth():
    """NATURAL yields no fixed depth budget -- the source decides."""
    spec = ZoomSpec()
    for kind in ALL_KINDS:
        assert target_depth(spec, kind) is None


# ---------------------------------------------------------------------------
# should_expand: semantic depth, never windowing
# ---------------------------------------------------------------------------


def test_natural_never_forces_expansion():
    """NATURAL defers to the source and never forces a deeper layer."""
    spec = ZoomSpec()
    for depth in (0, 1, 5, 50):
        assert should_expand(spec, AtomKind.TEXT, depth) is False


def test_whole_keeps_atom_whole():
    """WHOLE expands zero layers (the atom is kept whole)."""
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.WHOLE})
    assert should_expand(spec, AtomKind.TEXT, 0) is False


def test_finer_levels_expand_until_budget_reached():
    """Finer levels expand while fewer layers than the budget exist."""
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
    # SECTION < PARAGRAPH < SENTENCE: sentence expands several layers.
    assert should_expand(spec, AtomKind.TEXT, 0) is True
    assert should_expand(spec, AtomKind.TEXT, 1) is True
    assert should_expand(spec, AtomKind.TEXT, 2) is True
    # Once enough layers exist, expansion stops -- it is not unbounded.
    assert should_expand(spec, AtomKind.TEXT, 3) is False
    assert should_expand(spec, AtomKind.TEXT, 99) is False


def test_section_is_coarser_than_sentence():
    """Coarser levels stop expanding sooner than finer ones."""
    section = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SECTION})
    sentence = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
    section_depth = target_depth(section, AtomKind.TEXT)
    sentence_depth = target_depth(sentence, AtomKind.TEXT)
    assert section_depth is not None
    assert sentence_depth is not None
    assert section_depth < sentence_depth


def test_max_expands_as_deep_as_source_allows():
    """MAX keeps expanding far beyond any small fixed depth."""
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.MAX})
    for depth in (0, 1, 10, 1000):
        assert should_expand(spec, AtomKind.TEXT, depth) is True


def test_should_expand_rejects_negative_depth():
    """A negative depth is a programmer error, surfaced as ValueError."""
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
    with pytest.raises(ValueError):
        should_expand(spec, AtomKind.TEXT, -1)


def test_no_windowing_units_in_vocabulary():
    """The depth vocabulary is semantic; no token/char window levels exist."""
    names = {level.value for level in DepthLevel}
    forbidden = {"token", "tokens", "char", "chars", "character", "window"}
    assert names.isdisjoint(forbidden)
    # The whole vocabulary is the curated semantic set, nothing more.
    assert names == {
        "natural",
        "whole",
        "section",
        "paragraph",
        "sentence",
        "max",
    }


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_resolution_is_deterministic():
    """Identical inputs always yield identical resolutions."""
    spec = ZoomSpec(
        default=DepthLevel.SECTION,
        per_type={AtomKind.TEXT: DepthLevel.SENTENCE},
    )
    for kind in ALL_KINDS:
        first = resolve_level(spec, kind)
        for _ in range(5):
            assert resolve_level(spec, kind) is first


def test_node_argument_does_not_change_resolution():
    """Passing a node context never changes the spec-driven decision."""
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
    node = CompositionNode(children=[Text(content="hello world.")])
    assert resolve_level(spec, AtomKind.TEXT) is resolve_level(
        spec, AtomKind.TEXT, node
    )


# ---------------------------------------------------------------------------
# Edge cases: all/unknown kinds
# ---------------------------------------------------------------------------


def test_all_atom_kinds_resolve_to_a_valid_level():
    """Every atom kind resolves to a member of DepthLevel."""
    spec = ZoomSpec(default=DepthLevel.WHOLE)
    for kind in ALL_KINDS:
        assert resolve_level(spec, kind) in set(DepthLevel)


def test_each_kind_resolves_independently():
    """Distinct per-type entries do not bleed across atom kinds."""
    spec = ZoomSpec(
        per_type={
            AtomKind.TEXT: DepthLevel.SENTENCE,
            AtomKind.TABLE: DepthLevel.SECTION,
            AtomKind.VIDEO: DepthLevel.MAX,
        }
    )
    assert resolve_level(spec, AtomKind.TEXT) is DepthLevel.SENTENCE
    assert resolve_level(spec, AtomKind.TABLE) is DepthLevel.SECTION
    assert resolve_level(spec, AtomKind.VIDEO) is DepthLevel.MAX
    # Omitted kinds keep the (natural) default.
    assert resolve_level(spec, AtomKind.AUDIO) is DepthLevel.NATURAL


# ---------------------------------------------------------------------------
# prune_to_zoom: pure, semantic, never windowed
# ---------------------------------------------------------------------------


def _sample_tree() -> CompositionNode:
    """Build a small natural composition tree for pruning tests."""
    sentence_a = CompositionNode(children=[Text(content="One.")])
    sentence_b = CompositionNode(children=[Text(content="Two.")])
    paragraph = CompositionNode(children=[sentence_a, sentence_b])
    section = CompositionNode(children=[paragraph])
    return CompositionNode(children=[section])


def test_prune_returns_new_tree_and_leaves_input_unmutated():
    """Pruning is pure: the input tree is not mutated and a copy returns."""
    tree = _sample_tree()
    before = tree.model_dump()
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.WHOLE})
    pruned = prune_to_zoom(tree, spec)
    assert pruned is not tree
    assert tree.model_dump() == before


def test_prune_natural_preserves_tree():
    """A NATURAL spec leaves the source tree exactly as emitted."""
    tree = _sample_tree()
    pruned = prune_to_zoom(tree, ZoomSpec())
    assert pruned.model_dump() == tree.model_dump()


def test_prune_is_deterministic():
    """Pruning the same tree with the same spec is reproducible."""
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SECTION})
    first = prune_to_zoom(_sample_tree(), spec)
    second = prune_to_zoom(_sample_tree(), spec)
    assert first.model_dump() == second.model_dump()


def test_prune_keeps_leaf_atoms_intact():
    """Pruning never splits an atom's content (no windowing)."""
    tree = CompositionNode(
        children=[Text(content="A whole sentence of text.")]
    )
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.WHOLE})
    pruned = prune_to_zoom(tree, spec)
    leaves = [c for c in pruned.children if isinstance(c, Text)]
    assert len(leaves) == 1
    assert leaves[0].content == "A whole sentence of text."


def test_prune_leaf_only_tree_for_all_levels():
    """A leaf-only tree survives pruning intact at every level."""
    tree = CompositionNode(children=[Text(content="leaf content")])
    for level in DepthLevel:
        spec = ZoomSpec(per_type={AtomKind.TEXT: level})
        pruned = prune_to_zoom(tree, spec)
        leaves = [c for c in pruned.children if isinstance(c, Text)]
        assert len(leaves) == 1
        assert leaves[0].content == "leaf content"


def test_prune_empty_tree():
    """An empty tree prunes to an empty tree."""
    tree = CompositionNode()
    pruned = prune_to_zoom(tree, ZoomSpec(default=DepthLevel.SENTENCE))
    assert pruned.children == []
