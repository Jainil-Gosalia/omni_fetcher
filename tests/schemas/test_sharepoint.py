"""Tests for SharePoint schemas."""

from datetime import datetime

import pytest

from omni_fetcher.schemas.sharepoint import (
    SharePointFile,
    SharePointLibrary,
    SharePointSite,
)
from omni_fetcher.schemas.atomics import TextDocument, ImageDocument


class TestSharePointFileSchema:
    def test_sharepoint_file_minimal_fields(self):
        file = SharePointFile(
            file_id="abc123",
            name="README.md",
            path="/Shared Documents/README.md",
            site="Engineering",
            library="Shared Documents",
            mime_type="text/markdown",
            size_bytes=1024,
            content=None,
            author="John Doe",
            last_modified_by="Jane Doe",
            created_at=datetime.now(),
            modified_at=datetime.now(),
            url="https://company.sharepoint.com/sites/Engineering/Shared Documents/README.md",
        )
        assert file.file_id == "abc123"
        assert file.name == "README.md"

    def test_sharepoint_file_tags_basic(self):
        file = SharePointFile(
            file_id="abc123",
            name="test.txt",
            path="/test.txt",
            site="Engineering",
            library="Shared Documents",
            mime_type="text/plain",
            size_bytes=100,
            content=TextDocument(source_uri="", content="test", format="plain"),
            created_at=datetime.now(),
            modified_at=datetime.now(),
            url="https://company.sharepoint.com/test.txt",
        )
        assert "sharepoint" in file.tags
        assert "file" in file.tags

    def test_sharepoint_file_tags_pdf(self):
        file = SharePointFile(
            file_id="abc123",
            name="doc.pdf",
            path="/doc.pdf",
            site="Engineering",
            library="Shared Documents",
            mime_type="application/pdf",
            size_bytes=100,
            content=None,
            created_at=datetime.now(),
            modified_at=datetime.now(),
            url="https://company.sharepoint.com/doc.pdf",
        )
        assert "sharepoint" in file.tags
        assert "file" in file.tags
        assert "pdf" in file.tags


class TestSharePointLibrarySchema:
    def test_sharepoint_library_minimal_fields(self):
        library = SharePointLibrary(
            library_id="drive123",
            name="Shared Documents",
            site="Engineering",
            items=[],
            item_count=0,
            url="https://company.sharepoint.com/sites/Engineering/Shared Documents",
        )
        assert library.library_id == "drive123"

    def test_sharepoint_library_tags(self):
        library = SharePointLibrary(
            library_id="drive123",
            name="Shared Documents",
            site="Engineering",
            description="Team documents",
            items=[],
            item_count=0,
            url="https://company.sharepoint.com/sites/Engineering/Shared Documents",
        )
        assert "sharepoint" in library.tags
        assert "library" in library.tags


class TestSharePointSiteSchema:
    def test_sharepoint_site_minimal_fields(self):
        site = SharePointSite(
            site_id="site123",
            name="Engineering",
            display_name="Engineering Team Site",
            hostname="company.sharepoint.com",
            items=[],
            url="https://company.sharepoint.com/sites/Engineering",
        )
        assert site.site_id == "site123"
        assert site.name == "Engineering"

    def test_sharepoint_site_tags(self):
        site = SharePointSite(
            site_id="site123",
            name="Engineering",
            display_name="Engineering Team Site",
            description=TextDocument(
                source_uri="", content="Engineering team site", format="markdown"
            ),
            hostname="company.sharepoint.com",
            items=[],
            url="https://company.sharepoint.com/sites/Engineering",
        )
        assert "sharepoint" in site.tags
        assert "site" in site.tags
