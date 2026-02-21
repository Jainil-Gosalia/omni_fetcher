"""Fetchers for OmniFetcher."""

from omni_fetcher.fetchers.base import BaseFetcher, FetchResult
from omni_fetcher.fetchers.local_file import LocalFileFetcher
from omni_fetcher.fetchers.http_url import HTTPURLFetcher
from omni_fetcher.fetchers.http_json import HTTPJSONFetcher

__all__ = [
    "BaseFetcher",
    "FetchResult",
    "LocalFileFetcher",
    "HTTPURLFetcher",
    "HTTPJSONFetcher",
]
