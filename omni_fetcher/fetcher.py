"""OmniFetcher - Universal data fetcher with Pydantic schemas."""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

from omni_fetcher.core.registry import SourceRegistry, SourceInfo
from omni_fetcher.core.exceptions import SourceNotFoundError, FetchError
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.fetchers import (
    LocalFileFetcher,
    HTTPURLFetcher,
    HTTPJSONFetcher,
)
from omni_fetcher.fetchers.youtube import YouTubeFetcher
from omni_fetcher.fetchers.rss import RSSFetcher
from omni_fetcher.fetchers.s3 import S3Fetcher
from omni_fetcher.fetchers.pdf import PDFFetcher
from omni_fetcher.fetchers.csv import CSVFetcher
from omni_fetcher.fetchers.http_auth import HTTPAuthFetcher
from omni_fetcher.auth import AuthConfig, load_auth_from_env


class OmniFetcher:
    """Universal fetcher that can fetch data from any source.
    
    OmniFetcher automatically detects the appropriate handler for a given
    URI and returns data in a Pydantic model.
    
    Example:
        fetcher = OmniFetcher()
        result = await fetcher.fetch("https://api.example.com/users")
        result = await fetcher.fetch("/path/to/file.txt")
    
    With authentication:
        fetcher = OmniFetcher(auth={
            "github": {"type": "bearer", "token_env": "GITHUB_TOKEN"}
        })
        result = await fetcher.fetch("https://api.github.com/user")
    """
    
    def __init__(
        self,
        auto_register_builtins: bool = True,
        auth: Optional[Dict[str, Dict[str, Any]]] = None,
        load_env_auth: bool = True,
    ):
        """Initialize OmniFetcher.
        
        Args:
            auto_register_builtins: Whether to register built-in fetchers
            auth: Auth configs per source {source_name: {type: "bearer", token: "xxx", ...}}
            load_env_auth: Whether to load auth from environment variables
        """
        self._registry = SourceRegistry()
        
        # Initialize auth registry
        self._auth_registry: Dict[str, AuthConfig] = {}
        
        # Load from .env if requested
        if load_env_auth:
            env_auth = load_auth_from_env()
            for source, config in env_auth.items():
                self._auth_registry[source] = config
        
        # Override with explicitly provided auth
        if auth:
            for source, config_dict in auth.items():
                self._auth_registry[source] = AuthConfig(**config_dict)
        
        if auto_register_builtins:
            self._register_builtins()
    
    def _register_builtins(self) -> None:
        """Register built-in fetchers."""
        # Import fetchers to trigger @source decorator registration
        # They register themselves when imported
        _ = LocalFileFetcher
        _ = HTTPURLFetcher
        _ = HTTPJSONFetcher
        _ = YouTubeFetcher
        _ = RSSFetcher
        _ = S3Fetcher
        _ = PDFFetcher
        _ = CSVFetcher
        _ = HTTPAuthFetcher
    
    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch data from the given URI.
        
        Automatically detects the appropriate handler based on URI pattern.
        
        Args:
            uri: The URI to fetch (file path, URL, etc.)
            **kwargs: Additional options to pass to the fetcher
                - auth: Optional AuthConfig dict to override source auth
                
        Returns:
            Pydantic model with the fetched data
            
        Raises:
            SourceNotFoundError: No suitable handler found
            FetchError: Fetching failed
        """
        # Find appropriate source handler
        source_info = self._find_handler(uri)
        
        if not source_info:
            raise SourceNotFoundError(uri)
        
        # Create fetcher instance
        fetcher = source_info.fetcher_class()
        
        # Resolve auth: per-request > explicit config > source config > env
        auth_config = None
        
        # Check for per-request auth override
        if "auth" in kwargs:
            auth_dict = kwargs.pop("auth")
            if auth_dict:
                auth_config = AuthConfig(**auth_dict)
        else:
            # Check if this source has auth configured
            auth_dict = self._auth_registry.get(source_info.name)
            if auth_dict:
                auth_config = auth_dict
        
        # Apply auth to fetcher if supported
        if auth_config and hasattr(fetcher, 'set_auth'):
            fetcher.set_auth(auth_config)
        
        try:
            result = await fetcher.fetch(uri, **kwargs)
            return result
        except Exception as e:
            raise FetchError(uri, str(e))
    
    def _find_handler(self, uri: str) -> Optional[SourceInfo]:
        """Find the best handler for a URI."""
        matches = self._registry.find_sources_by_uri(uri)
        
        if not matches:
            return None
        
        # Return highest priority (lowest number)
        return matches[0]
    
    def list_sources(self) -> list[str]:
        """List all registered sources."""
        return self._registry.list_sources()
    
    def get_source_info(self, name: str) -> Optional[SourceInfo]:
        """Get information about a registered source."""
        return self._registry.get_source_info(name)
    
    def register_source(
        self,
        name: str,
        fetcher_class: Type[BaseFetcher],
        uri_patterns: list[str],
        mime_types: Optional[list[str]] = None,
        priority: int = 100,
        auth: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a custom source handler.
        
        Args:
            name: Unique name for the source
            fetcher_class: The fetcher class
            uri_patterns: List of URI patterns this source handles
            mime_types: List of MIME types this source handles
            priority: Lower = higher priority
            auth: Optional auth configuration dict
        """
        # Register the source
        self._registry.register(
            name=name,
            fetcher_class=fetcher_class,
            uri_patterns=uri_patterns,
            mime_types=mime_types,
            priority=priority,
            auth_config=auth,
        )
        
        # Also store auth config if provided
        if auth:
            self._auth_registry[name] = AuthConfig(**auth)
    
    def set_auth(self, source: str, auth: Dict[str, Any]) -> None:
        """Set auth config for a source.
        
        Args:
            source: Source name
            auth: Auth configuration dict
        """
        self._auth_registry[source] = AuthConfig(**auth)
    
    def get_auth(self, source: str) -> Optional[AuthConfig]:
        """Get auth config for a source.
        
        Args:
            source: Source name
            
        Returns:
            AuthConfig or None if not configured
        """
        return self._auth_registry.get(source)
    
    def unregister_source(self, name: str) -> None:
        """Unregister a source handler."""
        self._registry.unregister(name)
    
    async def fetch_metadata(self, uri: str) -> dict[str, Any]:
        """Fetch only metadata from a URI."""
        source_info = self._find_handler(uri)
        
        if not source_info:
            raise SourceNotFoundError(uri)
        
        fetcher = source_info.fetcher_class()
        return await fetcher.fetch_metadata(uri)
