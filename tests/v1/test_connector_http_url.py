"""External-behaviour tests for the v1 ``http_url`` connector.

These tests exercise only the connector's public surface (``stream()`` /
inherited ``fetch()``) and never hit a real network: every test installs a
``httpx.MockTransport`` onto the connector's client via monkeypatch, so the
connector's real request/parse/map logic runs against a canned response.

Asserted external behaviours:

- a 2xx HTML response yields a ``Success`` whose node is a canonical
  ``"webpage"`` with a ``Text`` atom and the HTTP descriptive fields
  recorded in ``source_extra["http_url"]`` (not on the atom);
- a 404 maps to ``error(NOT_FOUND)``;
- other non-2xx statuses map onto their taxonomy kinds;
- a transport failure maps onto ``TRANSIENT``;
- an HTML body renders to real paragraph structure, so ``decompose`` can
  actually split it at ``PARAGRAPH`` instead of returning one giant block;
- a page declaring its encoding only in ``<meta charset>`` decodes correctly
  rather than arriving as U+FFFD mojibake;
- the reserved ``?__omni_text_format=`` key selects the rendering per call,
  never reaches the origin server, and never leaks into recorded URLs.
"""

from __future__ import annotations

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors import http_url as http_url_module
from omni_fetcher.v1.connectors.http_url import (
    SOURCE_NAMESPACE,
    TEXT_FORMAT_PARAM,
    WEBPAGE_KIND,
    HTTPURLConnector,
)
from omni_fetcher.v1.decompose import split_text
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success
from omni_fetcher.v1.zoom import DepthLevel

_HTML = (
    "<html><head><title>Hello Page</title></head>"
    "<body><h1>Header</h1><p>Body text here.</p>"
    "<script>ignored()</script></body></html>"
)

# Prose wrapped in the nav/footer chrome a real article carries, for
# asserting that structure survives and boilerplate does not fuse into it.
#
# Deliberately several paragraphs long rather than two: trafilatura's
# extraction heuristics only behave representatively on a document with
# enough body text to look like an article, and on a two-paragraph stub it
# emits the content twice. A fixture small enough to trigger that would be
# testing the quirk rather than the connector.
_ARTICLE_PARAGRAPHS = [
    "I have observed thousands of founders. Compound yourself relentlessly.",
    "Have almost too much self-belief. It is a delicate balance to strike.",
    "Learn to think independently. Most people follow the nearest crowd.",
    "Get good at sales. Everything is sales, whether or not you call it that.",
    "Take risks while they are cheap. The downside shrinks the earlier you go.",
    "Focus counts more than effort. Pick the right thing, then move fast.",
]
_ARTICLE_HTML = (
    "<html><head><title>How To Be Successful</title></head><body>"
    "<nav><a href='/'>Home</a><a href='/blog'>Blog</a></nav>"
    "<article><h1>How To Be Successful</h1>"
    + "".join(f"<p>{para}</p>" for para in _ARTICLE_PARAGRAPHS)
    + "</article><footer>A posthaven user upvoted this post.</footer>"
    "</body></html>"
)


def _install_transport(monkeypatch, handler) -> None:
    """Force the connector's httpx client to use a MockTransport.

    Patches ``httpx.AsyncClient`` so any construction inside the connector
    is given a ``MockTransport`` driving ``handler``; no socket is opened.
    """
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _html_handler(request: httpx.Request) -> httpx.Response:
    """Respond 200 with a small HTML page."""
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        text=_HTML,
        request=request,
    )


def _status_handler(status_code: int):
    """Build a handler that always responds with a fixed status code."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="nope", request=request)

    return handler


async def test_html_success_yields_canonical_webpage(monkeypatch) -> None:
    """A 2xx HTML response yields a canonical ``"webpage"`` Success."""
    _install_transport(monkeypatch, _html_handler)
    connector = HTTPURLConnector()

    result = await connector.fetch("https://example.com/page")

    assert isinstance(result, Success)
    node = result.tree
    # Semantic kind and source url on the metadata core.
    assert node.metadata.kind == WEBPAGE_KIND
    assert node.metadata.source_url == "https://example.com/page"

    # Content is carried as Text atoms (title + body), none of them HTML kind.
    atoms = list(node.iter_atoms())
    assert atoms, "expected at least one content atom"
    assert all(atom.kind is AtomKind.TEXT for atom in atoms)
    # Whichever rendering ran, the declared format names what the bytes are
    # and matches what source_extra reports was produced.
    assert all(atom.format in (TextFormat.PLAIN, TextFormat.MARKDOWN) for atom in atoms)
    assert atoms[-1].format.value == node.metadata.source_extra[SOURCE_NAMESPACE]["text_format"]
    contents = [atom.content for atom in atoms]
    assert "Hello Page" in contents  # title atom
    body_text = "\n".join(contents)
    assert "Body text here." in body_text
    # Script content is stripped, never surfaced as content.
    assert "ignored()" not in body_text

    # HTTP descriptive fields live in source_extra, not on the atoms.
    extra = node.metadata.source_extra[SOURCE_NAMESPACE]
    assert extra["status_code"] == 200
    assert extra["mime_type"] == "text/html"
    assert extra["final_url"] == "https://example.com/page"
    assert "content-type" in extra["headers"]
    # Atoms carry content only -- no descriptive HTTP fields leak onto them.
    for atom in atoms:
        dumped = atom.model_dump()
        assert "status_code" not in dumped
        assert "headers" not in dumped


async def test_404_maps_to_not_found(monkeypatch) -> None:
    """A 404 response maps to a typed NOT_FOUND error (never raised)."""
    _install_transport(monkeypatch, _status_handler(404))
    connector = HTTPURLConnector()

    result = await connector.fetch("https://example.com/missing")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND
    assert result.locator == "https://example.com/missing"


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, ErrorKind.AUTH_FAILED),
        (403, ErrorKind.PERMISSION_DENIED),
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.TRANSIENT),
        (503, ErrorKind.TRANSIENT),
        (400, ErrorKind.INVALID_INPUT),
    ],
)
async def test_non_2xx_status_maps_to_taxonomy(monkeypatch, status, kind) -> None:
    """Each non-2xx status maps onto its expected taxonomy kind."""
    _install_transport(monkeypatch, _status_handler(status))
    connector = HTTPURLConnector()

    result = await connector.fetch("https://example.com/x")

    assert isinstance(result, Error)
    assert result.kind is kind


async def test_transport_error_maps_to_transient(monkeypatch) -> None:
    """A transport-level failure is returned as a TRANSIENT error."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_transport(monkeypatch, boom)
    connector = HTTPURLConnector()

    result = await connector.fetch("https://example.com/down")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.TRANSIENT


async def test_non_html_text_becomes_plain_text_atom(monkeypatch) -> None:
    """A non-HTML text response is carried as a single plain Text atom."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="just some text",
            request=request,
        )

    _install_transport(monkeypatch, handler)
    connector = HTTPURLConnector()

    result = await connector.fetch("https://example.com/plain.txt")

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].content == "just some text"
    assert atoms[0].format is TextFormat.PLAIN


async def test_stream_yields_single_item_with_temporal(monkeypatch) -> None:
    """stream() yields exactly one node carrying a temporal position."""
    _install_transport(monkeypatch, _html_handler)
    connector = HTTPURLConnector()

    items = []
    async for item in connector.stream("https://example.com/page"):
        items.append(item)

    assert len(items) == 1
    assert isinstance(items[0], Success)
    temporal = items[0].tree.metadata.temporal
    assert temporal.sequence == 0
    assert temporal.timestamp is not None


def _article_handler(request: httpx.Request) -> httpx.Response:
    """Respond 200 with a chrome-wrapped two-paragraph article."""
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        text=_ARTICLE_HTML,
        request=request,
    )


async def test_plain_rendering_has_splittable_paragraphs(monkeypatch) -> None:
    """PLAIN output carries blank lines, so PARAGRAPH decomposition works.

    This is the regression that matters most: text joined with single
    newlines has no "\\n\\n", so decompose's PARAGRAPH rule returns the whole
    page as one block while reporting no gap at all.
    """
    _install_transport(monkeypatch, _article_handler)
    connector = HTTPURLConnector(text_format=TextFormat.PLAIN)

    result = await connector.fetch(
        "https://blog.example.com/successful",
    )

    assert isinstance(result, (Success, Partial))
    body = [a for a in result.tree.iter_atoms() if a.format is TextFormat.PLAIN][-1]
    assert "\n\n" in body.content, "no paragraph breaks survived extraction"

    pieces = split_text(body.content, DepthLevel.PARAGRAPH, TextFormat.PLAIN)
    assert len(pieces) > 1, "page still collapses to a single paragraph"
    # Losslessness is the contract split_text promises.
    assert "".join(pieces) == body.content


async def test_plain_rendering_never_fuses_chrome_into_prose(monkeypatch) -> None:
    """Nav/footer chrome lands in its own blocks, never welded onto a sentence.

    PLAIN keeps chrome by design -- removing it is what MARKDOWN is for -- but
    a piece that is part menu and part article is contaminated in a way no
    downstream consumer can undo.
    """
    _install_transport(monkeypatch, _article_handler)
    connector = HTTPURLConnector(text_format=TextFormat.PLAIN)

    result = await connector.fetch("https://blog.example.com/successful")

    body = [a for a in result.tree.iter_atoms() if a.format is TextFormat.PLAIN][-1]
    pieces = split_text(body.content, DepthLevel.PARAGRAPH, TextFormat.PLAIN)

    # Every prose paragraph survives as a piece of its own, uncontaminated.
    for para in _ARTICLE_PARAGRAPHS:
        owning = [p for p in pieces if para in p]
        assert len(owning) == 1, f"paragraph not cleanly separated: {para!r}"
        assert "Home" not in owning[0]
        assert "posthaven" not in owning[0]

    # The <title> is carried as its own atom and must not also open the body.
    assert not body.content.startswith("How To Be Successful How To Be")


async def test_markdown_rendering_drops_chrome_and_keeps_structure(monkeypatch) -> None:
    """The default markdown rendering removes chrome and keeps headings."""
    pytest.importorskip("trafilatura")
    _install_transport(monkeypatch, _article_handler)

    result = await HTTPURLConnector().fetch("https://blog.example.com/successful")

    assert isinstance(result, Success)
    extra = result.tree.metadata.source_extra[SOURCE_NAMESPACE]
    assert extra["text_format"] == "markdown"

    body = list(result.tree.iter_atoms())[-1]
    assert body.format is TextFormat.MARKDOWN

    # Boilerplate is gone -- the whole point of the markdown path.
    assert "Home" not in body.content
    assert "posthaven" not in body.content
    # ...and the article itself is intact, exactly once.
    for para in _ARTICLE_PARAGRAPHS:
        assert body.content.count(para) == 1, f"paragraph not present exactly once: {para!r}"

    # MARKDOWN is the only format decompose splits at all three levels.
    for level in (DepthLevel.SECTION, DepthLevel.PARAGRAPH, DepthLevel.SENTENCE):
        pieces = split_text(body.content, level, TextFormat.MARKDOWN)
        assert "".join(pieces) == body.content
    assert len(split_text(body.content, DepthLevel.PARAGRAPH, TextFormat.MARKDOWN)) > 1


async def test_meta_charset_page_decodes_without_mojibake(monkeypatch) -> None:
    """A page declaring its charset only in <meta> decodes correctly.

    httpx honours only the Content-Type header and otherwise assumes utf-8
    with errors="replace", so decoding before the parser sees the markup
    turns a cp1252 apostrophe into U+FFFD.
    """
    raw = (
        b"<html><head><meta charset='windows-1252'><title>T</title></head>"
        b"<body><p>I\x92ve observed thousands of founders.</p></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        # No charset in the header: the <meta> tag is the only declaration.
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=raw,
            request=request,
        )

    _install_transport(monkeypatch, handler)
    connector = HTTPURLConnector(text_format=TextFormat.PLAIN)

    result = await connector.fetch("https://example.com/cp1252")

    assert isinstance(result, (Success, Partial))
    text = "\n".join(a.content for a in result.tree.iter_atoms())
    assert "�" not in text, "content arrived as replacement characters"
    assert "I’ve observed" in text


async def test_reserved_param_selects_plain_and_never_leaves_process(monkeypatch) -> None:
    """?__omni_text_format= picks the rendering and is stripped from the request."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _article_handler(request)

    _install_transport(monkeypatch, handler)
    connector = HTTPURLConnector()  # default markdown

    result = await connector.fetch(
        f"https://example.com/post?page=2&{TEXT_FORMAT_PARAM}=plain",
    )

    assert isinstance(result, (Success, Partial))
    # The reserved key never reached the origin server, and the real query
    # parameter beside it survived untouched.
    assert len(seen) == 1
    assert TEXT_FORMAT_PARAM not in seen[0]
    assert "page=2" in seen[0]

    # Nor does it leak into the recorded URLs.
    extra = result.tree.metadata.source_extra[SOURCE_NAMESPACE]
    assert TEXT_FORMAT_PARAM not in extra["requested_url"]
    assert TEXT_FORMAT_PARAM not in extra["final_url"]
    assert TEXT_FORMAT_PARAM not in (result.tree.metadata.source_url or "")

    # The override took effect.
    assert extra["text_format"] == "plain"
    body = list(result.tree.iter_atoms())[-1]
    assert body.format is TextFormat.PLAIN


async def test_url_without_reserved_param_is_untouched(monkeypatch) -> None:
    """A normal URL is passed through byte-identically, never re-encoded."""
    seen: list[str] = []
    target = "https://example.com/s?q=a%20b&tags=x,y&empty="

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return _article_handler(request)

    _install_transport(monkeypatch, handler)

    result = await HTTPURLConnector().fetch(target)

    assert isinstance(result, (Success, Partial))
    assert seen == [target]


async def test_bad_reserved_param_value_is_invalid_input(monkeypatch) -> None:
    """An unreadable format value is a typed INVALID_INPUT, never a raise."""
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return _article_handler(request)

    _install_transport(monkeypatch, handler)

    result = await HTTPURLConnector().fetch(
        f"https://example.com/post?{TEXT_FORMAT_PARAM}=yaml",
    )

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.INVALID_INPUT
    # The bad request was rejected at the boundary, before any network call.
    assert called == []


def test_constructor_rejects_a_non_renderable_format() -> None:
    """Only the two renderable formats are accepted as a default."""
    with pytest.raises(ValueError):
        HTTPURLConnector(text_format=TextFormat.CODE)


async def test_explicit_markdown_without_trafilatura_gaps(monkeypatch) -> None:
    """An unmeetable *explicit* markdown ask degrades to PLAIN plus a Gap."""
    monkeypatch.setattr(http_url_module, "TRAFILATURA_AVAILABLE", False)
    _install_transport(monkeypatch, _article_handler)

    result = await HTTPURLConnector(text_format=TextFormat.MARKDOWN).fetch(
        "https://example.com/post",
    )

    assert isinstance(result, Partial)
    assert any(g.kind is ErrorKind.UNSUPPORTED for g in result.gaps)
    assert any("trafilatura" in (g.detail or "") for g in result.gaps)
    # Degraded honestly: PLAIN content is labelled PLAIN, never MARKDOWN.
    body = list(result.tree.iter_atoms())[-1]
    assert body.format is TextFormat.PLAIN
    assert result.tree.metadata.source_extra[SOURCE_NAMESPACE]["text_format"] == "plain"


async def test_defaulted_markdown_without_trafilatura_stays_quiet(monkeypatch) -> None:
    """Defaulting to markdown records no Gap -- only an explicit ask does.

    Otherwise every HTML fetch on a base install would return Partial.
    """
    monkeypatch.setattr(http_url_module, "TRAFILATURA_AVAILABLE", False)
    _install_transport(monkeypatch, _article_handler)

    result = await HTTPURLConnector().fetch("https://example.com/post")

    assert isinstance(result, Success)
    # Silent about the gap, but not about the outcome.
    assert result.tree.metadata.source_extra[SOURCE_NAMESPACE]["text_format"] == "plain"
