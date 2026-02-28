"""SharePoint schemas for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from typing_extensions import Self

from pydantic import BaseModel, Field, model_validator

from omni_fetcher.schemas.base import DataCategory, MediaType
from omni_fetcher.schemas.atomics import TextDocument


def _get_mime_tag(mime_type: str) -> str:
    """Extract tag from mime type."""
    if not mime_type:
        return "unknown"
    if "/" in mime_type:
        main_type = mime_type.split("/")[1]
        if "xlsx" in main_type or "spreadsheet" in main_type:
            return "xlsx"
        if "wordprocessing" in main_type:
            return "docx"
        if "presentation" in main_type:
            return "pptx"
        if "pdf" in main_type:
            return "pdf"
        if main_type.startswith("vnd.openxmlformats"):
            if "spreadsheet" in main_type:
                return "xlsx"
            if "wordprocessing" in main_type:
                return "docx"
            if "presentation" in main_type:
                return "pptx"
        return main_type.split(".")[-1].split("+")[0]
    return mime_type


class SharePointFile(BaseModel):
    """SharePoint file representation."""

    file_id: str = Field(..., description="Graph API item ID")
    name: str = Field(..., description="File name")
    path: str = Field(..., description="Relative path within library")
    site: str = Field(..., description="Site name")
    library: str = Field(..., description="Library name")
    mime_type: str = Field(..., description="MIME type")
    size_bytes: Optional[int] = Field(None, description="File size in bytes")
    content: Optional[Any] = Field(None, description="File content as atomic")
    author: Optional[str] = Field(None, description="Created by")
    last_modified_by: Optional[str] = Field(None, description="Last modified by")
    created_at: datetime = Field(..., description="Creation timestamp")
    modified_at: datetime = Field(..., description="Last modification timestamp")
    url: str = Field(..., description="Web URL")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["sharepoint", "file"]
        tags.append(_get_mime_tag(self.mime_type))
        self.tags = tags


class SharePointLibrary(BaseModel):
    """SharePoint library (drive) representation."""

    library_id: str = Field(..., description="Graph API drive ID")
    name: str = Field(..., description="Library name")
    site: str = Field(..., description="Site name")
    description: Optional[str] = Field(None, description="Library description")
    items: list[SharePointFile] = Field(default_factory=list, description="Files in library")
    item_count: int = Field(0, ge=0, description="Total file count")
    url: str = Field(..., description="Web URL")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["sharepoint", "library"])
        for item in self.items:
            if item and getattr(item, "tags", None):
                all_tags.update(item.tags)
        self.tags = sorted(all_tags)
        return self


class SharePointSite(BaseModel):
    """SharePoint site representation."""

    site_id: str = Field(..., description="Graph API site ID")
    name: str = Field(..., description="Site name (URL path)")
    display_name: str = Field(..., description="Site display name")
    description: Optional[TextDocument] = Field(None, description="Site description")
    hostname: str = Field(..., description=" hostname (company.sharepoint.com)")
    items: list[SharePointLibrary] = Field(default_factory=list, description="Libraries in site")
    url: str = Field(..., description="Web URL")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["sharepoint", "site"])
        for item in self.items:
            if item and getattr(item, "tags", None):
                all_tags.update(item.tags)
        self.tags = sorted(all_tags)
        return self
