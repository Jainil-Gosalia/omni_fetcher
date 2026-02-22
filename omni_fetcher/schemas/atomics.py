from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Any, Optional, List

from pydantic import BaseModel, Field


class AtomicBase(BaseModel):
    """Base model for all atomic schemas."""

    source_uri: str
    fetched_at: datetime = Field(default_factory=datetime.now)
    content_hash: str = ""
    tags: list[str] = Field(default_factory=list)

    def model_post_init(self, __context) -> None:
        """Compute content_hash after initialization."""
        super().model_post_init(__context)
        if not self.content_hash:
            content = self._get_primary_content()
            if content:
                self.content_hash = hashlib.sha256(content.encode()).hexdigest()

    def _get_primary_content(self) -> str:
        """Override in subclasses to provide content for hashing."""
        return ""


class TextFormat(str, Enum):
    PLAIN = "plain"
    MARKDOWN = "markdown"
    HTML = "html"
    RST = "rst"
    CODE = "code"
    TRANSCRIPT = "transcript"


class TextDocument(AtomicBase):
    """Atomic text document schema."""

    content: str
    format: TextFormat
    language: Optional[str] = None
    encoding: Optional[str] = "utf-8"
    char_count: Optional[int] = None
    word_count: Optional[int] = None

    def model_post_init(self, __context) -> None:
        """Compute char_count and word_count after initialization."""
        super().model_post_init(__context)
        if self.char_count is None:
            self.char_count = len(self.content)
        if self.word_count is None:
            self.word_count = len(self.content.split())

    def _get_primary_content(self) -> str:
        return self.content


class AudioDocument(AtomicBase):
    """Atomic audio document schema."""

    duration_seconds: float
    format: str
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    transcript: Optional[TextDocument] = None
    language: Optional[str] = None
    # File metadata
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    # Audio metadata
    artist: Optional[str] = None
    album: Optional[str] = None
    track_number: Optional[int] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    raw: Optional[bytes] = None

    def _get_primary_content(self) -> str:
        if self.transcript:
            return self.transcript.content
        return ""


class ImageDocument(AtomicBase):
    """Atomic image document schema."""

    format: str
    width: Optional[int] = None
    height: Optional[int] = None
    alt_text: Optional[str] = None
    ocr_text: Optional[TextDocument] = None
    caption: Optional[str] = None
    # File metadata
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    # Image/EXIF metadata
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    orientation: Optional[int] = None
    # Web context
    photographer: Optional[str] = None
    license: Optional[str] = None
    page_url: Optional[str] = None
    raw: Optional[bytes] = None

    def _get_primary_content(self) -> str:
        if self.ocr_text:
            return self.ocr_text.content
        if self.alt_text:
            return self.alt_text
        if self.caption:
            return self.caption
        return ""


class VideoDocument(AtomicBase):
    """Atomic video document schema."""

    duration_seconds: float
    format: str
    resolution: Optional[tuple[int, int]] = None
    fps: Optional[float] = None
    audio: Optional[AudioDocument] = None
    thumbnail: Optional[ImageDocument] = None
    captions: Optional[TextDocument] = None
    # File metadata
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    raw: Optional[bytes] = None

    def _get_primary_content(self) -> str:
        if self.captions:
            return self.captions.content
        if self.audio and self.audio.transcript:
            return self.audio.transcript.content
        return ""


class SheetData(BaseModel):
    """Sheet data within a spreadsheet."""

    name: str
    headers: Optional[List[str]] = None
    rows: List[List[Any]]
    row_count: int
    col_count: int


class SpreadsheetDocument(AtomicBase):
    """Atomic spreadsheet document schema."""

    sheets: List[SheetData]
    format: str
    sheet_count: int

    def _get_primary_content(self) -> str:
        content_parts = []
        for sheet in self.sheets:
            if sheet.headers:
                content_parts.extend(sheet.headers)
            for row in sheet.rows:
                content_parts.extend(str(cell) for cell in row)
        return "\n".join(content_parts)
