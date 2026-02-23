"""Notion schemas for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from typing_extensions import Self

from pydantic import BaseModel, Field, model_validator

from omni_fetcher.schemas.base import DataCategory, MediaType
from omni_fetcher.schemas.atomics import TextDocument, SpreadsheetDocument


class NotionRichText(BaseModel):
    """Rich text element in Notion."""

    plain_text: str = Field(default="", description="Plain text content")
    html: Optional[str] = Field(None, description="HTML representation")
    markdown: str = Field(default="", description="Markdown representation")
    annotations_bold: bool = Field(default=False, description="Bold formatting")
    annotations_italic: bool = Field(default=False, description="Italic formatting")
    annotations_strikethrough: bool = Field(default=False, description="Strikethrough")
    annotations_underline: bool = Field(default=False, description="Underline")
    annotations_code: bool = Field(default=False, description="Code formatting")
    annotations_color: str = Field(default="default", description="Text color")
    href: Optional[str] = Field(None, description="Link URL")
    text_type: str = Field(default="text", description="Text type (text, mention, equation)")


class NotionUser(BaseModel):
    """Notion user representation."""

    id: str = Field(..., description="User ID")
    name: Optional[str] = Field(None, description="User name")
    avatar_url: Optional[str] = Field(None, description="Avatar URL")
    type: str = Field(default="person", description="User type")


class NotionProperty(BaseModel):
    """Notion page property value."""

    id: str = Field(..., description="Property ID")
    name: str = Field(..., description="Property name")
    type: str = Field(..., description="Property type")
    value: Any = Field(..., description="Property value")
    raw: dict[str, Any] = Field(default_factory=dict, description="Raw property data")


class NotionBlock(BaseModel):
    """Individual Notion block."""

    id: str = Field(..., description="Block ID")
    type: str = Field(..., description="Block type")
    content: Any = Field(..., description="Block content")
    has_children: bool = Field(default=False, description="Whether block has children")
    children: list[NotionBlock] = Field(default_factory=list, description="Child blocks")
    raw: dict[str, Any] = Field(default_factory=dict, description="Raw block data")


class NotionPage(BaseModel):
    """Notion page representation."""

    page_id: str = Field(..., description="Notion page ID")
    title: str = Field(..., description="Page title")
    url: Optional[str] = Field(None, description="Page URL")
    icon: Optional[str] = Field(None, description="Page icon (emoji or URL)")
    cover: Optional[str] = Field(None, description="Page cover image URL")
    created_time: Optional[datetime] = Field(None, description="Page creation time")
    last_edited_time: Optional[datetime] = Field(None, description="Last edit time")
    created_by: Optional[NotionUser] = Field(None, description="Page creator")
    last_edited_by: Optional[NotionUser] = Field(None, description="Last editor")
    properties: dict[str, NotionProperty] = Field(
        default_factory=dict, description="Page properties"
    )
    content: Optional[TextDocument] = Field(None, description="Page content as text")
    blocks: list[NotionBlock] = Field(default_factory=list, description="Page blocks")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.APPLICATION_VND_NOTION_PAGE
    category: DataCategory = DataCategory.DOCUMENT

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["notion", "page"]
        self.tags = tags


class NotionDatabase(BaseModel):
    """Notion database representation."""

    database_id: str = Field(..., description="Notion database ID")
    title: str = Field(..., description="Database title")
    url: Optional[str] = Field(None, description="Database URL")
    icon: Optional[str] = Field(None, description="Database icon (emoji or URL)")
    cover: Optional[str] = Field(None, description="Database cover image URL")
    created_time: Optional[datetime] = Field(None, description="Database creation time")
    last_edited_time: Optional[datetime] = Field(None, description="Last edit time")
    properties_schema: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Database schema (property definitions)"
    )
    items: list[NotionPage] = Field(default_factory=list, description="Database items (pages)")
    data: Optional[SpreadsheetDocument] = Field(None, description="Database as SpreadsheetDocument")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.APPLICATION_VND_NOTION_DATABASE
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from child fields."""
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["notion", "database"])
        if self.items:
            for item in self.items:
                if item.tags:
                    all_tags.update(item.tags)
        if self.data and getattr(self.data, "tags", None):
            all_tags.update(self.data.tags)
        self.tags = sorted(all_tags)
        return self

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["notion", "database"]
        self.tags = tags
