"""HTTP JSON fetcher for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata, MediaType
from omni_fetcher.schemas.structured import JSONData, GraphQLResponse


@source(
    name="http_json",
    uri_patterns=["*.json", "*api*", "*graphql*"],
    mime_types=["application/json"],
    priority=20,
    description="Fetch and parse JSON from HTTP APIs",
)
class HTTPJSONFetcher(BaseFetcher):
    """Fetcher specifically for JSON APIs."""

    name = "http_json"
    priority = 20

    def __init__(self, timeout: float = 30.0):
        super().__init__()
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this looks like a JSON API endpoint."""
        if not (uri.startswith("http://") or uri.startswith("https://")):
            return False

        # Check for common JSON indicators
        return (
            uri.endswith(".json")
            or "api" in uri.lower()
            or "json" in uri.lower()
            or "graphql" in uri.lower()
        )

    async def fetch(self, uri: str, **kwargs: Any) -> JSONData:
        """Fetch JSON from HTTP URL.

        Args:
            uri: HTTP/HTTPS URL returning JSON

        Returns:
            JSONData with parsed JSON
        """
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(uri)
            response.raise_for_status()

            # Check if it's GraphQL
            content_type = response.headers.get("content-type", "")

            # Try to detect GraphQL
            if "graphql" in uri.lower() or self._is_graphql_response(response.text):
                return await self._handle_graphql(response, uri)

            # Regular JSON
            return await self._handle_json(response, uri)

    async def _handle_json(self, response: httpx.Response, uri: str) -> JSONData:
        """Handle regular JSON response."""
        content_type = response.headers.get("content-type", "application/json")
        mime_type = content_type.split(";")[0].strip()

        try:
            data = response.json()
        except Exception as e:
            raise ValueError(f"Failed to parse JSON: {e}")

        fetch_duration_ms = response.elapsed.total_seconds() * 1000

        metadata = FetchMetadata(
            source_uri=uri,
            fetched_at=datetime.now(),
            source_name=self.name,
            mime_type=mime_type,
            fetch_duration_ms=fetch_duration_ms,
            headers=dict(response.headers),
            status_code=response.status_code,
        )

        return JSONData(
            metadata=metadata,
            data=data,
            root_keys=list(data.keys()) if isinstance(data, dict) else None,
            is_array=isinstance(data, list),
            array_length=len(data) if isinstance(data, (list, tuple)) else None,
            max_depth=self._calculate_depth(data),
            tags=["json", "api"],
        )

    async def _handle_graphql(self, response: httpx.Response, uri: str) -> GraphQLResponse:
        """Handle GraphQL response."""
        content_type = response.headers.get("content-type", "application/json")
        mime_type = content_type.split(";")[0].strip()

        try:
            data = response.json()
        except Exception:
            data = {}

        # Extract query and variables if present
        query = None
        variables = None

        # Try to get errors
        errors = data.get("errors")

        fetch_duration_ms = response.elapsed.total_seconds() * 1000

        metadata = FetchMetadata(
            source_uri=uri,
            fetched_at=datetime.now(),
            source_name=self.name,
            mime_type=mime_type,
            fetch_duration_ms=fetch_duration_ms,
            headers=dict(response.headers),
            status_code=response.status_code,
        )

        return GraphQLResponse(
            metadata=metadata,
            data=data.get("data"),
            query=query or "",
            variables=variables,
            errors=errors,
            tags=["graphql", "api"],
        )

    def _is_graphql_response(self, text: str) -> bool:
        """Check if response looks like GraphQL."""
        try:
            import json

            data = json.loads(text)
            return "data" in data or "errors" in data
        except Exception:
            return False

    def _calculate_depth(self, obj: Any, current_depth: int = 0) -> int:
        """Calculate maximum depth of nested JSON."""
        if isinstance(obj, dict):
            if not obj:
                return current_depth
            return max(self._calculate_depth(v, current_depth + 1) for v in obj.values())
        elif isinstance(obj, list):
            if not obj:
                return current_depth
            return max(self._calculate_depth(item, current_depth + 1) for item in obj)
        else:
            return current_depth
