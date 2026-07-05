"""Finer-than-natural text decomposition tests (issue 008, tracer).

The pure splitter (``omni_fetcher.v1.decompose``) plus its wiring into the
local_file connector -- the tracer proving the pattern end to end:

- splits are lossless at every level (pieces concatenate to the input);
- a multi-paragraph file fetched at PARAGRAPH yields one child node per
  paragraph whose concatenated content equals the natural fetch's content;
- SECTION splits on markdown headings; SENTENCE is best-effort;
- marker-less text stays whole (no gratuitous wrapper nodes);
- determinism: same file + spec -> identical tree;
- an explicitly-requested finer level for an undecomposable atom kind
  records an honest UNSUPPORTED gap (Partial), never a silent no-op.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omni_fetcher.v1 import DepthLevel, ZoomSpec
from omni_fetcher.v1.atoms import AtomKind, Image, Text
from omni_fetcher.v1.connectors.local_file import LocalFileFetcher
from omni_fetcher.v1.decompose import (
    decompose_node,
    decompose_result,
    split_text,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.result import Partial, Success, success

pytestmark = pytest.mark.asyncio

PARAGRAPH_TEXT = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.PARAGRAPH})

DOC = "# One\n\nFirst paragraph here.\n\nSecond paragraph. Two sentences!\n\n# Two\n\nThird paragraph.\n"


# ---------------------------------------------------------------------------
# The pure splitter


@pytest.mark.parametrize(
    "level",
    [DepthLevel.SECTION, DepthLevel.PARAGRAPH, DepthLevel.SENTENCE, DepthLevel.MAX],
)
async def test_split_is_lossless_at_every_level(level: DepthLevel) -> None:
    """Pieces concatenate exactly to the input, whatever the level."""
    pieces = split_text(DOC, level)

    assert "".join(pieces) == DOC
    assert all(pieces)


async def test_section_split_breaks_before_headings() -> None:
    """SECTION starts a new piece at each markdown heading."""
    pieces = split_text(DOC, DepthLevel.SECTION)

    assert len(pieces) == 2
    assert pieces[0].startswith("# One")
    assert pieces[1].startswith("# Two")


async def test_paragraph_split_breaks_on_blank_lines() -> None:
    """PARAGRAPH yields one piece per blank-line-separated block."""
    pieces = split_text("a\n\nb\n\nc", DepthLevel.PARAGRAPH)

    assert [piece.strip() for piece in pieces] == ["a", "b", "c"]


async def test_sentence_split_is_best_effort() -> None:
    """SENTENCE splits on terminal punctuation, keeping separators."""
    pieces = split_text("One. Two! Three?", DepthLevel.SENTENCE)

    assert [piece.strip() for piece in pieces] == ["One.", "Two!", "Three?"]
    assert "".join(pieces) == "One. Two! Three?"


async def test_markerless_text_stays_whole() -> None:
    """Text with no split markers comes back as a single piece."""
    assert split_text("just one line", DepthLevel.PARAGRAPH) == ["just one line"]


# ---------------------------------------------------------------------------
# The tracer connector: local_file end to end


async def test_paragraph_zoom_yields_one_child_per_paragraph(
    tmp_path: Path,
) -> None:
    """A multi-paragraph file at PARAGRAPH decomposes with content equality."""
    doc = tmp_path / "notes.md"
    doc.write_text(DOC, encoding="utf-8")

    natural = await LocalFileFetcher().fetch(str(doc))
    zoomed = await LocalFileFetcher().fetch(str(doc), zoom=PARAGRAPH_TEXT)

    assert isinstance(natural, Success) and isinstance(zoomed, Success)
    paragraphs = zoomed.tree.find_by_kind("paragraph")
    assert len(paragraphs) == 5  # headings and bodies are blocks alike
    natural_content = "".join(atom.content for atom in natural.tree.find_atoms(AtomKind.TEXT))
    zoomed_content = "".join(atom.content for atom in zoomed.tree.find_atoms(AtomKind.TEXT))
    assert zoomed_content == natural_content


async def test_section_zoom_splits_on_headings(tmp_path: Path) -> None:
    """A headed document at SECTION yields one child per heading."""
    doc = tmp_path / "notes.md"
    doc.write_text(DOC, encoding="utf-8")

    result = await LocalFileFetcher().fetch(
        str(doc), zoom=ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SECTION})
    )

    assert isinstance(result, Success)
    sections = result.tree.find_by_kind("section")
    assert len(sections) == 2
    assert sections[0].find_atoms(AtomKind.TEXT)[0].content.startswith("# One")


async def test_decomposition_is_deterministic(tmp_path: Path) -> None:
    """Same file + spec produces an identical tree across runs."""
    doc = tmp_path / "notes.md"
    doc.write_text(DOC, encoding="utf-8")

    first = await LocalFileFetcher().fetch(str(doc), zoom=PARAGRAPH_TEXT)
    second = await LocalFileFetcher().fetch(str(doc), zoom=PARAGRAPH_TEXT)

    assert isinstance(first, Success) and isinstance(second, Success)
    firsts = [n.find_atoms(AtomKind.TEXT)[0].content for n in first.tree.find_by_kind("paragraph")]
    seconds = [
        n.find_atoms(AtomKind.TEXT)[0].content for n in second.tree.find_by_kind("paragraph")
    ]
    assert firsts == seconds


async def test_single_block_file_keeps_its_flat_shape(tmp_path: Path) -> None:
    """A file with no split markers gains no wrapper nodes."""
    doc = tmp_path / "one.txt"
    doc.write_text("just one block of text", encoding="utf-8")

    result = await LocalFileFetcher().fetch(str(doc), zoom=PARAGRAPH_TEXT)

    assert isinstance(result, Success)
    assert result.tree.find_by_kind("paragraph") == []
    assert result.tree.find_atoms(AtomKind.TEXT)[0].content == "just one block of text"


# ---------------------------------------------------------------------------
# Honest gaps for undecomposable kinds


async def test_explicit_finer_level_for_images_records_a_gap() -> None:
    """Requesting SENTENCE for image atoms yields Partial with a typed gap."""
    node = build_node(
        kind="doc",
        atoms=[
            Text(content="Alpha.\n\nBeta."),
            Image(uri="https://example.com/x.png", format="png"),
        ],
    )
    spec = ZoomSpec(
        per_type={
            AtomKind.TEXT: DepthLevel.PARAGRAPH,
            AtomKind.IMAGE: DepthLevel.SENTENCE,
        }
    )

    result = decompose_result(success(node), spec)

    assert isinstance(result, Partial)
    assert result.gaps and result.gaps[0].kind == ErrorKind.UNSUPPORTED
    # The text still decomposed and the image atom survived whole.
    assert len(result.tree.find_by_kind("paragraph")) == 2
    assert len(result.tree.find_atoms(AtomKind.IMAGE)) == 1


async def test_default_finer_level_does_not_gap_other_kinds() -> None:
    """A finer *default* never flags undecomposable kinds -- only explicit
    per-type requests do."""
    node = build_node(
        kind="doc",
        atoms=[
            Text(content="Alpha.\n\nBeta."),
            Image(uri="https://example.com/x.png", format="png"),
        ],
    )
    spec = ZoomSpec(default=DepthLevel.PARAGRAPH)

    _, gaps = decompose_node(node, spec)

    assert gaps == []
