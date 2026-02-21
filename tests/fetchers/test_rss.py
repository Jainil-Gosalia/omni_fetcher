"""Tests for RSS/Atom fetcher."""
import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from omni_fetcher.fetchers.rss import RSSFetcher


class TestRSSFetcher:
    def test_rss_fetcher_creation(self):
        """Can create RSSFetcher."""
        fetcher = RSSFetcher()
        assert fetcher.name == "rss"

    def test_rss_fetcher_supports_feed_urls(self):
        """RSSFetcher identifies feed URLs."""
        assert RSSFetcher.can_handle("https://example.com/feed.xml")
        assert RSSFetcher.can_handle("https://example.com/feed.atom")
        assert RSSFetcher.can_handle("https://example.com/rss")
        assert RSSFetcher.can_handle("https://example.com/feed.rss")
        assert RSSFetcher.can_handle("https://blog.example.com/atom.xml")

    def test_rss_fetcher_rejects_non_feeds(self):
        """RSSFetcher rejects non-feed URLs."""
        assert not RSSFetcher.can_handle("https://example.com/page.html")
        assert not RSSFetcher.can_handle("https://example.com/article")
        assert not RSSFetcher.can_handle("https://example.com/about")

    def test_rss_fetcher_priority(self):
        """RSSFetcher has high priority."""
        assert RSSFetcher.priority < 50

    @pytest.mark.asyncio
    async def test_parse_feed_entry(self):
        """Can parse a feed entry."""
        fetcher = RSSFetcher()
        
        entry = {
            'title': 'Test Article',
            'link': 'https://example.com/article',
            'published': '2024-01-15T10:00:00Z',
            'summary': 'Article summary',
            'author': 'John Doe',
            'tags': [{'term': 'python'}, {'term': 'testing'}],
        }
        
        parsed = fetcher._parse_entry(entry)
        
        assert parsed['title'] == 'Test Article'
        assert parsed['link'] == 'https://example.com/article'
