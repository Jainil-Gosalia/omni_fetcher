"""Source registry system for OmniFetcher."""

from __future__ import annotations

import re
import fnmatch
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Type, Dict

from omni_fetcher.core.exceptions import SourceRegistrationError


@dataclass
class SourceInfo:
    """Information about a registered source handler."""

    name: str
    fetcher_class: Type
    uri_patterns: list[str] = field(default_factory=list)
    mime_types: list[str] = field(default_factory=list)
    priority: int = 100
    description: str = ""
    auth_config: Optional[Dict[str, Any]] = field(default_factory=dict)

    def matches_uri(self, uri: str) -> bool:
        """Check if this source matches the given URI."""
        for pattern in self.uri_patterns:
            if "*" in pattern or "?" in pattern:
                if fnmatch.fnmatch(uri, pattern):
                    return True
            elif re.search(pattern, uri):
                return True
            elif pattern in uri:
                return True
        return False

    def matches_mime_type(self, mime_type: str) -> bool:
        """Check if this source matches the given MIME type."""
        if not self.mime_types:
            return False
        for pattern in self.mime_types:
            if "*" in pattern:
                pattern_base = pattern.split("/")[0]
                mime_base = mime_type.split("/")[0]
                if pattern_base == mime_base:
                    return True
            elif pattern == mime_type:
                return True
        return False


class SourceRegistry:
    """Singleton registry for source handlers."""

    _instance: Optional["SourceRegistry"] = None
    _sources: dict[str, SourceInfo] = field(default_factory=dict)

    def __new__(cls) -> "SourceRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sources = {}
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance to None."""
        cls._instance = None

    def register(
        self,
        name: str,
        fetcher_class: Type,
        uri_patterns: list[str],
        mime_types: Optional[list[str]] = None,
        priority: int = 100,
        description: str = "",
        auth_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        if name in self._sources:
            raise SourceRegistrationError(f"Source '{name}' is already registered")

        self._sources[name] = SourceInfo(
            name=name,
            fetcher_class=fetcher_class,
            uri_patterns=uri_patterns,
            mime_types=mime_types or [],
            priority=priority,
            description=description,
            auth_config=auth_config or {},
        )

    def unregister(self, name: str) -> None:
        if name in self._sources:
            del self._sources[name]

    def get_source_info(self, name: str) -> Optional[SourceInfo]:
        return self._sources.get(name)

    def list_sources(self) -> list[str]:
        return list(self._sources.keys())

    def find_sources_by_uri(self, uri: str) -> list[SourceInfo]:
        matches = [info for info in self._sources.values() if info.matches_uri(uri)]
        matches.sort(key=lambda x: x.priority)
        return matches

    def find_sources_by_mime_type(self, mime_type: str) -> list[SourceInfo]:
        matches = [info for info in self._sources.values() if info.matches_mime_type(mime_type)]
        matches.sort(key=lambda x: x.priority)
        return matches

    def clear(self) -> None:
        self._sources.clear()


def source(
    name: str,
    uri_patterns: Optional[list[str]] = None,
    mime_types: Optional[list[str]] = None,
    priority: int = 100,
    description: str = "",
    auth: Optional[Dict[str, Any]] = None,
) -> Callable[[Type], Type]:
    """Decorator to register a fetcher class as a source handler.

    Args:
        name: Unique name for the source
        uri_patterns: List of URI patterns this source handles
        mime_types: List of MIME types this source handles
        priority: Lower = higher priority (default 100)
        description: Optional description
        auth: Optional auth configuration dict

    Example:
        @source(
            name="github",
            uri_patterns=["github.com", "api.github.com"],
            auth={"type": "bearer", "token_env": "GITHUB_TOKEN"}
        )
        class GitHubFetcher(BaseFetcher):
            pass
    """

    def decorator(fetcher_class: Type) -> Type:
        # Store original init if exists
        original_init = getattr(fetcher_class, "__init__", None)

        def new_init(self, *args, **kwargs):
            # Set attributes first
            object.__setattr__(self, "_source_name", name)
            object.__setattr__(self, "_source_priority", priority)
            # Call original init
            if original_init is not None:
                original_init(self, *args, **kwargs)

        # Replace init
        fetcher_class.__init__ = new_init

        # Register with the registry
        registry = SourceRegistry()
        registry.register(
            name=name,
            fetcher_class=fetcher_class,
            uri_patterns=uri_patterns or [],
            mime_types=mime_types or [],
            priority=priority,
            description=description,
            auth_config=auth,
        )

        return fetcher_class

    return decorator
