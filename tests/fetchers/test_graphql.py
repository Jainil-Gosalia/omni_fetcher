"""Tests for GraphQL fetcher."""

import json
from datetime import timedelta

import httpx
import pytest

from omni_fetcher.fetchers.graphql import GraphQLFetcher


_OriginalAsyncClient = httpx.AsyncClient


def _make_response(status_code, **kwargs):
    resp = httpx.Response(status_code, **kwargs)
    resp._elapsed = timedelta(milliseconds=10)
    return resp


def _make_transport(response_data: dict, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return _make_response(
            status_code,
            json=response_data,
            headers={"content-type": "application/json"},
        )

    return httpx.MockTransport(handler)


def _patched_client_factory(transport):
    def factory(**kwargs):
        kwargs["transport"] = transport
        return _OriginalAsyncClient(**kwargs)

    return factory


SUCCESSFUL_RESPONSE = {
    "data": {"user": {"id": "1", "name": "Alice"}},
}

ERROR_RESPONSE = {
    "data": None,
    "errors": [{"message": "Field 'missing' not found", "locations": [{"line": 1, "column": 3}]}],
}

MUTATION_RESPONSE = {
    "data": {"createUser": {"id": "2", "name": "Bob"}},
}


class TestGraphQLFetcherCreation:
    def test_default_creation(self):
        fetcher = GraphQLFetcher(endpoint="https://api.example.com/graphql")
        assert fetcher.endpoint == "https://api.example.com/graphql"
        assert fetcher.timeout == 30.0
        assert fetcher.headers == {}

    def test_with_endpoint(self):
        fetcher = GraphQLFetcher(endpoint="https://other.example.com/gql")
        assert fetcher.endpoint == "https://other.example.com/gql"

    def test_with_headers(self):
        headers = {"Authorization": "Bearer token123", "X-Custom": "value"}
        fetcher = GraphQLFetcher(endpoint="https://api.example.com/graphql", headers=headers)
        assert fetcher.headers == headers
        assert fetcher.headers["Authorization"] == "Bearer token123"


class TestGraphQLFetcherQuery:
    @pytest.mark.asyncio
    async def test_simple_query(self, monkeypatch):
        transport = _make_transport(SUCCESSFUL_RESPONSE)
        fetcher = GraphQLFetcher(endpoint="https://api.example.com/graphql")

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _patched_client_factory(transport),
        )

        result = await fetcher.query("{ user { id name } }")
        assert result.data == {"user": {"id": "1", "name": "Alice"}}
        assert result.errors is None
        assert result.has_errors is False
        assert result.query == "{ user { id name } }"

    @pytest.mark.asyncio
    async def test_query_with_variables(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _make_response(
                200, json=SUCCESSFUL_RESPONSE, headers={"content-type": "application/json"}
            )

        transport = httpx.MockTransport(handler)
        fetcher = GraphQLFetcher(endpoint="https://api.example.com/graphql")

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _patched_client_factory(transport),
        )

        variables = {"userId": "1"}
        result = await fetcher.query(
            "query GetUser($userId: ID!) { user(id: $userId) { id name } }", variables=variables
        )

        assert captured["body"]["variables"] == {"userId": "1"}
        assert result.variables == variables
        assert result.data == {"user": {"id": "1", "name": "Alice"}}

    @pytest.mark.asyncio
    async def test_query_with_operation_name(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _make_response(
                200, json=SUCCESSFUL_RESPONSE, headers={"content-type": "application/json"}
            )

        transport = httpx.MockTransport(handler)
        fetcher = GraphQLFetcher(endpoint="https://api.example.com/graphql")

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _patched_client_factory(transport),
        )

        result = await fetcher.query(
            "query GetUser { user { id name } }",
            operation_name="GetUser",
        )

        assert captured["body"]["operationName"] == "GetUser"
        assert result.operation_name == "GetUser"


class TestGraphQLFetcherMutation:
    @pytest.mark.asyncio
    async def test_simple_mutation(self, monkeypatch):
        transport = _make_transport(MUTATION_RESPONSE)
        fetcher = GraphQLFetcher(endpoint="https://api.example.com/graphql")

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _patched_client_factory(transport),
        )

        result = await fetcher.mutation('mutation { createUser(name: "Bob") { id name } }')
        assert result.data == {"createUser": {"id": "2", "name": "Bob"}}
        assert result.errors is None

    @pytest.mark.asyncio
    async def test_mutation_with_variables(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _make_response(
                200, json=MUTATION_RESPONSE, headers={"content-type": "application/json"}
            )

        transport = httpx.MockTransport(handler)
        fetcher = GraphQLFetcher(endpoint="https://api.example.com/graphql")

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _patched_client_factory(transport),
        )

        variables = {"name": "Bob"}
        result = await fetcher.mutation(
            "mutation CreateUser($name: String!) { createUser(name: $name) { id name } }",
            variables=variables,
        )

        assert captured["body"]["variables"] == {"name": "Bob"}
        assert result.variables == variables
        assert result.data == {"createUser": {"id": "2", "name": "Bob"}}


class TestGraphQLFetcherCanHandle:
    def test_can_handle_graphql_uri(self):
        assert GraphQLFetcher.can_handle("https://api.example.com/graphql")
        assert GraphQLFetcher.can_handle("https://api.example.com/GRAPHQL")
        assert GraphQLFetcher.can_handle("https://api.example.com/v1/graphql")

    def test_can_handle_gql_uri(self):
        assert GraphQLFetcher.can_handle("https://api.example.com/gql")
        assert GraphQLFetcher.can_handle("https://api.example.com/v2/gql")

    def test_cannot_handle_non_graphql(self):
        assert not GraphQLFetcher.can_handle("https://api.example.com/rest/users")
        assert not GraphQLFetcher.can_handle("https://api.example.com/v1/data")
        assert not GraphQLFetcher.can_handle("https://example.com/page.html")


class TestGraphQLResponse:
    @pytest.mark.asyncio
    async def test_response_with_data(self, monkeypatch):
        transport = _make_transport(SUCCESSFUL_RESPONSE)
        fetcher = GraphQLFetcher(endpoint="https://api.example.com/graphql")

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _patched_client_factory(transport),
        )

        result = await fetcher.query("{ user { id name } }")
        assert result.data is not None
        assert result.data["user"]["id"] == "1"
        assert result.metadata.source_uri == "https://api.example.com/graphql"
        assert result.metadata.status_code == 200

    @pytest.mark.asyncio
    async def test_response_with_errors(self, monkeypatch):
        transport = _make_transport(ERROR_RESPONSE)
        fetcher = GraphQLFetcher(endpoint="https://api.example.com/graphql")

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _patched_client_factory(transport),
        )

        result = await fetcher.query("{ missing { id } }")
        assert result.data is None
        assert result.errors is not None
        assert len(result.errors) == 1
        assert result.errors[0]["message"] == "Field 'missing' not found"

    @pytest.mark.asyncio
    async def test_has_errors_property(self, monkeypatch):
        transport = _make_transport(ERROR_RESPONSE)
        fetcher = GraphQLFetcher(endpoint="https://api.example.com/graphql")

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _patched_client_factory(transport),
        )

        result = await fetcher.query("{ missing { id } }")
        assert result.has_errors is True

        transport_ok = _make_transport(SUCCESSFUL_RESPONSE)
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            _patched_client_factory(transport_ok),
        )

        result_ok = await fetcher.query("{ user { id } }")
        assert result_ok.has_errors is False
