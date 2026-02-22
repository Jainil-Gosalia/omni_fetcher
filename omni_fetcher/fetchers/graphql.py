"""GraphQL fetcher for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata
from omni_fetcher.schemas.structured import GraphQLResponse


@source(
    name="graphql",
    uri_patterns=["graphql", "gql"],
    priority=10,
    description="Fetch data from GraphQL endpoints",
)
class GraphQLFetcher(BaseFetcher):
    """Fetcher for GraphQL APIs."""

    name = "graphql"
    priority = 10

    def __init__(
        self,
        endpoint: str,
        timeout: float = 30.0,
        headers: Optional[dict[str, str]] = None,
    ):
        super().__init__()
        self.endpoint = endpoint
        self.timeout = timeout
        self.headers = headers or {}

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this fetcher can handle the URI."""
        uri_lower = uri.lower()
        return "graphql" in uri_lower or "gql" in uri_lower

    async def query(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
        operation_name: Optional[str] = None,
    ) -> GraphQLResponse:
        """Execute a GraphQL query.

        Args:
            query: GraphQL query string
            variables: Optional variables for the query
            operation_name: Optional operation name

        Returns:
            GraphQLResponse with query results
        """
        return await self._execute(
            query=query,
            variables=variables,
            operation_name=operation_name,
        )

    async def mutation(
        self,
        mutation: str,
        variables: Optional[dict[str, Any]] = None,
        operation_name: Optional[str] = None,
    ) -> GraphQLResponse:
        """Execute a GraphQL mutation.

        Args:
            mutation: GraphQL mutation string
            variables: Optional variables for the mutation
            operation_name: Optional operation name

        Returns:
            GraphQLResponse with mutation results
        """
        return await self._execute(
            query=mutation,
            variables=variables,
            operation_name=operation_name,
        )

    async def _execute(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
        operation_name: Optional[str] = None,
    ) -> GraphQLResponse:
        """Execute a GraphQL operation.

        Args:
            query: GraphQL query/mutation string
            variables: Optional variables
            operation_name: Optional operation name

        Returns:
            GraphQLResponse with results
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        if operation_name:
            payload["operationName"] = operation_name

        merged_headers = dict(self.headers)
        merged_headers.update(self.get_auth_headers())
        merged_headers.setdefault("Content-Type", "application/json")

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.post(
                self.endpoint,
                json=payload,
                headers=merged_headers,
            )
            response.raise_for_status()

            fetch_duration_ms = response.elapsed.total_seconds() * 1000
            content_type = response.headers.get("content-type", "application/json")
            mime_type = content_type.split(";")[0].strip()

            try:
                data = response.json()
            except Exception:
                data = {}

            errors = data.get("errors")
            response_data = data.get("data")
            extensions = data.get("extensions")

            metadata = FetchMetadata(
                source_uri=self.endpoint,
                fetched_at=datetime.now(),
                source_name=self.name,
                mime_type=mime_type,
                fetch_duration_ms=fetch_duration_ms,
                headers=dict(response.headers),
                status_code=response.status_code,
            )

            return GraphQLResponse(
                metadata=metadata,
                data=response_data,
                query=query,
                operation_name=operation_name,
                variables=variables,
                errors=errors,
                extensions=extensions,
                tags=["graphql", "api"],
            )

    async def fetch(self, uri: str, **kwargs: Any) -> GraphQLResponse:
        """Fetch data from GraphQL endpoint.

        This method provides compatibility with the BaseFetcher interface.
        If uri differs from endpoint, it will be used as the endpoint.

        Args:
            uri: GraphQL endpoint URI (or query if endpoint is set)
            **kwargs: Additional options (query, variables, operation_name)

        Returns:
            GraphQLResponse with results
        """
        if uri != self.endpoint:
            endpoint = uri
        else:
            endpoint = self.endpoint

        query = kwargs.get("query")
        variables = kwargs.get("variables")
        operation_name = kwargs.get("operation_name")

        if not query:
            query = "{ __typename }"

        if endpoint != self.endpoint:
            fetcher = GraphQLFetcher(
                endpoint=endpoint,
                timeout=self.timeout,
                headers=self.headers,
            )
            fetcher._auth_config = self._auth_config
            return await fetcher.query(query, variables, operation_name)

        return await self._execute(query, variables, operation_name)
