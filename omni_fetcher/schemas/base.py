"""Base Pydantic models for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DataCategory(str, Enum):
    """Category of fetched data."""

    TEXT = "text"
    MEDIA = "media"
    DOCUMENT = "document"
    STRUCTURED = "structured"
    ARCHIVE = "archive"


class MediaType(str, Enum):
    """Media type of fetched data."""

    # Text types
    TEXT_PLAIN = "text/plain"
    TEXT_HTML = "text/html"
    TEXT_MARKDOWN = "text/markdown"
    TEXT_XML = "text/xml"
    TEXT_CSV = "text/csv"
    TEXT_JSON = "application/json"
    TEXT_YAML = "application/x-yaml"

    # Image types
    IMAGE_JPEG = "image/jpeg"
    IMAGE_PNG = "image/png"
    IMAGE_GIF = "image/gif"
    IMAGE_WEBP = "image/webp"
    IMAGE_SVG = "image/svg+xml"
    IMAGE_BMP = "image/bmp"

    # Video types
    VIDEO_MP4 = "video/mp4"
    VIDEO_MKV = "video/x-matroska"
    VIDEO_WEBM = "video/webm"
    VIDEO_AVI = "video/x-msvideo"
    VIDEO_MOV = "video/quicktime"

    # Audio types
    AUDIO_MP3 = "audio/mpeg"
    AUDIO_WAV = "audio/wav"
    AUDIO_FLAC = "audio/flac"
    AUDIO_OGG = "audio/ogg"
    AUDIO_M4A = "audio/mp4"

    # Document types
    APPLICATION_PDF = "application/pdf"
    APPLICATION_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    APPLICATION_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    APPLICATION_OCTET = "application/octet-stream"

    # Google types
    APPLICATION_VND_GOOGLE_SPREADSHEET = "application/vnd.google-apps.spreadsheet"
    APPLICATION_VND_GOOGLE_DOCUMENT = "application/vnd.google-apps.document"
    APPLICATION_VND_GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
    APPLICATION_VND_GOOGLE_FOLDER = "application/vnd.google-apps.folder"

    # Notion types
    APPLICATION_VND_NOTION_PAGE = "application/vnd.notion.page"
    APPLICATION_VND_NOTION_DATABASE = "application/vnd.notion.database"


class FetchMetadata(BaseModel):
    """Metadata about a fetched resource."""

    source_uri: str = Field(default="", description="Original URI of the fetched resource")
    fetched_at: datetime = Field(
        default_factory=datetime.now, description="Timestamp when resource was fetched"
    )
    source_name: Optional[str] = Field(None, description="Name of the source handler used")
    mime_type: Optional[str] = Field(None, description="MIME type of the resource")
    file_size: Optional[int] = Field(None, ge=0, description="Size in bytes")
    fetch_duration_ms: Optional[float] = Field(
        None, ge=0, description="Time taken to fetch in milliseconds"
    )
    cache_hit: bool = Field(False, description="Whether this was a cache hit")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP headers if applicable")
    status_code: Optional[int] = Field(None, description="HTTP status code if applicable")
    encoding: Optional[str] = Field(None, description="Content encoding")
    last_modified: Optional[datetime] = Field(None, description="Last modification time")
    etag: Optional[str] = Field(None, description="ETag header value")


class BaseFetchedData(BaseModel):
    """Base model for all fetched data."""

    metadata: FetchMetadata = Field(..., description="Metadata about the fetch operation")
    category: DataCategory = Field(..., description="Category of the data")
    media_type: MediaType = Field(..., description="Media type of the data")
    tags: list[str] = Field(
        default_factory=list, description="Tags for categorization and filtering"
    )

    model_config = ConfigDict(use_enum_values=True)
