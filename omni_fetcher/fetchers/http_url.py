"""HTTP URL fetcher for OmniFetcher."""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import httpx

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata, MediaType, DataCategory
from omni_fetcher.schemas.documents import HTMLDocument
from omni_fetcher.schemas.atomics import TextDocument, ImageDocument, TextFormat


@source(
    name="http_url",
    uri_patterns=["http://*", "https://*"],
    mime_types=["text/*", "application/octet-stream"],
    priority=50,
    description="Fetch content from HTTP/HTTPS URLs",
)
class HTTPURLFetcher(BaseFetcher):
    """Fetcher for HTTP/HTTPS URLs."""

    name = "http_url"
    priority = 50

    def __init__(self, timeout: float = 30.0):
        super().__init__()
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this is an HTTP/HTTPS URI."""
        return uri.startswith("http://") or uri.startswith("https://")

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch content from HTTP URL."""
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(uri)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "application/octet-stream")
            mime_type = content_type.split(";")[0].strip()
            fetch_duration_ms = response.elapsed.total_seconds() * 1000

            metadata = FetchMetadata(
                source_uri=str(response.url),
                fetched_at=datetime.now(),
                source_name=self.name,
                mime_type=mime_type,
                file_size=len(response.content),
                fetch_duration_ms=fetch_duration_ms,
                headers=dict(response.headers),
                status_code=response.status_code,
                etag=response.headers.get("etag"),
                last_modified=self._parse_date(response.headers.get("last-modified")),
            )

            return await self._create_result(response, metadata, mime_type)

    async def _create_result(
        self, response: httpx.Response, metadata: FetchMetadata, mime_type: str
    ) -> Any:
        """Create appropriate result model based on content type."""
        if mime_type.startswith("text/"):
            content = response.text

            if mime_type == "text/html":
                return HTMLDocument(
                    metadata=metadata,
                    text=TextDocument(
                        source_uri=metadata.source_uri,
                        content=content,
                        format=TextFormat.HTML,
                    ),
                    title=self._extract_title(content),
                )
            elif mime_type == "text/markdown":
                return TextDocument(
                    source_uri=metadata.source_uri,
                    content=content,
                    format=TextFormat.MARKDOWN,
                )
            else:
                return TextDocument(
                    source_uri=metadata.source_uri,
                    content=content,
                    format=TextFormat.PLAIN,
                )

        elif mime_type.startswith("image/"):
            return ImageDocument(
                source_uri=metadata.source_uri,
                width=None,
                height=None,
                format=mime_type.split("/")[-1].upper(),
            )

        elif mime_type == "application/json":
            from omni_fetcher.schemas.structured import JSONData

            try:
                data = response.json()
            except Exception:
                data = {}

            return JSONData(
                metadata=metadata,
                data=data,
                root_keys=list(data.keys()) if isinstance(data, dict) else None,
                is_array=isinstance(data, list),
            )

        else:
            return TextDocument(
                source_uri=metadata.source_uri,
                content=response.text[:10000],
                format=TextFormat.PLAIN,
            )

    def _extract_title(self, html: str) -> Optional[str]:
        """Extract title from HTML."""
        match = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse HTTP date string to datetime."""
        if not date_str:
            return None
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            return None
