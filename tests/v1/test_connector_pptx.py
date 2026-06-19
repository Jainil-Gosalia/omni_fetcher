"""External-behaviour tests for the v1 PPTX connector.

These tests exercise only the connector's public contract -- never its
internals -- by monkeypatching ``pptx.Presentation`` with a tiny in-memory
fake that mimics the slice of the python-pptx API the connector consumes. No
real ``.pptx`` file is read and no network is touched.

What is asserted:

- a healthy deck yields a ``Success`` whose tree is a deck node
  (``kind="presentation"``) whose children are slide nodes
  (``kind="slide"``) carrying ``Text`` / ``Image`` / ``Table`` content atoms;
- descriptive presentation/slide fields live in the metadata core and the
  namespaced ``source_extra["pptx"]`` mapping, never inline on atoms;
- a corrupt deck (``Presentation`` raises) yields a typed
  ``error(PARSE_ERROR)``;
- a deck whose individual slide fails to extract yields a ``Partial`` whose
  gaps name the skipped slide, never a silent success.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Optional

import pytest

from omni_fetcher.v1.atoms import AtomKind
from omni_fetcher.v1.connectors.pptx import PptxConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Error, Partial, Success


# ---------------------------------------------------------------------------
# In-memory fakes mimicking the python-pptx surface the connector uses.


class _FakeCell:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeRow:
    def __init__(self, cells: list[str]) -> None:
        self.cells = [_FakeCell(c) for c in cells]


class _FakeTable:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = [_FakeRow(r) for r in rows]


class _FakeImage:
    def __init__(self, blob: bytes, content_type: str) -> None:
        self.blob = blob
        self.content_type = content_type


class _FakeShape:
    """A shape that may carry text, an image, or a table."""

    def __init__(
        self,
        *,
        text: str = "",
        image: Optional[_FakeImage] = None,
        table: Optional[_FakeTable] = None,
    ) -> None:
        self.text = text
        if image is not None:
            self.image = image
        self.has_table = table is not None
        if table is not None:
            self.table = table


class _FakeShapes(list):
    """A shape collection exposing the ``title`` accessor like python-pptx."""

    def __init__(self, shapes: list[_FakeShape], title: Any) -> None:
        super().__init__(shapes)
        self.title = title


class _FakeNotesFrame:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeNotesSlide:
    def __init__(self, text: str) -> None:
        self.notes_text_frame = _FakeNotesFrame(text)


class _FakeSlide:
    def __init__(
        self,
        *,
        title: Optional[_FakeShape],
        body: list[_FakeShape],
        notes: Optional[str] = None,
        explode: bool = False,
    ) -> None:
        ordered = ([title] if title is not None else []) + body
        self.shapes = _FakeShapes(ordered, title)
        self._notes = notes
        self._explode = explode

    @property
    def has_notes_slide(self) -> bool:
        if self._explode:
            raise RuntimeError("boom: slide extraction failed")
        return self._notes is not None

    @property
    def notes_slide(self) -> _FakeNotesSlide:
        return _FakeNotesSlide(self._notes or "")


class _FakeCoreProps:
    def __init__(self, title: str, author: str) -> None:
        self.title = title
        self.author = author


class _FakePresentation:
    def __init__(self, slides: list[_FakeSlide], title: str, author: str):
        self.slides = slides
        self.core_properties = _FakeCoreProps(title, author)


def _install_fake_pptx(
    monkeypatch: pytest.MonkeyPatch,
    factory: Any,
) -> None:
    """Register a fake ``pptx`` module and stub the byte-loading boundary.

    ``Presentation`` is replaced with ``factory`` so no real ``.pptx`` is
    parsed, and the connector's local-file read is stubbed to return dummy
    bytes so the filesystem (and any network) is never touched.
    """
    module = types.ModuleType("pptx")
    module.Presentation = factory
    monkeypatch.setitem(sys.modules, "pptx", module)
    monkeypatch.setattr(
        PptxConnector,
        "_read_local",
        staticmethod(lambda _uri: b"fake-pptx-bytes"),
    )


def _healthy_deck() -> _FakePresentation:
    """A two-slide deck with text, an image, a table, and speaker notes."""
    title1 = _FakeShape(text="Quarterly Review")
    body1 = _FakeShape(text="Revenue is up.")
    picture = _FakeShape(image=_FakeImage(b"\x89PNG-bytes", "image/png"))
    slide1 = _FakeSlide(
        title=title1,
        body=[body1, picture],
        notes="Remember to smile.",
    )

    title2 = _FakeShape(text="Numbers")
    table = _FakeShape(
        table=_FakeTable([["Q1", "Q2"], ["10", "20"]]),
    )
    slide2 = _FakeSlide(title=title2, body=[table])

    return _FakePresentation(
        [slide1, slide2],
        title="Deck Title",
        author="Ada Lovelace",
    )


# ---------------------------------------------------------------------------
# Tests


async def test_healthy_deck_yields_presentation_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy deck -> Success with deck -> slide -> atom tree."""
    _install_fake_pptx(monkeypatch, lambda _stream: _healthy_deck())

    result = await PptxConnector().fetch("file:///deck.pptx")

    assert isinstance(result, Success)
    deck = result.tree
    assert deck.metadata.kind == "presentation"

    # Children are slide nodes (the recursive composition tree).
    slides = list(deck.iter_children())
    assert len(slides) == 2
    assert all(isinstance(s, CompositionNode) for s in slides)
    assert [s.metadata.kind for s in slides] == ["slide", "slide"]


async def test_slides_carry_text_image_and_table_atoms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each slide's content is canonical Text / Image / Table atoms."""
    _install_fake_pptx(monkeypatch, lambda _stream: _healthy_deck())

    result = await PptxConnector().fetch("file:///deck.pptx")
    assert isinstance(result, Success)
    slide1, slide2 = list(result.tree.iter_children())

    # Slide 1: text (title + body + notes) and an image.
    texts = slide1.find_atoms(AtomKind.TEXT)
    assert len(texts) == 1
    assert "Quarterly Review" in texts[0].content
    assert "Revenue is up." in texts[0].content
    assert "[Speaker Notes]: Remember to smile." in texts[0].content

    images = slide1.find_atoms(AtomKind.IMAGE)
    assert len(images) == 1
    assert images[0].format == "image/png"
    assert images[0].data == b"\x89PNG-bytes"

    # Slide 2: a table atom with the grid contents.
    tables = slide2.find_atoms(AtomKind.TABLE)
    assert len(tables) == 1
    assert tables[0].rows == [["Q1", "Q2"], ["10", "20"]]


async def test_descriptive_fields_live_in_source_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deck/slide descriptive data is namespaced in source_extra, not atoms."""
    _install_fake_pptx(monkeypatch, lambda _stream: _healthy_deck())

    result = await PptxConnector().fetch("file:///deck.pptx")
    assert isinstance(result, Success)
    deck = result.tree

    # Presentation-level descriptive fields: core + source_extra["pptx"].
    assert deck.metadata.author == "Ada Lovelace"
    pptx_extra = deck.metadata.source_extra["pptx"]
    assert pptx_extra["slide_count"] == 2
    assert pptx_extra["title"] == "Deck Title"
    assert pptx_extra["author"] == "Ada Lovelace"

    # Per-slide descriptive data lives in that slide node's source_extra.
    slide1 = list(deck.iter_children())[0]
    slide_extra = slide1.metadata.source_extra["pptx"]
    assert slide_extra["slide_number"] == 1
    assert slide_extra["title"] == "Quarterly Review"

    # Atoms are content-only: no descriptive fields leak onto them.
    text_atom = slide1.find_atoms(AtomKind.TEXT)[0]
    assert set(text_atom.model_dump().keys()) == {
        "kind",
        "content",
        "format",
        "language",
        "encoding",
    }


async def test_corrupt_deck_yields_typed_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deck that cannot be opened -> typed error(PARSE_ERROR)."""

    def _explode(_stream: Any) -> _FakePresentation:
        raise ValueError("not a zip file")

    _install_fake_pptx(monkeypatch, _explode)

    result = await PptxConnector().fetch("file:///corrupt.pptx")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.PARSE_ERROR
    assert result.locator == "file:///corrupt.pptx"


async def test_slide_failure_yields_partial_with_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slide that fails to extract -> Partial whose gap names the slide."""
    good = _FakeSlide(title=_FakeShape(text="Good"), body=[])
    bad = _FakeSlide(title=_FakeShape(text="Bad"), body=[], explode=True)
    deck = _FakePresentation([good, bad], title="T", author="A")
    _install_fake_pptx(monkeypatch, lambda _stream: deck)

    result = await PptxConnector().fetch("file:///mixed.pptx")

    assert isinstance(result, Partial)
    # The good slide is preserved in the tree.
    kinds = [c.metadata.kind for c in result.tree.iter_children()]
    assert kinds == ["slide"]
    # The failed slide is surfaced as a typed gap, not dropped.
    assert len(result.gaps) == 1
    assert result.gaps[0].kind is ErrorKind.PARSE_ERROR
    assert result.gaps[0].locator == "file:///mixed.pptx#slide=2"


def test_can_handle_matches_pptx_suffix() -> None:
    """can_handle claims .pptx URIs (case-insensitive) and nothing else."""
    assert PptxConnector.can_handle("file:///deck.PPTX") is True
    assert PptxConnector.can_handle("https://x/y.pptx") is True
    assert PptxConnector.can_handle("file:///doc.docx") is False
