"""Tests for base Pydantic models."""
import pytest
from datetime import datetime
from typing import Optional

from omni_fetcher.schemas.base import (
    BaseFetchedData,
    FetchMetadata,
    MediaType,
    DataCategory,
)


class TestFetchMetadata:
    """Tests for FetchMetadata."""

    def test_metadata_creation(self):
        """Can create FetchMetadata with required fields."""
        metadata = FetchMetadata(
            source_uri="https://example.com/file.txt",
            fetched_at=datetime.now(),
        )
        assert metadata.source_uri == "https://example.com/file.txt"
        assert metadata.fetched_at is not None

    def test_metadata_with_optional_fields(self):
        """Can create FetchMetadata with optional fields."""
        metadata = FetchMetadata(
            source_uri="file:///path/to/file.txt",
            fetched_at=datetime.now(),
            file_size=1024,
            mime_type="text/plain",
            source_name="local_file",
        )
        assert metadata.file_size == 1024
        assert metadata.mime_type == "text/plain"
        assert metadata.source_name == "local_file"

    def test_metadata_default_values(self):
        """Metadata has sensible defaults."""
        metadata = FetchMetadata(
            source_uri="test://test",
            fetched_at=datetime.now(),
        )
        assert metadata.fetch_duration_ms is None
        assert metadata.cache_hit is False


class TestBaseFetchedData:
    """Tests for BaseFetchedData."""

    def test_base_data_creation(self):
        """Can create BaseFetchedData."""
        metadata = FetchMetadata(
            source_uri="https://example.com/file.txt",
            fetched_at=datetime.now(),
        )
        data = BaseFetchedData(
            metadata=metadata,
            category=DataCategory.TEXT,
            media_type=MediaType.TEXT_PLAIN,
        )
        assert data.metadata.source_uri == "https://example.com/file.txt"
        assert data.category == DataCategory.TEXT
        assert data.media_type == MediaType.TEXT_PLAIN

    def test_base_data_category_enum(self):
        """DataCategory enum has expected values."""
        assert DataCategory.TEXT.value == "text"
        assert DataCategory.MEDIA.value == "media"
        assert DataCategory.DOCUMENT.value == "document"
        assert DataCategory.STRUCTURED.value == "structured"
        assert DataCategory.ARCHIVE.value == "archive"

    def test_base_data_media_type_enum(self):
        """MediaType enum has expected values."""
        assert MediaType.TEXT_PLAIN.value == "text/plain"
        assert MediaType.TEXT_HTML.value == "text/html"
        assert MediaType.TEXT_MARKDOWN.value == "text/markdown"
        assert MediaType.VIDEO_MP4.value == "video/mp4"
        assert MediaType.AUDIO_MP3.value == "audio/mpeg"
        assert MediaType.IMAGE_JPEG.value == "image/jpeg"


class TestMediaTypeEnum:
    """Tests for MediaType enum completeness."""

    def test_all_text_types(self):
        """All text types are defined."""
        assert MediaType.TEXT_PLAIN is not None
        assert MediaType.TEXT_HTML is not None
        assert MediaType.TEXT_MARKDOWN is not None
        assert MediaType.TEXT_XML is not None
        assert MediaType.TEXT_CSV is not None

    def test_all_image_types(self):
        """All image types are defined."""
        assert MediaType.IMAGE_JPEG is not None
        assert MediaType.IMAGE_PNG is not None
        assert MediaType.IMAGE_GIF is not None
        assert MediaType.IMAGE_WEBP is not None
        assert MediaType.IMAGE_SVG is not None

    def test_all_video_types(self):
        """All video types are defined."""
        assert MediaType.VIDEO_MP4 is not None
        assert MediaType.VIDEO_MKV is not None
        assert MediaType.VIDEO_WEBM is not None
        assert MediaType.VIDEO_AVI is not None

    def test_all_audio_types(self):
        """All audio types are defined."""
        assert MediaType.AUDIO_MP3 is not None
        assert MediaType.AUDIO_WAV is not None
        assert MediaType.AUDIO_FLAC is not None
        assert MediaType.AUDIO_OGG is not None


class TestDataValidation:
    """Tests for data validation in base models."""

    def test_mime_type_validation(self):
        """Mime type is stored correctly."""
        metadata = FetchMetadata(
            source_uri="test://test",
            fetched_at=datetime.now(),
            mime_type="application/json",
        )
        assert metadata.mime_type == "application/json"

    def test_file_size_validation(self):
        """File size is stored correctly."""
        metadata = FetchMetadata(
            source_uri="test://test",
            fetched_at=datetime.now(),
            file_size=1048576,  # 1 MB
        )
        assert metadata.file_size == 1048576

    def test_negative_file_size_rejected(self):
        """Negative file size is rejected."""
        with pytest.raises(Exception):  # Pydantic validation error
            FetchMetadata(
                source_uri="test://test",
                fetched_at=datetime.now(),
                file_size=-1,
            )
