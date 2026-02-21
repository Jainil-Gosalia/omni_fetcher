"""Document Pydantic models for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from omni_fetcher.schemas.base import BaseFetchedData, DataCategory, MediaType
from omni_fetcher.schemas.atomics import (
    TextDocument as AtomicTextDocument,
    ImageDocument,
)


class BaseDocument(BaseFetchedData):
    """Base model for documents."""

    title: Optional[str] = Field(None, description="Document title")
    content: str = Field(..., description="Document content")

    category: DataCategory = DataCategory.DOCUMENT


class TextDocument(BaseDocument):
    """Model for plain text document."""

    media_type: MediaType = MediaType.TEXT_PLAIN
    encoding: str = Field("utf-8", description="Text encoding")
    line_count: Optional[int] = Field(None, ge=0, description="Number of lines")
    word_count: Optional[int] = Field(None, ge=0, description="Number of words")
    char_count: Optional[int] = Field(None, ge=0, description="Number of characters")


class MarkdownDocument(TextDocument):
    """Model for Markdown document."""

    media_type: MediaType = MediaType.TEXT_MARKDOWN
    front_matter: Optional[dict[str, Any]] = Field(None, description="YAML front matter")
    headings: Optional[list[str]] = Field(None, description="All heading text")
    links: Optional[list[str]] = Field(None, description="All URLs in document")
    code_blocks: Optional[list[str]] = Field(None, description="Code block contents")
    images: Optional[list[str]] = Field(None, description="Image references")


class HTMLDocument(BaseFetchedData):
    """Model for HTML document."""

    media_type: MediaType = MediaType.TEXT_HTML
    text: AtomicTextDocument
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
    text: AtomicTextDocument
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


class CSVData(BaseDocument):
    """Model for CSV data."""

    media_type: MediaType = MediaType.TEXT_CSV
    headers: list[str] = Field(default_factory=list, description="CSV headers")
    row_count: Optional[int] = Field(None, ge=0, description="Number of data rows")
    delimiter: str = Field(",", description="CSV delimiter character")
    quote_char: str = Field('"', description="Quote character")
    has_header: bool = Field(True, description="Whether first row is header")
    sample_rows: Optional[list[list[str]]] = Field(None, description="First few rows as samples")
