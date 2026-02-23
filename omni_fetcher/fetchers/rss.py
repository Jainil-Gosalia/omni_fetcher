"""RSS/Atom feed fetcher for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import feedparser

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata


@source(
    name="rss",
    uri_patterns=["feed", "rss", "atom", "rss.xml", ".rss"],
    mime_types=["application/rss+xml", "application/atom+xml", "application/xml"],
    priority=15,
    description="Fetch and parse RSS/Atom feeds",
)
class RSSFetcher(BaseFetcher):
    """Fetcher for RSS and Atom feeds."""

    name = "rss"
    priority = 15

    FEED_EXTENSIONS = [".rss", ".atom", ".feed", ".rdf"]
    FEED_PATTERNS = ["feed", "rss", "atom", "rss.xml"]

    def __init__(self):
        super().__init__()

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this is an RSS/Atom feed URL."""
        uri_lower = uri.lower()

        # Check extensions
        for ext in cls.FEED_EXTENSIONS:
            if uri_lower.endswith(ext):
                return True

        # Check patterns in URL
        for pattern in cls.FEED_PATTERNS:
            if pattern in uri_lower:
                return True

        return False

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch and parse an RSS/Atom feed."""
        feed_data = await self._parse_feed(uri)

        metadata = FetchMetadata(
            source_uri=uri,
            fetched_at=datetime.now(),
            source_name=self.name,
            mime_type="application/rss+xml",
        )

        from omni_fetcher.schemas.structured import JSONData

        return JSONData(
            metadata=metadata,
            data=feed_data,
            root_keys=list(feed_data.keys()) if isinstance(feed_data, dict) else None,
            tags=["rss", "feed"],
        )

    async def _parse_feed(self, uri: str) -> dict[str, Any]:
        """Parse the feed using feedparser."""

        def _parse():
            return feedparser.parse(uri)

        import asyncio

        loop = asyncio.get_event_loop()
        feed = await loop.run_in_executor(None, _parse)

        # Build feed data dict with all info
        feed_data = {
            "title": feed.feed.get("title", ""),
            "description": feed.feed.get("description", ""),
            "link": feed.feed.get("link", ""),
            "language": feed.feed.get("language", ""),
            "updated": feed.feed.get("updated", ""),
            "entries": [],
        }

        # Parse entries
        for entry in feed.entries:
            parsed_entry = self._parse_entry(entry)
            feed_data["entries"].append(parsed_entry)

        return feed_data

    def _parse_entry(self, entry: Any) -> dict[str, Any]:
        """Parse a single feed entry."""
        result = {
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "summary": entry.get("summary", ""),
            "content": entry.get("content", [{}])[0].get("value", "")
            if entry.get("content")
            else "",
            "author": entry.get("author", ""),
            "published": entry.get("published", ""),
            "updated": entry.get("updated", ""),
            "guid": entry.get("id", entry.get("link", "")),
        }

        if hasattr(entry, "tags") and entry.tags:
            result["tags"] = [tag.term for tag in entry.tags]

        if hasattr(entry, "author_detail"):
            result["author"] = entry.author_detail.get("name", "")

        return result
