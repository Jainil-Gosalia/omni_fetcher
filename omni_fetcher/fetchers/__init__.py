"""Fetchers for OmniFetcher (legacy; deprecated in favor of omni_fetcher.v1)."""

from omni_fetcher._deprecation import warn_legacy_use
from omni_fetcher.fetchers.base import BaseFetcher, FetchResult
from omni_fetcher.fetchers.local_file import LocalFileFetcher
from omni_fetcher.fetchers.audio import AudioFetcher
from omni_fetcher.fetchers.http_url import HTTPURLFetcher
from omni_fetcher.fetchers.http_json import HTTPJSONFetcher
from omni_fetcher.fetchers.graphql import GraphQLFetcher
from omni_fetcher.fetchers.github import GitHubFetcher

# The legacy layer is deprecated; fire the (once-per-process) warning on
# direct import of this package too, not only via the lazy exports.
warn_legacy_use()

__all__ = [
    "BaseFetcher",
    "FetchResult",
    "LocalFileFetcher",
    "AudioFetcher",
    "HTTPURLFetcher",
    "HTTPJSONFetcher",
    "GraphQLFetcher",
    "GitHubFetcher",
]
