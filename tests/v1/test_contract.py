"""External-behaviour tests for the v1 canonical contract schema.

These tests exercise only the public surface of the contract modules
(``omni_fetcher.v1.atoms``, ``omni_fetcher.v1.node`` and
``omni_fetcher.v1.metadata``):

- recursive composition of nodes whose leaves are canonical atoms,
- the content-vs-metadata separation (atoms carry content only and reject
  descriptive fields),
- tree navigation helpers (iterate atoms/descendants, find by kind),
- tag merging across a node and its descendants,
- deterministic Merkle-style ``content_hash`` population, and
- namespaced ``source_extra`` validation.

They assert observable behaviour, never private implementation details.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from omni_fetcher.v1.atoms import (
    Atom,
    AtomKind,
    Audio,
    Image,
    Table,
    Text,
    TextFormat,
    Video,
)
from omni_fetcher.v1.metadata import Metadata
from omni_fetcher.v1.node import CompositionNode


def _sample_tree() -> CompositionNode:
    """Build a small two-level tree reused across several tests."""
    return CompositionNode(
        metadata=Metadata(kind="document", tags=["root"]),
        children=[
            CompositionNode(
                metadata=Metadata(kind="page", tags=["p1"]),
                children=[
                    Text(content="hello"),
                    Text(content="world"),
                ],
            ),
            CompositionNode(
                metadata=Metadata(kind="page", tags=["p2", "root"]),
                children=[Table(headers=["h"], rows=[["v"]])],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Recursive composition: nodes nest; leaves are canonical atoms
# ---------------------------------------------------------------------------


class TestComposition:
    """A node composes child nodes and atoms recursively."""

    def test_node_holds_atom_children(self):
        node = CompositionNode(children=[Text(content="hi")])
        assert len(node.children) == 1
        assert isinstance(node.children[0], Text)

    def test_node_nests_node_children(self):
        leaf = CompositionNode(children=[Text(content="leaf")])
        root = CompositionNode(children=[leaf])
        assert isinstance(root.children[0], CompositionNode)
        assert isinstance(root.children[0].children[0], Text)

    def test_mixed_atom_and_node_children(self):
        node = CompositionNode(
            children=[
                Text(content="loose atom"),
                CompositionNode(children=[Text(content="nested")]),
            ]
        )
        assert isinstance(node.children[0], Text)
        assert isinstance(node.children[1], CompositionNode)

    def test_default_node_is_empty(self):
        node = CompositionNode()
        assert node.children == []
        assert isinstance(node.metadata, Metadata)

    def test_iter_atoms_walks_whole_subtree_in_order(self):
        tree = _sample_tree()
        atoms = list(tree.iter_atoms())
        assert len(atoms) == 3
        assert all(isinstance(a, Atom) for a in atoms)
        # Depth-first document order is preserved.
        texts = [a.content for a in atoms if isinstance(a, Text)]
        assert texts == ["hello", "world"]

    def test_iter_descendants_excludes_atoms(self):
        tree = _sample_tree()
        descendants = list(tree.iter_descendants())
        # Two child pages; atoms are not descendants.
        assert len(descendants) == 2
        assert all(isinstance(n, CompositionNode) for n in descendants)

    def test_find_by_kind_collects_matching_nodes(self):
        tree = _sample_tree()
        pages = tree.find_by_kind("page")
        assert len(pages) == 2
        assert {p.metadata.kind for p in pages} == {"page"}

    def test_find_atoms_filters_by_atom_kind(self):
        tree = _sample_tree()
        assert len(tree.find_atoms(AtomKind.TEXT)) == 2
        assert len(tree.find_atoms(AtomKind.TABLE)) == 1
        assert tree.find_atoms(AtomKind.IMAGE) == []


# ---------------------------------------------------------------------------
# Content vs metadata separation: atoms reject descriptive fields
# ---------------------------------------------------------------------------


class TestContentOnlyAtoms:
    """Atoms carry content only; descriptive fields are rejected."""

    def test_text_rejects_descriptive_author_field(self):
        with pytest.raises(ValidationError):
            Text(content="x", author="alice")

    def test_text_rejects_descriptive_id_and_timestamps(self):
        with pytest.raises(ValidationError):
            Text(content="x", id="123")
        with pytest.raises(ValidationError):
            Text(content="x", created="2020-01-01")

    def test_image_rejects_inline_exif_metadata(self):
        with pytest.raises(ValidationError):
            Image(format="jpeg", uri="http://x", exif={"Make": "Canon"})

    def test_audio_rejects_inline_id3_tags(self):
        with pytest.raises(ValidationError):
            Audio(format="mp3", uri="http://x", artist="Someone")

    def test_atoms_are_frozen(self):
        atom = Text(content="immutable")
        with pytest.raises(ValidationError):
            atom.content = "changed"

    def test_text_keeps_only_content_fields(self):
        atom = Text(
            content="body",
            format=TextFormat.MARKDOWN,
            language="en",
            encoding="utf-8",
        )
        assert atom.content == "body"
        assert atom.format is TextFormat.MARKDOWN
        assert set(atom.model_dump()) == {
            "kind",
            "content",
            "format",
            "language",
            "encoding",
        }


class TestBinaryAtomLocation:
    """Binary atoms require exactly one content location."""

    def test_image_requires_a_location(self):
        with pytest.raises(ValidationError):
            Image(format="png")

    def test_image_rejects_two_locations(self):
        with pytest.raises(ValidationError):
            Image(format="png", data=b"\x89PNG", uri="http://x/i.png")

    def test_image_accepts_inline_data_alone(self):
        atom = Image(format="png", data=b"\x89PNG")
        assert atom.data == b"\x89PNG"
        assert atom.uri is None

    def test_image_accepts_uri_alone(self):
        atom = Image(format="png", uri="http://x/i.png")
        assert atom.uri == "http://x/i.png"
        assert atom.data is None

    def test_audio_and_video_enforce_same_rule(self):
        with pytest.raises(ValidationError):
            Audio(format="mp3")
        with pytest.raises(ValidationError):
            Video(format="mp4")
        assert Audio(format="mp3", uri="http://x/a.mp3").uri
        assert Video(format="mp4", uri="http://x/v.mp4").uri

    def test_negative_dimensions_rejected(self):
        with pytest.raises(ValidationError):
            Image(format="png", uri="http://x", width=-1)
        with pytest.raises(ValidationError):
            Audio(format="mp3", uri="http://x", sample_rate=0)


class TestTableValidation:
    """Tables enforce rectangular rows against their headers."""

    def test_ragged_rows_rejected_when_headers_present(self):
        with pytest.raises(ValidationError):
            Table(headers=["a", "b"], rows=[[1, 2], [3]])

    def test_rectangular_rows_accepted(self):
        table = Table(headers=["a", "b"], rows=[[1, 2], [3, 4]])
        assert len(table.rows) == 2

    def test_headerless_table_allows_any_row_shape(self):
        table = Table(rows=[[1], [2, 3], []])
        assert len(table.rows) == 3


# ---------------------------------------------------------------------------
# Metadata: tags + namespaced source_extra
# ---------------------------------------------------------------------------


class TestMetadataTags:
    """Tags are deduplicated and blanks dropped on a single node."""

    def test_tags_deduplicated_preserving_order(self):
        meta = Metadata(tags=["b", "a", "b", "a", "c"])
        assert meta.tags == ["b", "a", "c"]

    def test_blank_tags_dropped(self):
        meta = Metadata(tags=["x", "", "   ", "y"])
        assert meta.tags == ["x", "y"]

    def test_default_tags_empty(self):
        assert Metadata().tags == []


class TestSourceExtraNamespacing:
    """source_extra is namespaced: keys are non-empty source names."""

    def test_namespaced_payload_accepted(self):
        meta = Metadata(
            source_extra={
                "github": {"issue_number": 42, "state": "open"},
                "notion": {"block_type": "paragraph"},
            }
        )
        assert meta.source_extra["github"]["issue_number"] == 42
        assert set(meta.source_extra) == {"github", "notion"}

    def test_empty_namespace_key_rejected(self):
        with pytest.raises(ValidationError):
            Metadata(source_extra={"": {"x": 1}})

    def test_blank_namespace_key_rejected(self):
        with pytest.raises(ValidationError):
            Metadata(source_extra={"   ": {"x": 1}})

    def test_non_dict_namespace_value_rejected(self):
        with pytest.raises(ValidationError):
            Metadata(source_extra={"github": ["not", "a", "dict"]})

    def test_default_source_extra_empty(self):
        assert Metadata().source_extra == {}


# ---------------------------------------------------------------------------
# Tag merging across the tree
# ---------------------------------------------------------------------------


class TestTagMerging:
    """A composite aggregates its descendants' tags, deduplicated."""

    def test_merged_tags_union_across_tree(self):
        tree = _sample_tree()
        # root + p1 + p2; the duplicate "root" on a child is deduped.
        assert tree.merged_tags() == ["root", "p1", "p2"]

    def test_merged_tags_preserve_depth_first_first_seen_order(self):
        tree = CompositionNode(
            metadata=Metadata(tags=["z"]),
            children=[
                CompositionNode(metadata=Metadata(tags=["a", "b"])),
                CompositionNode(metadata=Metadata(tags=["b", "c"])),
            ],
        )
        assert tree.merged_tags() == ["z", "a", "b", "c"]

    def test_merged_tags_does_not_mutate_node_tags(self):
        tree = _sample_tree()
        _ = tree.merged_tags()
        # The root's own stored tags are untouched by aggregation.
        assert tree.metadata.tags == ["root"]
        assert tree.children[0].metadata.tags == ["p1"]

    def test_leaf_node_merged_tags_are_its_own(self):
        node = CompositionNode(metadata=Metadata(tags=["solo"]))
        assert node.merged_tags() == ["solo"]


# ---------------------------------------------------------------------------
# content_hash: deterministic Merkle over content
# ---------------------------------------------------------------------------


class TestContentHash:
    """``content_hash`` is a deterministic content-only fingerprint."""

    def test_same_content_same_hash(self):
        assert (
            _sample_tree().compute_content_hash()
            == _sample_tree().compute_content_hash()
        )

    def test_different_content_different_hash(self):
        a = _sample_tree()
        b = _sample_tree()
        b.children[0].children[0] = Text(content="HELLO")
        assert a.compute_content_hash() != b.compute_content_hash()

    def test_hash_ignores_metadata(self):
        # Changing descriptive metadata must not change the content hash:
        # the hash fingerprints content, not description.
        a = _sample_tree()
        b = _sample_tree()
        b.metadata.id = "doc-99"
        b.metadata.author = "bob"
        b.children[0].metadata.tags = ["totally", "different"]
        assert a.compute_content_hash() == b.compute_content_hash()

    def test_child_order_affects_hash(self):
        ordered = CompositionNode(
            children=[Text(content="a"), Text(content="b")]
        )
        swapped = CompositionNode(
            children=[Text(content="b"), Text(content="a")]
        )
        assert (
            ordered.compute_content_hash()
            != swapped.compute_content_hash()
        )

    def test_empty_trees_hash_equal(self):
        assert (
            CompositionNode().compute_content_hash()
            == CompositionNode().compute_content_hash()
        )

    def test_not_populated_on_construction(self):
        # Hashing is opt-in; constructing a node does not populate it.
        assert _sample_tree().metadata.content_hash is None

    def test_populate_hashes_fills_every_node(self):
        tree = _sample_tree()
        tree.populate_hashes()
        assert tree.metadata.content_hash is not None
        for node in tree.iter_descendants():
            assert node.metadata.content_hash is not None

    def test_populate_hashes_matches_compute(self):
        tree = _sample_tree()
        expected = tree.compute_content_hash()
        tree.populate_hashes()
        assert tree.metadata.content_hash == expected

    def test_parent_hash_differs_from_child_hash(self):
        tree = _sample_tree()
        tree.populate_hashes()
        child_hashes = {
            n.metadata.content_hash for n in tree.iter_descendants()
        }
        assert tree.metadata.content_hash not in child_hashes

    def test_prev_hash_left_reserved(self):
        tree = _sample_tree()
        tree.populate_hashes()
        assert tree.metadata.prev_hash is None
        for node in tree.iter_descendants():
            assert node.metadata.prev_hash is None

    def test_hash_stable_with_binary_atom_content(self):
        # Arbitrary bytes (non-UTF-8) must hash deterministically.
        payload = bytes(range(256))
        a = CompositionNode(children=[Image(format="png", data=payload)])
        b = CompositionNode(children=[Image(format="png", data=payload)])
        assert a.compute_content_hash() == b.compute_content_hash()
