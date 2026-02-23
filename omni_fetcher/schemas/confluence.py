"""Confluence schemas for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from typing_extensions import Self

from pydantic import BaseModel, Field, model_validator

from omni_fetcher.schemas.base import DataCategory, MediaType
from omni_fetcher.schemas.atomics import TextDocument


class ConfluenceUser(BaseModel):
    """Confluence user representation."""

    user_id: str = Field(..., description="User account ID")
    display_name: str = Field(..., description="User display name")
    username: Optional[str] = Field(None, description="Username")
    email: Optional[str] = Field(None, description="User email")
    profile_picture: Optional[str] = Field(None, description="Profile picture URL")


class ConfluencePage(BaseModel):
    """Confluence page representation."""

    page_id: str = Field(..., description="Confluence page ID")
    title: str = Field(..., description="Page title")
    space_key: Optional[str] = Field(None, description="Confluence space key")
    url: Optional[str] = Field(None, description="Page URL")
    icon: Optional[str] = Field(None, description="Page icon (emoji)")
    parent_id: Optional[str] = Field(None, description="Parent page ID")
    version: int = Field(default=1, ge=1, description="Page version number")
    created_at: Optional[datetime] = Field(None, description="Page creation time")
    updated_at: Optional[datetime] = Field(None, description="Last update time")
    created_by: Optional[ConfluenceUser] = Field(None, description="Page creator")
    last_updated_by: Optional[ConfluenceUser] = Field(None, description="Last updater")
    content: Optional[TextDocument] = Field(None, description="Page content as text")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.TEXT_HTML
    category: DataCategory = DataCategory.DOCUMENT

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["confluence", "page"]
        if self.space_key:
            tags.append(f"space:{self.space_key.lower()}")
        self.tags = tags


class ConfluenceAttachment(BaseModel):
    """Confluence page attachment."""

    attachment_id: str = Field(..., description="Attachment ID")
    filename: str = Field(..., description="File name")
    media_type: str = Field(..., description="MIME type")
    size: int = Field(..., ge=0, description="File size in bytes")
    url: Optional[str] = Field(None, description="Download URL")
    web_url: Optional[str] = Field(None, description="Web view URL")
    created_at: Optional[datetime] = Field(None, description="Upload time")
    created_by: Optional[ConfluenceUser] = Field(None, description="Uploader")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["confluence", "attachment"]
        if self.media_type:
            if "image" in self.media_type.lower():
                tags.append("image")
            elif "pdf" in self.media_type.lower():
                tags.append("pdf")
            elif "video" in self.media_type.lower():
                tags.append("video")
            elif "audio" in self.media_type.lower():
                tags.append("audio")
            else:
                tags.append("document")
        self.tags = tags


class ConfluenceSpace(BaseModel):
    """Confluence space (container with pages and attachments)."""

    space_key: str = Field(..., description="Confluence space key")
    name: str = Field(..., description="Space name")
    description: Optional[str] = Field(None, description="Space description")
    url: Optional[str] = Field(None, description="Space URL")
    icon: Optional[str] = Field(None, description="Space icon (emoji)")
    type: str = Field(default="global", description="Space type (global, personal)")
    status: str = Field(default="current", description="Space status")
    homepage_id: Optional[str] = Field(None, description="Homepage page ID")
    created_at: Optional[datetime] = Field(None, description="Space creation time")
    updated_at: Optional[datetime] = Field(None, description="Last update time")
    pages: list[ConfluencePage] = Field(default_factory=list, description="Pages in space")
    attachments: list[ConfluenceAttachment] = Field(
        default_factory=list, description="Attachments in space"
    )
    source_uri: str = Field(default="", description="Original URI")
    fetched_at: datetime = Field(default_factory=datetime.now, description="Fetch timestamp")
    source_name: str = Field(default="confluence", description="Source name")
    page_count: int = Field(0, ge=0, description="Total page count")
    attachment_count: int = Field(0, ge=0, description="Total attachment count")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.APPLICATION_OCTET
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from all pages and attachments."""
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["confluence", "space"])
        for page in self.pages:
            if page.tags:
                all_tags.update(page.tags)
        for attachment in self.attachments:
            if attachment.tags:
                all_tags.update(attachment.tags)
        self.tags = sorted(all_tags)
        return self


class ConfluenceComment(BaseModel):
    """Confluence comment on a page."""

    comment_id: str = Field(..., description="Comment ID")
    page_id: str = Field(..., description="Page ID")
    content: Optional[TextDocument] = Field(None, description="Comment content as text")
    created_at: Optional[datetime] = Field(None, description="Comment creation time")
    updated_at: Optional[datetime] = Field(None, description="Last update time")
    created_by: Optional[ConfluenceUser] = Field(None, description="Comment author")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["confluence", "comment"]
        self.tags = tags
