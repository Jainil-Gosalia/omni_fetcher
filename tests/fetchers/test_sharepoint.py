"""Tests for SharePointFetcher."""

import pytest

from omni_fetcher.core.exceptions import SourceNotFoundError
from omni_fetcher.fetchers.sharepoint import (
    SharePointFetcher,
    parse_sharepoint_uri,
    SharePointRoute,
)


class TestSharePointFetcherCreation:
    def test_creation(self):
        """Can create SharePointFetcher."""
        fetcher = SharePointFetcher()
        assert fetcher.name == "sharepoint"
        assert fetcher.priority == 15


class TestSharePointFetcherCanHandle:
    def test_can_handle_sharepoint_com_url(self):
        """SharePointFetcher handles sharepoint.com URLs."""
        assert SharePointFetcher.can_handle("https://company.sharepoint.com/sites/Engineering")
        assert SharePointFetcher.can_handle(
            "https://company.sharepoint.com/sites/Engineering/Shared Documents"
        )
        assert SharePointFetcher.can_handle("https://mycompany.sharepoint.com/sites/Test")

    def test_can_handle_sharepoint_protocol(self):
        """SharePointFetcher handles sharepoint:// URIs."""
        assert SharePointFetcher.can_handle("sharepoint://sites/Engineering")
        assert SharePointFetcher.can_handle("sharepoint://sites/Engineering/Shared Documents")
        assert SharePointFetcher.can_handle(
            "sharepoint://sites/Engineering/Shared Documents/file.txt"
        )

    def test_cannot_handle_other_url(self):
        """SharePointFetcher rejects non-SharePoint URLs."""
        assert not SharePointFetcher.can_handle("https://github.com/owner/repo")
        assert not SharePointFetcher.can_handle("https://example.com/page")
        assert not SharePointFetcher.can_handle("")

    def test_cannot_handle_onedrive_url(self):
        """SharePointFetcher rejects OneDrive URLs."""
        assert not SharePointFetcher.can_handle("https://company-my.sharepoint.com/sites/Test")


class TestSharePointFetcherParseURI:
    def test_parse_site_from_url(self):
        """Parse site from sharepoint.com URL."""
        route = parse_sharepoint_uri("https://company.sharepoint.com/sites/Engineering")
        assert route.type == "site"
        assert route.site_name == "Engineering"

    def test_parse_library_from_url(self):
        """Parse library from sharepoint.com URL."""
        route = parse_sharepoint_uri(
            "https://company.sharepoint.com/sites/Engineering/Shared Documents"
        )
        assert route.type == "library"
        assert route.site_name == "Engineering"
        assert route.library_name == "Shared Documents"

    def test_parse_file_from_url(self):
        """Parse file from sharepoint.com URL."""
        route = parse_sharepoint_uri(
            "https://company.sharepoint.com/sites/Engineering/Shared Documents/README.md"
        )
        assert route.type == "file"
        assert route.site_name == "Engineering"
        assert route.library_name == "Shared Documents"
        assert route.file_name == "README.md"

    def test_parse_site_from_protocol(self):
        """Parse site from sharepoint:// URI."""
        route = parse_sharepoint_uri("sharepoint://sites/Engineering")
        assert route.type == "site"
        assert route.site_name == "Engineering"

    def test_parse_library_from_protocol(self):
        """Parse library from sharepoint:// URI."""
        route = parse_sharepoint_uri("sharepoint://sites/Engineering/Shared Documents")
        assert route.type == "library"
        assert route.site_name == "Engineering"
        assert route.library_name == "Shared Documents"

    def test_parse_file_from_protocol(self):
        """Parse file from sharepoint:// URI."""
        route = parse_sharepoint_uri("sharepoint://sites/Engineering/Shared Documents/README.md")
        assert route.type == "file"
        assert route.site_name == "Engineering"
        assert route.library_name == "Shared Documents"
        assert route.file_name == "README.md"

    def test_parse_invalid_uri_raises_error(self):
        """Invalid URIs raise SourceNotFoundError."""
        with pytest.raises(SourceNotFoundError):
            parse_sharepoint_uri("https://example.com/page")
