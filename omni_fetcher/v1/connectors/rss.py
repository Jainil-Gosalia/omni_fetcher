"""The canonical ``rss`` connector for the OmniFetcher v1 contract.

Fetches and parses an RSS/Atom feed and emits it as a canonical
``CompositionNode`` tree wrapped in a ``Result``. A feed is a natural
*container* node (advisory ``kind`` ``"feed"``) whose children are the feed's
items, each a node (advisory ``kind`` ``"feed_item"``).

Content lives in atoms, description lives in metadata:

- An item's textual body (its ``content``, falling back to its ``summary``)
  becomes a ``Text`` atom on that item node. The atom's ``format`` is
  ``TextFormat.HTML`` when feedparser reports HTML, otherwise
  ``TextFormat.PLAIN``.
- An item's descriptive fields (title, link, published, author, guid) are
  filed under that item node's ``source_extra["rss"]`` -- never inline on the
  atom.
- The feed's descriptive fields (title, link, description, updated, language)
  are filed under the feed node's ``source_extra["rss"]``.

Expected failures are returned as typed ``Error`` results, never raised: an
unreachable or non-feed URL whose parse yields no recognised feed and no
entries is reported as ``NOT_FOUND``; a feed that parsed with a fatal
``bozo`` error and produced nothing usable is a ``PARSE_ERROR``. A feed that
parsed with warnings but still produced entries is a ``Partial`` -- the items
that were built, plus a ``Gap`` recording the parse warning.

Everything here is deterministic and read-only; ``feedparser.parse`` is run in
a thread so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import calendar
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import feedparser

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential, NormalizedAuthResolver
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Gap,
    Result,
    error,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# The source namespace under which this connector files its descriptive
# fields in ``Metadata.source_extra``.
SOURCE_NAMESPACE = "rss"

# Advisory semantic labels for the container feed node and its item children.
FEED_KIND = "feed"
ITEM_KIND = "feed_item"

# URI shapes this connector recognises as RSS/Atom feeds.
_FEED_EXTENSIONS = (".rss", ".atom", ".feed", ".rdf", "rss.xml")
_FEED_PATTERNS = ("feed", "rss", "atom")


def _struct_time_to_datetime(value: Any) -> Optional[datetime]:
    """Convert a feedparser UTC ``struct_time`` into an aware datetime.

    feedparser normalises parsed dates to UTC ``time.struct_time`` values, so
    ``calendar.timegm`` (which treats the tuple as UTC) is used rather than
    ``time.mktime`` (which would assume local time). Returns ``None`` when the
    value is missing or not a parsable time tuple.
    """
    if not value:
        return None
    try:
        epoch = calendar.timegm(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _item_text(entry: Any) -> tuple[str, TextFormat]:
    """Extract an item's body content and its surface text format.

    Prefers a structured ``content`` payload (Atom ``content`` /
    ``content:encoded``) over the plain ``summary``; reports
    ``TextFormat.HTML`` when feedparser tags the source as HTML/XHTML,
    otherwise ``TextFormat.PLAIN``.
    """
    content_list = entry.get("content")
    if content_list:
        first = content_list[0]
        value = first.get("value", "") or ""
        ctype = (first.get("type") or "").lower()
        fmt = TextFormat.HTML if "html" in ctype else TextFormat.PLAIN
        return value, fmt

    summary = entry.get("summary", "") or ""
    detail = entry.get("summary_detail") or {}
    ctype = (detail.get("type") or "").lower()
    fmt = TextFormat.HTML if "html" in ctype else TextFormat.PLAIN
    return summary, fmt


def _build_item_node(entry: Any) -> CompositionNode:
    """Build one ``"feed_item"`` node from a parsed feed entry."""
    content, fmt = _item_text(entry)
    atoms = [Text(content=content, format=fmt)]

    guid = entry.get("id") or entry.get("link") or None
    link = entry.get("link") or None

    author = entry.get("author") or None
    author_detail = entry.get("author_detail") or {}
    if author_detail.get("name"):
        author = author_detail["name"]

    published = entry.get("published") or None
    created = _struct_time_to_datetime(entry.get("published_parsed"))
    updated = _struct_time_to_datetime(entry.get("updated_parsed"))

    source_fields: dict[str, Any] = {
        "title": entry.get("title") or None,
        "link": link,
        "published": published,
        "author": author,
        "guid": guid,
    }
    tags = entry.get("tags")
    if tags:
        source_fields["tags"] = [tag.get("term") for tag in tags if tag.get("term")]

    return build_node(
        kind=ITEM_KIND,
        atoms=atoms,
        id=guid,
        created=created,
        updated=updated,
        author=author,
        source_url=link,
        source_namespace=SOURCE_NAMESPACE,
        source_fields=source_fields,
    )


def _feed_source_fields(parsed: Any) -> dict[str, Any]:
    """Collect the feed-level descriptive fields for ``source_extra``."""
    feed = parsed.feed
    description = feed.get("description") or feed.get("subtitle") or None
    return {
        "title": feed.get("title") or None,
        "link": feed.get("link") or None,
        "description": description,
        "updated": feed.get("updated") or None,
        "language": feed.get("language") or None,
        "version": parsed.get("version") or None,
    }


class RSSConnector(BaseFetcher):
    """
    Canonical RSS/Atom feed connector
    ===============================================
    Fetches and parses an RSS/Atom feed and streams it as one canonical
    container node (advisory ``kind`` ``"feed"``) whose children are item
    nodes (advisory ``kind`` ``"feed_item"``). Each item's body becomes a
    ``Text`` atom; per-item descriptive fields (title, link, published,
    author, guid) and feed-level descriptive fields (title, link,
    description, updated) are filed under ``source_extra["rss"]`` -- never
    inline on an atom. Expected failures are returned as typed ``Result``
    values, never raised.
    ===============================================
    NOTE:
        1. This connector implements only ``stream()``; ``fetch()`` is the
           inherited base sugar that collects the bounded stream.
        2. ``feedparser.parse`` accepts a URL or raw feed content and is run
           in a worker thread so it never blocks the event loop.
        3. Credentials are passed per call via ``auth`` and resolved
           transiently into request headers; nothing is stored on the
           instance.

    Methods
    -------
        can_handle:
        stream:
    """

    def __init__(self) -> None:
        """
        Create an RSS/Atom feed connector

        Return
        ------
            none:
                Nothing; the connector holds no per-call state.
        """
        self._auth_resolver = NormalizedAuthResolver()

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI looks like an RSS/Atom feed

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` when ``uri`` has a feed-like extension or contains a
                feed-like path token (``feed``, ``rss``, ``atom``).
        """
        lowered = uri.lower()
        if lowered.endswith(_FEED_EXTENSIONS):
            return True
        return any(pattern in lowered for pattern in _FEED_PATTERNS)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream a parsed RSS/Atom feed as one canonical container result

        Parses the feed at ``uri`` (off the event loop) and yields exactly
        one ``Result``. On a recognised feed it yields a ``Success`` whose
        tree is a ``"feed"`` container node with one ``"feed_item"`` child per
        entry; if the parse reported a warning but still produced entries it
        yields a ``Partial`` carrying those items plus a ``Gap`` describing
        the warning. A parse that produced no recognised feed and no entries
        yields a typed ``Error`` -- ``PARSE_ERROR`` when the parser flagged a
        fatal problem, otherwise ``NOT_FOUND``.

        NOTE:
            1. Expected failures are yielded as ``Result`` values, never
               raised.
            2. ``zoom`` is accepted for contract conformance; this connector
               emits the natural feed/item decomposition and does not act on
               it.

        Parameters
        ----------
            uri:
                The RSS/Atom feed URL (or raw feed content).
            auth:
                The per-call credential, or ``None`` for unauthenticated
                access. Resolved transiently into request headers passed to
                the parser.
            zoom:
                Optional per-atom-type zoom spec; accepted but not acted on.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        headers = self._auth_resolver.resolve_headers(auth)
        try:
            parsed = await asyncio.to_thread(self._parse, uri, headers)
        except Exception as exc:  # noqa: BLE001 - boundary: never raise out
            yield error(
                kind=ErrorKind.TRANSIENT,
                message=f"feed fetch/parse failed: {exc}",
                locator=uri,
            )
            return

        yield self._build_result(parsed, uri)

    @staticmethod
    def _parse(uri: str, headers: dict[str, str]) -> Any:
        """Parse a feed with feedparser (URL or raw content)."""
        if headers:
            return feedparser.parse(uri, request_headers=headers)
        return feedparser.parse(uri)

    def _build_result(self, parsed: Any, uri: str) -> Result:
        """Turn a parsed feedparser result into a canonical ``Result``."""
        entries = list(parsed.entries)
        version = parsed.get("version") or ""
        bozo = bool(parsed.get("bozo"))

        if not entries and not version:
            # Nothing recognisable was produced. A fatal parser problem is a
            # parse error; an empty/unreachable source is simply not found.
            if bozo:
                exc = parsed.get("bozo_exception")
                detail = str(exc) if exc else "feed could not be parsed"
                return error(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"could not parse feed: {detail}",
                    locator=uri,
                )
            return error(
                kind=ErrorKind.NOT_FOUND,
                message="no feed found at the given URI",
                locator=uri,
            )

        item_nodes = [_build_item_node(entry) for entry in entries]
        feed_node = build_node(
            kind=FEED_KIND,
            children=item_nodes,
            source_url=uri,
            updated=_struct_time_to_datetime(parsed.feed.get("updated_parsed")),
            source_namespace=SOURCE_NAMESPACE,
            source_fields=_feed_source_fields(parsed),
        )

        if bozo:
            # The feed still produced usable items but the parser flagged a
            # problem: surface it as a gap rather than hide it.
            exc = parsed.get("bozo_exception")
            detail = str(exc) if exc else "feed parsed with warnings"
            gaps: list[Gap] = [
                gap(
                    kind=ErrorKind.PARSE_ERROR,
                    locator=uri,
                    detail=f"feed parsed with warnings: {detail}",
                )
            ]
            return partial(feed_node, gaps)

        return success(feed_node)
