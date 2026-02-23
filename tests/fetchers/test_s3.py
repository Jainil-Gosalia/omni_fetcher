"""Tests for S3 fetcher."""

import pytest

from omni_fetcher.fetchers.s3 import S3Fetcher


class TestS3Fetcher:
    def test_s3_fetcher_creation(self):
        """Can create S3Fetcher."""
        fetcher = S3Fetcher()
        assert fetcher.name == "s3"

    def test_s3_fetcher_supports_s3_urls(self):
        """S3Fetcher identifies S3 URLs."""
        assert S3Fetcher.can_handle("s3://bucket/key")
        assert S3Fetcher.can_handle("s3://my-bucket/path/to/file.txt")
        assert S3Fetcher.can_handle("https://bucket.s3.amazonaws.com/file")

    def test_s3_fetcher_rejects_non_s3(self):
        """S3Fetcher rejects non-S3 URLs."""
        assert not S3Fetcher.can_handle("https://example.com/file.txt")
        assert not S3Fetcher.can_handle("sftp://server/file")

    def test_s3_fetcher_priority(self):
        """S3Fetcher has high priority."""
        assert S3Fetcher.priority < 50

    @pytest.mark.asyncio
    async def test_parse_s3_uri(self):
        """Can parse S3 URI."""
        fetcher = S3Fetcher()

        bucket, key = fetcher._parse_s3_uri("s3://my-bucket/path/to/file.txt")
        assert bucket == "my-bucket"
        assert key == "path/to/file.txt"

    @pytest.mark.asyncio
    async def test_parse_s3_uri_no_key(self):
        """Can parse S3 URI with no key."""
        fetcher = S3Fetcher()

        bucket, key = fetcher._parse_s3_uri("s3://my-bucket")
        assert bucket == "my-bucket"
        assert key == ""
