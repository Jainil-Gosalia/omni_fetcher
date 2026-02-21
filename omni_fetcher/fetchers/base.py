"""Base fetcher class for OmniFetcher."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, Type

from pydantic import BaseModel

from omni_fetcher.auth import AuthConfig


class BaseFetcher(ABC):
    """Abstract base class for all fetchers.
    
    All source handlers must inherit from this class and implement
    the fetch method.
    """
    
    name: str = "base"
    priority: int = 100
    
    def __init__(self):
        """Initialize the fetcher."""
        self._source_name = self.name
        self._source_priority = self.priority
        self._auth_config: Optional[AuthConfig] = None
    
    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this fetcher can handle the URI.
        
        Override this in subclasses to add custom logic.
        """
        return False
    
    def set_auth(self, auth_config: AuthConfig) -> None:
        """Set authentication config for this fetcher.
        
        Override in subclasses to apply auth to HTTP requests, etc.
        
        Args:
            auth_config: AuthConfig with authentication details
        """
        self._auth_config = auth_config
    
    def get_auth_headers(self) -> dict[str, str]:
        """Get HTTP headers from auth config.
        
        Returns:
            Dict of headers to add to requests
        """
        if self._auth_config:
            return self._auth_config.get_headers()
        return {}
    
    def get_auth_aws_credentials(self) -> dict[str, str]:
        """Get AWS credentials from auth config.
        
        Returns:
            Dict with aws_access_key_id, aws_secret_access_key, region_name
        """
        if self._auth_config and self._auth_config.type == "aws":
            return self._auth_config.get_aws_credentials()
        return {}
    
    @abstractmethod
    async def fetch(self, uri: str, **kwargs: Any) -> BaseModel:
        """Fetch data from the given URI.
        
        Args:
            uri: The URI to fetch
            **kwargs: Additional fetch options
                
        Returns:
            Pydantic model with the fetched data
        """
        pass
    
    async def fetch_metadata(self, uri: str) -> dict[str, Any]:
        """Fetch only metadata without full content.
        
        Override in subclasses for efficient metadata fetching.
        """
        result = await self.fetch(uri)
        return result.model_dump() if hasattr(result, 'model_dump') else result.dict()


class FetchResult(BaseModel):
    """Result wrapper for fetch operations."""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    fetcher_name: Optional[str] = None
