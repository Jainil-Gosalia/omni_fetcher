"""Document Pydantic models for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, List

from pydantic import Field

from omni_fetcher.schemas.base import BaseFetchedData, MediaType, DataCategory, FetchMetadata
from omni_fetcher.schemas.atomics import (
    TextDocument,
    ImageDocument,
    SpreadsheetDocument,
)


class WebPageDocument(BaseFetchedData):
    """Clean webpage with extracted content.

    Replaces HTMLDocument in v0.4.0.
    """

    url: str
    title: Optional[str] = None
    body: TextDocument  # clean extracted text, NOT raw HTML
    images: List[ImageDocument] = Field(default_factory=list)
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    language: Optional[str] = None
    status_code: int = 200
    metadata: FetchMetadata = Field(default_factory=lambda: FetchMetadata(source_uri=""))
    category: DataCategory = DataCategory.DOCUMENT
    media_type: MediaType = MediaType.TEXT_HTML


class SlideDocument(BaseFetchedData):
    """Individual slide in a PPTX presentation."""

    slide_number: int = Field(..., ge=1)
    title: Optional[str] = None
    text: TextDocument
    images: List[ImageDocument] = Field(default_factory=list)
    metadata: FetchMetadata = Field(default_factory=lambda: FetchMetadata(source_uri=""))
    category: DataCategory = DataCategory.DOCUMENT
    media_type: MediaType = MediaType.APPLICATION_PPTX


class DOCXDocument(BaseFetchedData):
    """Microsoft Word document."""

    text: TextDocument
    images: List[ImageDocument] = Field(default_factory=list)
    tables: List[SpreadsheetDocument] = Field(default_factory=list)
    page_count: Optional[int] = Field(None, ge=0)
    author: Optional[str] = None
    title: Optional[str] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    metadata: FetchMetadata = Field(default_factory=lambda: FetchMetadata(source_uri=""))
    category: DataCategory = DataCategory.DOCUMENT
    media_type: MediaType = MediaType.APPLICATION_DOCX


class PPTXDocument(BaseFetchedData):
    """Microsoft PowerPoint presentation."""

    slides: List[SlideDocument] = Field(default_factory=list)
    slide_count: int = Field(0, ge=0)
    author: Optional[str] = None
    title: Optional[str] = None
    metadata: FetchMetadata = Field(default_factory=lambda: FetchMetadata(source_uri=""))
    category: DataCategory = DataCategory.DOCUMENT
    media_type: MediaType = MediaType.APPLICATION_PPTX


class PDFDocument(BaseFetchedData):
    """Model for PDF document."""

    media_type: MediaType = MediaType.APPLICATION_PDF
    metadata: FetchMetadata = Field(default_factory=lambda: FetchMetadata(source_uri=""))
    category: DataCategory = DataCategory.DOCUMENT
    text: TextDocument
    images: List[ImageDocument] = Field(default_factory=list)
    author: Optional[str] = Field(None, description="PDF author")
    subject: Optional[str] = Field(None, description="PDF subject")
    creator: Optional[str] = Field(None, description="PDF creator application")
    producer: Optional[str] = Field(None, description="PDF producer")
    creation_date: Optional[datetime] = Field(None, description="PDF creation date")
    modification_date: Optional[datetime] = Field(None, description="PDF modification date")
    page_count: Optional[int] = Field(None, ge=0, description="Number of pages")
    language: Optional[str] = Field(None, description="PDF language")
    is_encrypted: bool = Field(False, description="Whether PDF is encrypted")
    is_signed: bool = Field(False, description="Whether PDF is digitally signed")
