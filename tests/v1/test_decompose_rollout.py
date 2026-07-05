"""Text-decomposition rollout tests: http_url, pdf, docx, pptx (issue 009).

The tracer-proven splitter (issue 008) is wired into the remaining
text-bearing connectors. Each is driven end to end through ``fetch()``:
http_url against a mock transport, docx against a real generated document,
pdf/pptx through their scripted parse seams (mirroring their own suites).
pptx maps SECTION onto its existing slide structure instead of re-splitting
flattened text; no connector changes behavior when zoom is omitted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import docx as python_docx
import httpx
import pytest

from omni_fetcher.v1 import DepthLevel, ZoomSpec
from omni_fetcher.v1.atoms import AtomKind, Text
from omni_fetcher.v1.connectors.docx import DocxConnector
from omni_fetcher.v1.connectors.http_url import HTTPURLConnector
from omni_fetcher.v1.connectors.pdf import PDFConnector
from omni_fetcher.v1.connectors.pptx import PptxConnector
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Success, success

pytestmark = pytest.mark.asyncio

PARAGRAPH_TEXT = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.PARAGRAPH})
SENTENCE_TEXT = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
SECTION_TEXT = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SECTION})


def _all_text(node: CompositionNode) -> str:
    return "".join(atom.content for atom in node.find_atoms(AtomKind.TEXT))


def _install_transport(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> None:
    """Force every ``httpx.AsyncClient`` onto a fixed-response transport."""
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda request: response)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


# ---------------------------------------------------------------------------
# http_url


async def test_http_url_paragraph_zoom_decomposes_page_text(monkeypatch) -> None:
    """A plain-text page at PARAGRAPH yields per-block child nodes."""
    body = "First block.\n\nSecond block.\n\nThird block."
    response = httpx.Response(
        status_code=200,
        headers={"content-type": "text/plain"},
        content=body.encode("utf-8"),
    )
    _install_transport(monkeypatch, response)

    natural = await HTTPURLConnector().fetch("https://example.com/plain.txt")
    zoomed = await HTTPURLConnector().fetch("https://example.com/plain.txt", zoom=PARAGRAPH_TEXT)

    assert isinstance(natural, Success) and isinstance(zoomed, Success)
    assert len(zoomed.tree.find_by_kind("paragraph")) == 3
    assert _all_text(zoomed.tree) == _all_text(natural.tree)


# ---------------------------------------------------------------------------
# pdf (scripted seams, mirroring its own suite style)


def _scripted_pdf(node: CompositionNode) -> PDFConnector:
    connector = PDFConnector()

    async def read_bytes(uri: str) -> bytes:
        return b"%PDF-fake"

    connector._read_bytes = read_bytes  # type: ignore[method-assign]
    connector._extract = lambda data: {}  # type: ignore[method-assign]
    connector._build_result = lambda uri, extraction: success(node)  # type: ignore[method-assign]
    return connector


async def test_pdf_sentence_zoom_decomposes_document_text() -> None:
    """The pdf stream path decomposes its document text at SENTENCE."""
    node = build_node(kind="document", atoms=[Text(content="One. Two. Three.")])
    connector = _scripted_pdf(node)

    result = await connector.fetch("file:///paper.pdf", zoom=SENTENCE_TEXT)

    assert isinstance(result, Success)
    sentences = result.tree.find_by_kind("sentence")
    assert len(sentences) == 3
    assert _all_text(result.tree) == "One. Two. Three."


async def test_pdf_without_zoom_is_untouched() -> None:
    """No spec, no decomposition -- the natural node passes through."""
    node = build_node(kind="document", atoms=[Text(content="One. Two. Three.")])
    connector = _scripted_pdf(node)

    result = await connector.fetch("file:///paper.pdf")

    assert isinstance(result, Success)
    assert result.tree.find_by_kind("sentence") == []


# ---------------------------------------------------------------------------
# docx (real generated document)


async def test_docx_sentence_zoom_decomposes_paragraph_atoms(tmp_path: Path) -> None:
    """A real .docx paragraph splits into sentence nodes at SENTENCE."""
    path = tmp_path / "sample.docx"
    document = python_docx.Document()
    document.add_paragraph("Alpha one. Alpha two! Alpha three?")
    document.save(str(path))

    natural = await DocxConnector().fetch(str(path))
    zoomed = await DocxConnector().fetch(str(path), zoom=SENTENCE_TEXT)

    assert isinstance(natural, Success) and isinstance(zoomed, Success)
    sentences = zoomed.tree.find_by_kind("sentence")
    assert len(sentences) == 3
    assert _all_text(zoomed.tree) == _all_text(natural.tree)


# ---------------------------------------------------------------------------
# pptx (scripted parse seam; SECTION maps to slides, not re-splitting)


def _scripted_pptx(tree: CompositionNode) -> PptxConnector:
    connector = PptxConnector()

    async def load_bytes(uri: str, auth: Any) -> bytes:
        return b"PK-fake"

    connector._load_bytes = load_bytes  # type: ignore[method-assign]
    connector._build_tree = lambda data, uri: (tree, [])  # type: ignore[method-assign]
    return connector


def _deck() -> CompositionNode:
    slide_one = build_node(kind="slide", atoms=[Text(content="Intro. Agenda.")])
    slide_two = build_node(kind="slide", atoms=[Text(content="Body. Close.")])
    return build_node(kind="presentation", children=[slide_one, slide_two])


async def test_pptx_section_zoom_keeps_the_natural_slide_structure() -> None:
    """SECTION preserves slides as the deck's sections -- no re-splitting."""
    connector = _scripted_pptx(_deck())

    result = await connector.fetch("file:///deck.pptx", zoom=SECTION_TEXT)

    assert isinstance(result, Success)
    assert len(result.tree.find_by_kind("slide")) == 2
    assert result.tree.find_by_kind("section") == []
    assert result.tree.find_by_kind("sentence") == []


async def test_pptx_sentence_zoom_decomposes_slide_text() -> None:
    """Finer-than-slide levels decompose each slide's text atoms."""
    connector = _scripted_pptx(_deck())

    result = await connector.fetch("file:///deck.pptx", zoom=SENTENCE_TEXT)

    assert isinstance(result, Success)
    assert len(result.tree.find_by_kind("slide")) == 2
    assert len(result.tree.find_by_kind("sentence")) == 4
    assert _all_text(result.tree) == "Intro. Agenda.Body. Close."
