"""Document Pydantic models for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from omni_fetcher.schemas.base import BaseFetchedData, MediaType
from omni_fetcher.schemas.atomics import (
    TextDocument,
    ImageDocument,
)


class HTMLDocument(BaseFetchedData):
    """Model for HTML document."""

    media_type: MediaType = MediaType.TEXT_HTML
    text: TextDocument
    images: list[ImageDocument] = Field(default_factory=list)
    title: Optional[str] = Field(None, description="Document title")
    meta_description: Optional[str] = Field(None, description="Meta description")
    meta_keywords: Optional[list[str]] = Field(None, description="Meta keywords")
    links: Optional[list[str]] = Field(None, description="All links in document")
    scripts: Optional[list[str]] = Field(None, description="Script URLs")
    stylesheets: Optional[list[str]] = Field(None, description="Stylesheet URLs")
    og_tags: Optional[dict[str, str]] = Field(None, description="Open Graph tags")


class PDFDocument(BaseFetchedData):
    """Model for PDF document."""

    media_type: MediaType = MediaType.APPLICATION_PDF
    text: TextDocument
    images: list[ImageDocument] = Field(default_factory=list)
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
