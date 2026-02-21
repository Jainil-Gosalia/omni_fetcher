"""Fetchers for OmniFetcher."""

from omni_fetcher.fetchers.base import BaseFetcher, FetchResult
from omni_fetcher.fetchers.local_file import LocalFileFetcher
from omni_fetcher.fetchers.http_url import HTTPURLFetcher
from omni_fetcher.fetchers.http_json import HTTPJSONFetcher
from omni_fetcher.fetchers.graphql import GraphQLFetcher

__all__ = [
    "BaseFetcher",
    "FetchResult",
    "LocalFileFetcher",
    "HTTPURLFetcher",
    "HTTPJSONFetcher",
    "GraphQLFetcher",
]
