"""External-behaviour tests for the v1 ``rss`` connector.

These tests exercise only the public surface of ``RSSConnector`` via
``fetch()`` (the inherited base sugar over ``stream()``). No real network is
used: ``feedparser.parse`` is monkeypatched so the connector parses an
in-test feed document (or a stubbed parse result) rather than reaching out.

What is asserted (behaviour, not internals):

- a well-formed feed yields a ``Success`` whose tree is a canonical
  ``"feed"`` container node with one ``"feed_item"`` child per entry;
- each item's body lives in a ``Text`` atom, and item descriptive fields
  (title, link, published, author, guid) live in
  ``source_extra["rss"]`` -- never inline on the atom;
- feed-level descriptive fields (title, link, description, updated) live in
  the feed node's ``source_extra["rss"]``;
- a malformed/non-feed document yields a typed ``Error`` (PARSE_ERROR /
  NOT_FOUND);
- a feed that parsed with warnings but still produced items yields a
  ``Partial`` carrying the items plus a parse-warning gap.
"""

from __future__ import annotations

import feedparser
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors import rss as rss_module
from omni_fetcher.v1.connectors.rss import RSSConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success

pytestmark = pytest.mark.asyncio


RSS_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <link>https://example.com/</link>
    <description>An example feed.</description>
    <language>en-us</language>
    <lastBuildDate>Mon, 01 Jan 2024 00:00:00 GMT</lastBuildDate>
    <item>
      <title>First Post</title>
      <link>https://example.com/posts/1</link>
      <description>The body of the first post.</description>
      <author>alice@example.com</author>
      <pubDate>Tue, 02 Jan 2024 00:00:00 GMT</pubDate>
      <guid>post-1</guid>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://example.com/posts/2</link>
      <description>The body of the second post.</description>
      <author>bob@example.com</author>
      <pubDate>Wed, 03 Jan 2024 00:00:00 GMT</pubDate>
      <guid>post-2</guid>
    </item>
  </channel>
</rss>
"""


def _install_feed(monkeypatch, document):
    """Force the connector's ``feedparser.parse`` to parse ``document``.

    The connector may pass a URL and request headers; we ignore both and
    parse the in-test ``document`` so no network is touched while still
    exercising the real feedparser data shape.

    ``feedparser.parse`` must be captured before the patch: the stub and the
    connector share the one feedparser module object, so calling
    ``feedparser.parse`` inside the stub would invoke the stub itself and
    recurse until the interpreter's recursion limit.
    """
    real_parse = feedparser.parse

    def fake_parse(uri, *args, **kwargs):
        return real_parse(document)

    monkeypatch.setattr(rss_module.feedparser, "parse", fake_parse)


def _install_result(monkeypatch, result):
    """Force ``feedparser.parse`` to return a pre-built ``result`` object."""

    def fake_parse(uri, *args, **kwargs):
        return result

    monkeypatch.setattr(rss_module.feedparser, "parse", fake_parse)


async def test_well_formed_feed_yields_feed_container(monkeypatch):
    """A well-formed feed yields a ``"feed"`` node with item children."""
    _install_feed(monkeypatch, RSS_DOC)

    result = await RSSConnector().fetch("https://example.com/feed.xml")

    assert isinstance(result, Success)
    feed = result.tree
    assert feed.metadata.kind == "feed"

    items = feed.find_by_kind("feed_item")
    assert len(items) == 2
    # The feed container carries no content atoms of its own; content lives
    # on the item children.
    assert [c for c in feed.children] == items


async def test_item_content_in_text_atom(monkeypatch):
    """Each item's body lives in a Text atom on the item node."""
    _install_feed(monkeypatch, RSS_DOC)

    result = await RSSConnector().fetch("https://example.com/feed.xml")

    assert isinstance(result, Success)
    items = result.tree.find_by_kind("feed_item")

    first = items[0]
    texts = first.find_atoms(AtomKind.TEXT)
    assert len(texts) == 1
    assert texts[0].content == "The body of the first post."
    assert texts[0].format in (TextFormat.PLAIN, TextFormat.HTML)


async def test_item_descriptive_fields_in_source_extra(monkeypatch):
    """Item descriptive fields live in source_extra, not on the atom."""
    _install_feed(monkeypatch, RSS_DOC)

    result = await RSSConnector().fetch("https://example.com/feed.xml")

    assert isinstance(result, Success)
    first = result.tree.find_by_kind("feed_item")[0]

    extra = first.metadata.source_extra["rss"]
    assert extra["title"] == "First Post"
    assert extra["link"] == "https://example.com/posts/1"
    assert extra["guid"] == "post-1"
    assert extra["author"] == "alice@example.com"
    assert extra["published"]  # the raw pubDate string is preserved

    # The Text atom carries content only -- no descriptive leakage.
    text_atom = first.find_atoms(AtomKind.TEXT)[0]
    dumped = text_atom.model_dump()
    assert set(dumped) <= {"kind", "content", "format", "language", "encoding"}


async def test_feed_level_descriptive_in_source_extra(monkeypatch):
    """Feed-level descriptive fields live in the feed node's source_extra."""
    _install_feed(monkeypatch, RSS_DOC)

    result = await RSSConnector().fetch("https://example.com/feed.xml")

    assert isinstance(result, Success)
    extra = result.tree.metadata.source_extra["rss"]
    assert extra["title"] == "Example Feed"
    assert extra["link"] == "https://example.com/"
    assert extra["description"] == "An example feed."
    assert extra["language"] == "en-us"
    assert extra["updated"]  # lastBuildDate preserved as the feed's updated


async def test_html_content_marked_html(monkeypatch):
    """An item whose body is HTML is marked TextFormat.HTML."""
    doc = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <title>HTML Feed</title>
      <link>https://example.com/</link>
      <item>
        <title>Rich</title>
        <link>https://example.com/rich</link>
        <guid>rich-1</guid>
        <content:encoded
          xmlns:content="http://purl.org/rss/1.0/modules/content/">
          <![CDATA[<p>Hello <b>world</b></p>]]>
        </content:encoded>
      </item>
    </channel></rss>"""
    _install_feed(monkeypatch, doc)

    result = await RSSConnector().fetch("https://example.com/feed")

    assert isinstance(result, Success)
    item = result.tree.find_by_kind("feed_item")[0]
    text = item.find_atoms(AtomKind.TEXT)[0]
    assert text.format == TextFormat.HTML
    assert "world" in text.content


async def test_malformed_document_yields_error(monkeypatch):
    """A non-feed document yields a typed Error, not a raise."""
    _install_feed(monkeypatch, "this is definitely not a feed <<< not xml")

    result = await RSSConnector().fetch("https://example.com/feed")

    assert isinstance(result, Error)
    assert result.kind in (ErrorKind.PARSE_ERROR, ErrorKind.NOT_FOUND)


async def test_empty_source_yields_not_found(monkeypatch):
    """An empty source (no feed, no entries) yields NOT_FOUND."""
    _install_feed(monkeypatch, "")

    result = await RSSConnector().fetch("https://example.com/empty")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_warning_feed_with_items_yields_partial(monkeypatch):
    """A feed flagged bozo but with usable items yields a Partial."""
    # Parse a clean feed, then stub the flags to simulate a parser that
    # produced items while flagging a recoverable warning.
    parsed = feedparser.parse(RSS_DOC)
    parsed["bozo"] = 1
    parsed["bozo_exception"] = ValueError("undeclared namespace")
    _install_result(monkeypatch, parsed)

    result = await RSSConnector().fetch("https://example.com/feed.xml")

    assert isinstance(result, Partial)
    assert len(result.tree.find_by_kind("feed_item")) == 2
    assert result.gaps
    assert result.gaps[0].kind == ErrorKind.PARSE_ERROR


async def test_parse_failure_yields_transient(monkeypatch):
    """An unexpected parser exception is returned as a TRANSIENT error."""

    def boom(uri, *args, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(rss_module.feedparser, "parse", boom)

    result = await RSSConnector().fetch("https://example.com/feed")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.TRANSIENT


async def test_auth_headers_passed_to_parser(monkeypatch):
    """A bearer credential is resolved into the parser's request headers."""
    from omni_fetcher.v1.auth import BearerAuth

    captured = {}
    real_parse = feedparser.parse  # capture before the patch; see _install_feed

    def fake_parse(uri, *args, **kwargs):
        captured["headers"] = kwargs.get("request_headers")
        return real_parse(RSS_DOC)

    monkeypatch.setattr(rss_module.feedparser, "parse", fake_parse)

    result = await RSSConnector().fetch(
        "https://example.com/feed.xml",
        auth=BearerAuth(token="s3cret"),
    )

    assert isinstance(result, Success)
    assert captured["headers"] == {"Authorization": "Bearer s3cret"}
