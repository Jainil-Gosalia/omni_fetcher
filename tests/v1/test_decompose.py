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

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from omni_fetcher.v1 import DepthLevel, ZoomSpec
from omni_fetcher.v1.atoms import AtomKind, Image, Text, TextFormat
from omni_fetcher.v1.connectors.local_file import LocalFileFetcher
from omni_fetcher.v1.decompose import (
    decompose_node,
    decompose_result,
    resolve_level,
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
    pieces = split_text(DOC, DepthLevel.SECTION, TextFormat.MARKDOWN)

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


async def test_markdown_rules_are_not_applied_to_plain_text() -> None:
    """A '#' in plain text is not a heading: only markdown sections split."""
    hashed = "# not a heading\n\nbody"

    assert split_text(hashed, DepthLevel.SECTION, TextFormat.PLAIN) == [hashed]
    assert len(split_text(hashed, DepthLevel.SECTION, TextFormat.MARKDOWN)) == 1


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
            # PLAIN is asserted: the default is OPAQUE, which would (rightly)
            # refuse to split and gap, defeating this test's subject.
            Text(content="Alpha.\n\nBeta.", format=TextFormat.PLAIN),
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
            # PLAIN is asserted: the default is OPAQUE, which would (rightly)
            # refuse to split and gap, defeating this test's subject.
            Text(content="Alpha.\n\nBeta.", format=TextFormat.PLAIN),
            Image(uri="https://example.com/x.png", format="png"),
        ],
    )
    spec = ZoomSpec(default=DepthLevel.PARAGRAPH)

    _, gaps = decompose_node(node, spec)

    assert gaps == []


# ---------------------------------------------------------------------------
# Format-aware splitting: the (format, level) contract
#
# The table below IS the contract (mirrors _SPLIT_RULES / PRD D3). "split"
# means the rule runs; "whole" means it is a true one-piece answer and must
# stay quiet; "gap" means the decomposition was refused and must be visible.


_PROSE = "One. Two.\n\nThree."
_MARKUP = "<h1>T</h1><p>One. Two.</p><h2>N</h2><p>Three.</p>"
_JSON = '{"op": "UPDATE", "bio": "Dr. Smith. Loves cats."}'

# (format, level, body, outcome)
_MATRIX: list[tuple[TextFormat, DepthLevel, str, str]] = [
    (TextFormat.MARKDOWN, DepthLevel.SECTION, DOC, "split"),
    (TextFormat.MARKDOWN, DepthLevel.PARAGRAPH, _PROSE, "split"),
    (TextFormat.MARKDOWN, DepthLevel.SENTENCE, _PROSE, "split"),
    (TextFormat.PLAIN, DepthLevel.SECTION, _PROSE, "whole"),
    (TextFormat.PLAIN, DepthLevel.PARAGRAPH, _PROSE, "split"),
    (TextFormat.PLAIN, DepthLevel.SENTENCE, _PROSE, "split"),
    (TextFormat.TRANSCRIPT, DepthLevel.SECTION, _PROSE, "whole"),
    (TextFormat.TRANSCRIPT, DepthLevel.PARAGRAPH, _PROSE, "split"),
    (TextFormat.TRANSCRIPT, DepthLevel.SENTENCE, _PROSE, "split"),
    (TextFormat.HTML, DepthLevel.SECTION, _MARKUP, "split"),
    (TextFormat.HTML, DepthLevel.PARAGRAPH, _MARKUP, "split"),
    (TextFormat.HTML, DepthLevel.SENTENCE, _MARKUP, "gap"),
    (TextFormat.RST, DepthLevel.SECTION, _PROSE, "gap"),
    (TextFormat.RST, DepthLevel.PARAGRAPH, _PROSE, "split"),
    (TextFormat.RST, DepthLevel.SENTENCE, _PROSE, "split"),
    (TextFormat.CODE, DepthLevel.SECTION, _JSON, "gap"),
    (TextFormat.CODE, DepthLevel.PARAGRAPH, _JSON, "gap"),
    (TextFormat.CODE, DepthLevel.SENTENCE, _JSON, "gap"),
    (TextFormat.OPAQUE, DepthLevel.SECTION, _JSON, "gap"),
    (TextFormat.OPAQUE, DepthLevel.PARAGRAPH, _JSON, "gap"),
    (TextFormat.OPAQUE, DepthLevel.SENTENCE, _JSON, "gap"),
]


@pytest.mark.parametrize(("fmt", "level", "body", "outcome"), _MATRIX)
async def test_format_level_matrix(
    fmt: TextFormat, level: DepthLevel, body: str, outcome: str
) -> None:
    """Each (format, level) pair splits, stays whole quietly, or gaps."""
    node = build_node(kind="doc", atoms=[Text(content=body, format=fmt)])

    result = decompose_result(success(node), ZoomSpec(per_type={AtomKind.TEXT: level}))

    atoms = result.tree.find_atoms(AtomKind.TEXT)
    gaps = result.gaps if isinstance(result, Partial) else []
    if outcome == "split":
        assert len(atoms) > 1, "expected a decomposed tree"
        assert not gaps
    elif outcome == "whole":
        assert len(atoms) == 1
        assert not gaps, "a true one-piece answer must not gap"
    else:
        assert len(atoms) == 1
        assert gaps and gaps[0].kind == ErrorKind.UNSUPPORTED
    # Losslessness holds regardless of outcome.
    assert "".join(atom.content for atom in atoms) == body


@pytest.mark.parametrize("fmt", list(TextFormat))
@pytest.mark.parametrize(
    "level",
    [DepthLevel.SECTION, DepthLevel.PARAGRAPH, DepthLevel.SENTENCE, DepthLevel.MAX],
)
async def test_never_a_silent_no_op(fmt: TextFormat, level: DepthLevel) -> None:
    """The invariant: change the tree, or say why not. Never silently nothing.

    A one-piece result is only allowed to stay quiet when it is a *true*
    answer -- the content has no marker at this level. Anything else must be
    visible as a gap. This sweeps the whole vocabulary, so a TextFormat added
    without a rule fails here rather than silently ignoring zoom.
    """
    body = _MARKUP if fmt is TextFormat.HTML else _PROSE
    node = build_node(kind="doc", atoms=[Text(content=body, format=fmt)])

    result = decompose_result(success(node), ZoomSpec(per_type={AtomKind.TEXT: level}))

    changed = len(result.tree.find_atoms(AtomKind.TEXT)) > 1
    gapped = isinstance(result, Partial) and bool(result.gaps)
    quiet_whole = resolve_level(level, fmt) is not None and not changed
    assert changed or gapped or quiet_whole


@pytest.mark.parametrize("fmt", list(TextFormat))
@pytest.mark.parametrize(
    "level",
    [DepthLevel.SECTION, DepthLevel.PARAGRAPH, DepthLevel.SENTENCE, DepthLevel.MAX],
)
async def test_decomposition_is_idempotent(fmt: TextFormat, level: DepthLevel) -> None:
    """Re-decomposing an already-decomposed tree is a no-op.

    This is what makes central application safe: a connector that already
    decomposed cannot be double-decomposed on the way out.
    """
    body = _MARKUP if fmt is TextFormat.HTML else DOC
    spec = ZoomSpec(per_type={AtomKind.TEXT: level})
    node = build_node(kind="doc", atoms=[Text(content=body, format=fmt)])

    once = decompose_result(success(node), spec)
    twice = decompose_result(once, spec)

    assert twice.model_dump_json() == once.model_dump_json()


# ---------------------------------------------------------------------------
# Named regressions: the specific defects this work exists to fix


async def test_cdc_record_is_not_shredded_into_prose_fragments() -> None:
    """A Postgres CDC record at SENTENCE stays one parseable JSON atom.

    Regression: this previously returned four ``format=code`` atoms split on
    the prose punctuation inside string values -- none of them parseable as
    the JSON they claimed to be -- as a *success* with no gaps.
    """
    record = json.dumps(
        {
            "op": "UPDATE",
            "table": "public.users",
            "new": {"name": "Dr. Smith", "bio": "Loves cats. Hates bugs."},
            "old": {"name": "Dr. Smith", "bio": "Loves cats."},
        }
    )
    node = build_node(kind="change", atoms=[Text(content=record, format=TextFormat.CODE)])

    result = decompose_result(
        success(node), ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
    )

    atoms = result.tree.find_atoms(AtomKind.TEXT)
    assert len(atoms) == 1
    assert json.loads(atoms[0].content) == json.loads(record)
    assert isinstance(result, Partial)
    assert result.gaps[0].kind == ErrorKind.UNSUPPORTED


async def test_broker_payload_is_not_shredded_into_prose_fragments() -> None:
    """An OPAQUE broker/log payload at SENTENCE stays one whole atom.

    The streaming twin of the CDC regression: kafka/tail/redis/sse/websocket
    decode arbitrary bytes, so their payloads must never be prose-split.
    """
    payload = json.dumps({"id": 1, "bio": "Dr. Smith. Loves cats."})
    node = build_node(kind="message", atoms=[Text(content=payload, format=TextFormat.OPAQUE)])

    result = decompose_result(
        success(node), ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
    )

    atoms = result.tree.find_atoms(AtomKind.TEXT)
    assert len(atoms) == 1
    assert json.loads(atoms[0].content) == json.loads(payload)
    assert isinstance(result, Partial) and result.gaps


async def test_source_code_is_not_split_on_prose_punctuation() -> None:
    """Code with sentence punctuation in comments is kept whole, with a gap.

    Regression: this previously produced fragments such as
    ``'\\nimport os\\n\\ndef run():\\n    # Retry on failure.'`` -- still
    labelled ``format=code``, and not valid code.
    """
    source = '# Load the config. Then validate it.\nimport os\n\nprint(os.getenv("X"))\n'
    node = build_node(kind="file", atoms=[Text(content=source, format=TextFormat.CODE)])

    result = decompose_result(
        success(node), ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
    )

    atoms = result.tree.find_atoms(AtomKind.TEXT)
    assert [atom.content for atom in atoms] == [source]
    assert isinstance(result, Partial) and result.gaps


# ---------------------------------------------------------------------------
# HTML splits at element boundaries, never mid-markup


@pytest.mark.parametrize("level", [DepthLevel.SECTION, DepthLevel.PARAGRAPH])
async def test_html_pieces_are_well_formed(level: DepthLevel) -> None:
    """Every HTML piece re-parses to itself: no severed tags."""
    pieces = split_text(_MARKUP, level, TextFormat.HTML)

    assert "".join(pieces) == _MARKUP
    for piece in pieces:
        assert str(BeautifulSoup(piece, "html.parser")) == piece


async def test_html_with_only_nested_blocks_gaps_rather_than_unbalancing() -> None:
    """Nested-only markup is kept whole with a gap, not cut mid-element.

    Cutting at a nested element's offset would sever its ancestors' tags and
    leave both pieces claiming to be HTML while not being HTML.
    """
    nested = "<div><h1>A</h1><p>x</p><h2>B</h2></div>"
    node = build_node(kind="doc", atoms=[Text(content=nested, format=TextFormat.HTML)])

    result = decompose_result(
        success(node), ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.PARAGRAPH})
    )

    atoms = result.tree.find_atoms(AtomKind.TEXT)
    assert [atom.content for atom in atoms] == [nested]
    assert isinstance(result, Partial)
    assert "nested" in (result.gaps[0].detail or "")
