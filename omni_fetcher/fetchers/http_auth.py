"""HTTP fetcher with authentication support."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

import httpx

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata
from omni_fetcher.schemas.documents import TextDocument, HTMLDocument
from omni_fetcher.schemas.structured import JSONData
from omni_fetcher.auth import AuthConfig


@source(
    name="http_auth",
    uri_patterns=["http://*", "https://*"],
    mime_types=["text/*", "application/json", "application/octet-stream"],
    priority=45,
    description="Fetch with authentication support",
)
class HTTPAuthFetcher(BaseFetcher):
    """HTTP fetcher with authentication support.
    
    Supports auth via:
    - Constructor: HTTPAuthFetcher(bearer_token='xxx')
    - set_auth: fetcher.set_auth(AuthConfig(...))
    - OmniFetcher auth registry
    """
    
    name = "http_auth"
    priority = 45
    
    def __init__(
        self,
        timeout: float = 30.0,
        bearer_token: Optional[str] = None,
        api_key: Optional[str] = None,
        basic_auth: Optional[tuple[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
    ):
        super().__init__()
        self.timeout = timeout
        self.bearer_token = bearer_token
        self.api_key = api_key
        self.basic_auth = basic_auth
        self.extra_headers = headers or {}
    
    def set_auth(self, auth_config: AuthConfig) -> None:
        """Set auth from AuthConfig."""
        self._auth_config = auth_config
        
        # Also support legacy params for backward compatibility
        if auth_config.type == "bearer":
            self.bearer_token = auth_config.get_token()
        elif auth_config.type == "api_key":
            self.api_key = auth_config.get_api_key()
        elif auth_config.type == "basic":
            username = auth_config.get_username()
            password = auth_config.get_password()
            if username and password:
                self.basic_auth = (username, password)
    
    def _build_headers(self) -> dict[str, str]:
        """Build request headers with auth."""
        headers = dict(self.extra_headers)
        
        # Auth config headers (from set_auth or OmniFetcher)
        if self._auth_config:
            auth_headers = self._auth_config.get_headers()
            headers.update(auth_headers)
        
        # Legacy direct params
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        
        return headers
    
    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this is an HTTP/HTTPS URI."""
        return uri.startswith("http://") or uri.startswith("https://")
    
    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch content from HTTP URL with auth."""
        # Allow per-request auth override
        bearer = kwargs.pop("bearer_token", None)
        api_key = kwargs.pop("api_key", None)
        
        headers = self._build_headers()
        
        # Per-request takes precedence
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if api_key:
            headers["X-API-Key"] = api_key
        
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(uri, headers=headers)
            response.raise_for_status()
            
            content_type = response.headers.get("content-type", "application/octet-stream")
            mime_type = content_type.split(";")[0].strip()
            
            metadata = FetchMetadata(
                source_uri=str(response.url),
                fetched_at=datetime.now(),
                source_name=self.name,
                mime_type=mime_type,
                file_size=len(response.content),
                fetch_duration_ms=response.elapsed.total_seconds() * 1000,
                headers=dict(response.headers),
                status_code=response.status_code,
            )
            
            return await self._create_result(response, metadata, mime_type)
    
    async def _create_result(self, response, metadata, mime_type):
        """Create appropriate result model."""
        if mime_type.startswith("text/"):
            content = response.text
            
            if mime_type == "text/html":
                return HTMLDocument(
                    metadata=metadata,
                    content=content,
                    title=self._extract_title(content),
                )
            else:
                return TextDocument(
                    metadata=metadata,
                    content=content,
                )
        
        elif mime_type == "application/json":
            try:
                data = response.json()
            except Exception:
                data = {}
            
            return JSONData(
                metadata=metadata,
                data=data,
                root_keys=list(data.keys()) if isinstance(data, dict) else None,
            )
        
        else:
            return TextDocument(
                metadata=metadata,
                content=response.text[:10000],
            )
    
    def _extract_title(self, html: str):
        """Extract title from HTML."""
        match = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
