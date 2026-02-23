"""Google Drive, Sheets, Docs, and Slides schemas for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from typing_extensions import Self

from pydantic import BaseModel, Field, model_validator

from omni_fetcher.schemas.base import DataCategory, MediaType
from omni_fetcher.schemas.atomics import TextDocument, SpreadsheetDocument


class GoogleDriveFile(BaseModel):
    """Google Drive file representation."""

    file_id: str = Field(..., description="Google Drive file ID")
    name: str = Field(..., description="File name")
    mime_type: str = Field(..., description="MIME type")
    size: Optional[int] = Field(None, ge=0, description="File size in bytes")
    created_time: Optional[datetime] = Field(None, description="File creation time")
    modified_time: Optional[datetime] = Field(None, description="File modification time")
    parents: list[str] = Field(default_factory=list, description="Parent folder IDs")
    web_view_link: Optional[str] = Field(None, description="Web view link")
    web_content_link: Optional[str] = Field(None, description="Direct download link")
    icon_link: Optional[str] = Field(None, description="File type icon")
    thumbnail_link: Optional[str] = Field(None, description="Thumbnail URL")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["google", "drive"]
        if self.mime_type:
            if "folder" in self.mime_type.lower():
                tags.append("folder")
            elif "spreadsheet" in self.mime_type.lower():
                tags.append("spreadsheet")
            elif "document" in self.mime_type.lower():
                tags.append("document")
            elif "presentation" in self.mime_type.lower():
                tags.append("presentation")
            elif "image" in self.mime_type.lower():
                tags.append("image")
            elif "video" in self.mime_type.lower():
                tags.append("video")
            elif "pdf" in self.mime_type.lower():
                tags.append("pdf")
        self.tags = tags


class GoogleDriveFolder(BaseModel):
    """Google Drive folder representation."""

    folder_id: str = Field(..., description="Google Drive folder ID")
    name: str = Field(..., description="Folder name")
    created_time: Optional[datetime] = Field(None, description="Folder creation time")
    modified_time: Optional[datetime] = Field(None, description="Folder modification time")
    parents: list[str] = Field(default_factory=list, description="Parent folder IDs")
    web_view_link: Optional[str] = Field(None, description="Web view link")
    files: list[GoogleDriveFile] = Field(default_factory=list, description="Files in this folder")
    subfolders: list[GoogleDriveFolder] = Field(default_factory=list, description="Subfolders")
    file_count: int = Field(default=0, ge=0, description="Number of files in folder")
    folder_count: int = Field(default=0, ge=0, description="Number of subfolders")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from all files and subfolders."""
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["google", "drive", "folder"])
        for f in self.files:
            if f.tags:
                all_tags.update(f.tags)
        for sf in self.subfolders:
            if sf.tags:
                all_tags.update(sf.tags)
        self.tags = sorted(all_tags)
        return self


class GoogleDriveContainer(BaseModel):
    """Google Drive container for folder listings."""

    folder_id: Optional[str] = Field(None, description="Folder ID if specific folder")
    folder_name: Optional[str] = Field(None, description="Folder name")
    items: list[GoogleDriveFile] = Field(default_factory=list, description="Files in container")
    subfolders: list[GoogleDriveFolder] = Field(default_factory=list, description="Subfolders")
    source_uri: str = Field(default="", description="Original URI")
    fetched_at: datetime = Field(default_factory=datetime.now, description="Fetch timestamp")
    source_name: str = Field(default="google_drive", description="Source name")
    item_count: int = Field(0, ge=0, description="Total file count")
    folder_count: int = Field(0, ge=0, description="Total subfolder count")
    next_page_token: Optional[str] = Field(None, description="Token for next page")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.APPLICATION_OCTET
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from all items."""
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["google", "drive"])
        for item in self.items:
            if item.tags:
                all_tags.update(item.tags)
        for folder in self.subfolders:
            if folder.tags:
                all_tags.update(folder.tags)
        self.tags = sorted(all_tags)
        return self


class GoogleSheetsSpreadsheet(BaseModel):
    """Google Sheets spreadsheet representation."""

    spreadsheet_id: str = Field(..., description="Spreadsheet ID")
    spreadsheet_title: str = Field(..., description="Spreadsheet title")
    sheets: list[str] = Field(default_factory=list, description="Sheet names")
    spreadsheet_url: Optional[str] = Field(None, description="Spreadsheet URL")
    created_at: Optional[datetime] = Field(None, description="Spreadsheet creation time")
    modified_at: Optional[datetime] = Field(None, description="Spreadsheet modification time")
    data: Optional[SpreadsheetDocument] = Field(
        None, description="Spreadsheet data as SpreadsheetDocument"
    )
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.APPLICATION_VND_GOOGLE_SPREADSHEET
    category: DataCategory = DataCategory.STRUCTURED

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["google", "sheets", "spreadsheet"]
        self.tags = tags


class GoogleDocsDocument(BaseModel):
    """Google Docs document representation."""

    document_id: str = Field(..., description="Document ID")
    title: str = Field(..., description="Document title")
    document_url: Optional[str] = Field(None, description="Document URL")
    created_at: Optional[datetime] = Field(None, description="Document creation time")
    modified_at: Optional[datetime] = Field(None, description="Document modification time")
    text: TextDocument = Field(..., description="Document text content")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.APPLICATION_VND_GOOGLE_DOCUMENT
    category: DataCategory = DataCategory.DOCUMENT

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from child fields."""
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["google", "docs", "document"])
        if self.text and getattr(self.text, "tags", None):
            all_tags.update(self.text.tags)
        self.tags = sorted(all_tags)
        return self


class GoogleSlidesPresentation(BaseModel):
    """Google Slides presentation representation."""

    presentation_id: str = Field(..., description="Presentation ID")
    title: str = Field(..., description="Presentation title")
    presentation_url: Optional[str] = Field(None, description="Presentation URL")
    created_at: Optional[datetime] = Field(None, description="Presentation creation time")
    modified_at: Optional[datetime] = Field(None, description="Presentation modification time")
    slide_count: int = Field(0, ge=0, description="Number of slides")
    slides: list[TextDocument] = Field(
        default_factory=list, description="Slide text content as TextDocuments"
    )
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.APPLICATION_VND_GOOGLE_SLIDES
    category: DataCategory = DataCategory.DOCUMENT

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from child fields."""
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["google", "slides", "presentation"])
        for slide in self.slides:
            if slide and getattr(slide, "tags", None):
                all_tags.update(slide.tags)
        self.tags = sorted(all_tags)
        return self
