"""Container schemas for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Optional, TypeVar
from typing_extensions import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omni_fetcher.schemas.base import DataCategory, MediaType
from omni_fetcher.schemas.media import YouTubeVideo

T = TypeVar("T")


class BaseContainer(BaseModel, Generic[T]):
    """Base class for container schemas."""

    source_uri: str = Field(default="", description="Original URI of the container")
    fetched_at: datetime = Field(
        default_factory=datetime.now, description="Timestamp when container was fetched"
    )
    source_name: Optional[str] = Field(None, description="Name of the source handler used")
    item_count: int = Field(0, ge=0, description="Total number of items in container")
    fetched_fully: bool = Field(
        False, description="Whether all items were fetched or this is a partial result"
    )
    next_page_token: Optional[str] = Field(None, description="Token for fetching next page")
    tags: list[str] = Field(default_factory=list, description="Tags from all items in container")
    items: list[Any] = Field(default_factory=list, description="Items in container")

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from all items in container."""
        all_tags: set[str] = set(self.tags) if self.tags else set()

        for item in self.items:
            if item and getattr(item, "tags", None):
                all_tags.update(item.tags)

        self.tags = sorted(all_tags)
        return self

    model_config = ConfigDict(use_enum_values=True)


class RSSItem(BaseModel):
    """RSS/Atom feed item."""

    title: Optional[str] = Field(None, description="Item title")
    link: Optional[str] = Field(None, description="Item link")
    summary: Optional[str] = Field(None, description="Item summary/description")
    content: Optional[str] = Field(None, description="Full content")
    author: Optional[str] = Field(None, description="Item author")
    published: Optional[str] = Field(None, description="Publication date")
    updated: Optional[str] = Field(None, description="Update date")
    guid: Optional[str] = Field(None, description="Unique identifier")
    categories: Optional[list[str]] = Field(None, description="Item categories (RSS spec)")
    tags: list[str] = Field(default_factory=list, description="Item tags (omni_fetcher system)")


class RSSFeed(BaseContainer[RSSItem]):
    """RSS/Atom feed container."""

    title: Optional[str] = Field(None, description="Feed title")
    description: Optional[str] = Field(None, description="Feed description")
    link: Optional[str] = Field(None, description="Feed link")
    language: Optional[str] = Field(None, description="Feed language")
    updated: Optional[str] = Field(None, description="Last update time")
    items: list[RSSItem] = Field(default_factory=list, description="Feed items")

    media_type: MediaType = MediaType.TEXT_XML
    category: DataCategory = DataCategory.STRUCTURED


class S3Object(BaseModel):
    """S3 object summary."""

    key: str = Field(..., description="Object key")
    size: Optional[int] = Field(None, ge=0, description="Object size in bytes")
    last_modified: Optional[datetime] = Field(None, description="Last modification time")
    etag: Optional[str] = Field(None, description="Object ETag")
    content_type: Optional[str] = Field(None, description="Content type")
    storage_class: Optional[str] = Field(None, description="Storage class")


class S3Bucket(BaseContainer[S3Object]):
    """S3 bucket container."""

    bucket_name: str = Field(..., description="Bucket name")
    prefix: Optional[str] = Field(None, description="Prefix/path filter")
    items: list[S3Object] = Field(default_factory=list, description="S3 objects")
    continuation_token: Optional[str] = Field(None, description="Token for next list operation")

    media_type: MediaType = MediaType.APPLICATION_OCTET
    category: DataCategory = DataCategory.STRUCTURED


class YouTubePlaylist(BaseContainer[YouTubeVideo]):
    """YouTube playlist container."""

    playlist_id: Optional[str] = Field(None, description="Playlist ID")
    title: Optional[str] = Field(None, description="Playlist title")
    description: Optional[str] = Field(None, description="Playlist description")
    uploader: Optional[str] = Field(None, description="Playlist uploader/channel")
    uploader_url: Optional[str] = Field(None, description="Channel URL")
    item_count_total: Optional[int] = Field(None, ge=0, description="Total playlist item count")
    view_count: Optional[int] = Field(None, ge=0, description="Playlist view count")
    items: list[YouTubeVideo] = Field(default_factory=list, description="Playlist videos")

    media_type: MediaType = MediaType.VIDEO_MP4
    category: DataCategory = DataCategory.MEDIA
