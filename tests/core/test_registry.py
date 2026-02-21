"""Tests for the source registry system."""
import pytest
from dataclasses import dataclass
from typing import Optional

from omni_fetcher.core.registry import SourceRegistry, SourceInfo, source


class TestSourceInfo:
    """Tests for SourceInfo dataclass."""

    def test_source_info_creation(self):
        """SourceInfo can be created with required fields."""
        info = SourceInfo(
            name="test_source",
            fetcher_class=object,
            uri_patterns=["test://*"],
            mime_types=["text/*"],
            priority=10,
        )
        assert info.name == "test_source"
        assert info.uri_patterns == ["test://*"]
        assert info.priority == 10

    def test_source_info_defaults(self):
        """SourceInfo has sensible defaults."""
        info = SourceInfo(
            name="test_source",
            fetcher_class=object,
            uri_patterns=[],
        )
        assert info.mime_types == []
        assert info.priority == 100


class TestSourceRegistry:
    """Tests for SourceRegistry."""

    def test_registry_singleton(self):
        """Registry is a singleton."""
        reg1 = SourceRegistry()
        reg2 = SourceRegistry()
        assert reg1 is reg2

    def test_register_source(self):
        """Can register a source with the registry."""
        registry = SourceRegistry()
        
        @source(name="test_source", uri_patterns=["test://*"])
        class TestFetcher:
            pass
        
        # Check source is registered
        sources = registry.list_sources()
        assert "test_source" in sources

    def test_register_duplicate_source_raises(self):
        """Registering duplicate source raises error."""
        registry = SourceRegistry()
        registry._sources.clear()  # Clear any existing sources
        
        # First registration should succeed
        registry.register("dup_source", object, ["dup://*"], [], 100)
        
        # Second registration with same name should raise
        from omni_fetcher.core.exceptions import SourceRegistrationError
        with pytest.raises(SourceRegistrationError, match="already registered"):
            registry.register("dup_source", object, ["dup2://*"], [], 100)

    def test_list_sources(self):
        """Can list all registered sources."""
        registry = SourceRegistry()
        registry._sources.clear()
        
        @source(name="source_a", uri_patterns=["a://*"])
        class FetcherA:
            pass
        
        @source(name="source_b", uri_patterns=["b://*"])
        class FetcherB:
            pass
        
        sources = registry.list_sources()
        assert "source_a" in sources
        assert "source_b" in sources

    def test_get_source_info(self):
        """Can get source info by name."""
        registry = SourceRegistry()
        registry._sources.clear()
        
        @source(name="get_test", uri_patterns=["get://*"])
        class GetFetcher:
            pass
        
        info = registry.get_source_info("get_test")
        assert info is not None
        assert info.name == "get_test"

    def test_get_source_info_not_found(self):
        """Getting non-existent source returns None."""
        registry = SourceRegistry()
        info = registry.get_source_info("nonexistent")
        assert info is None

    def test_find_sources_by_uri(self):
        """Can find sources matching a URI."""
        registry = SourceRegistry()
        registry._sources.clear()
        
        @source(name="http", uri_patterns=["http://*", "https://*"], priority=50)
        class HTTPFetcher:
            pass
        
        @source(name="youtube", uri_patterns=["youtube.com", "youtu.be"], priority=10)
        class YouTubeFetcher:
            pass
        
        matches = registry.find_sources_by_uri("https://youtube.com/watch?v=abc")
        # Should match both (http is generic, youtube is specific)
        assert len(matches) >= 2
        # YouTube should be first due to higher priority (lower number)
        assert matches[0].name == "youtube"

    def test_find_sources_by_mime_type(self):
        """Can find sources matching a MIME type."""
        registry = SourceRegistry()
        registry._sources.clear()
        
        @source(name="json", uri_patterns=["*.json"], mime_types=["application/json"], priority=50)
        class JSONFetcher:
            pass
        
        @source(name="video", uri_patterns=["video://*"], mime_types=["video/*"], priority=50)
        class VideoFetcher:
            pass
        
        matches = registry.find_sources_by_mime_type("video/mp4")
        assert len(matches) >= 1
        assert matches[0].name == "video"

    def test_priority_ordering(self):
        """Sources are ordered by priority (lower = higher priority)."""
        registry = SourceRegistry()
        registry._sources.clear()
        
        @source(name="low_priority", uri_patterns=["low://*"], priority=100)
        class LowPriorityFetcher:
            pass
        
        @source(name="high_priority", uri_patterns=["high://*"], priority=10)
        class HighPriorityFetcher:
            pass
        
        matches = registry.find_sources_by_uri("high://test")
        assert matches[0].name == "high_priority"

    def test_unregister_source(self):
        """Can unregister a source."""
        registry = SourceRegistry()
        registry._sources.clear()
        
        @source(name="temp_source", uri_patterns=["temp://*"])
        class TempFetcher:
            pass
        
        assert "temp_source" in registry.list_sources()
        registry.unregister("temp_source")
        assert "temp_source" not in registry.list_sources()

    def test_clear_sources(self):
        """Can clear all sources."""
        registry = SourceRegistry()
        
        @source(name="clear_test", uri_patterns=["clear://*"])
        class ClearFetcher:
            pass
        
        registry.clear()
        assert len(registry.list_sources()) == 0


class TestSourceDecorator:
    """Tests for @source decorator."""

    def test_source_decorator_basic(self):
        """@source decorator registers the fetcher."""
        # This is tested indirectly via other tests
        pass

    def test_source_decorator_with_custom_priority(self):
        """@source decorator accepts custom priority."""
        registry = SourceRegistry()
        registry._sources.clear()
        
        @source(name="custom_priority", uri_patterns=["cp://*"], priority=5)
        class CustomPriorityFetcher:
            pass
        
        info = registry.get_source_info("custom_priority")
        assert info.priority == 5

    def test_source_decorator_with_mime_types(self):
        """@source decorator accepts mime types."""
        registry = SourceRegistry()
        registry._sources.clear()
        
        @source(name="mime_test", uri_patterns=["mime://*"], mime_types=["application/json"])
        class MimeTestFetcher:
            pass
        
        info = registry.get_source_info("mime_test")
        assert "application/json" in info.mime_types
