"""Tests for atomic schemas."""

import pytest
from datetime import datetime

from omni_fetcher.schemas.atomics import (
    AtomicBase,
    TextDocument,
    TextFormat,
    AudioDocument,
    ImageDocument,
    VideoDocument,
    SpreadsheetDocument,
    SheetData,
)


class TestAtomicBase:
    """Tests for AtomicBase."""

    def test_source_uri_and_fetched_at(self):
        """Test that source_uri and fetched_at are properly set."""
        doc = TextDocument(
            source_uri="https://example.com/text.txt",
            content="Hello world",
            format=TextFormat.PLAIN,
        )
        assert doc.source_uri == "https://example.com/text.txt"
        assert doc.fetched_at is not None
        assert isinstance(doc.fetched_at, datetime)

    def test_content_hash_auto_computed(self):
        """Test that content_hash is auto-computed from content."""
        doc = TextDocument(
            source_uri="https://example.com/text.txt",
            content="Hello world",
            format=TextFormat.PLAIN,
        )
        assert doc.content_hash != ""
        import hashlib

        expected_hash = hashlib.sha256("Hello world".encode()).hexdigest()
        assert doc.content_hash == expected_hash


class TestTextDocument:
    """Tests for TextDocument."""

    def test_creation(self):
        """Test creating a TextDocument with required fields."""
        doc = TextDocument(
            source_uri="https://example.com/text.txt",
            content="Hello world",
            format=TextFormat.PLAIN,
        )
        assert doc.content == "Hello world"
        assert doc.format == TextFormat.PLAIN
        assert doc.source_uri == "https://example.com/text.txt"

    def test_char_count_and_word_count_auto_computed(self):
        """Test that char_count and word_count are auto-computed."""
        doc = TextDocument(
            source_uri="https://example.com/text.txt",
            content="Hello world",
            format=TextFormat.PLAIN,
        )
        assert doc.char_count == 11
        assert doc.word_count == 2

    def test_text_format_enum(self):
        """Test TextFormat enum values."""
        assert TextFormat.PLAIN.value == "plain"
        assert TextFormat.MARKDOWN.value == "markdown"
        assert TextFormat.HTML.value == "html"
        assert TextFormat.RST.value == "rst"
        assert TextFormat.CODE.value == "code"
        assert TextFormat.TRANSCRIPT.value == "transcript"

    def test_all_text_formats(self):
        """Test creating TextDocument with all text formats."""
        formats = [
            TextFormat.PLAIN,
            TextFormat.MARKDOWN,
            TextFormat.HTML,
            TextFormat.RST,
            TextFormat.CODE,
            TextFormat.TRANSCRIPT,
        ]
        for fmt in formats:
            doc = TextDocument(
                source_uri="https://example.com/test.txt",
                content="Test content",
                format=fmt,
            )
            assert doc.format == fmt


class TestAudioDocument:
    """Tests for AudioDocument."""

    def test_creation(self):
        """Test creating an AudioDocument with required fields."""
        audio = AudioDocument(
            source_uri="https://example.com/audio.mp3",
            duration_seconds=120.5,
            format="mp3",
        )
        assert audio.source_uri == "https://example.com/audio.mp3"
        assert audio.duration_seconds == 120.5
        assert audio.format == "mp3"

    def test_optional_fields(self):
        """Test AudioDocument with optional fields."""
        transcript = TextDocument(
            source_uri="https://example.com/transcript.txt",
            content="Audio transcript",
            format=TextFormat.TRANSCRIPT,
        )
        audio = AudioDocument(
            source_uri="https://example.com/audio.mp3",
            duration_seconds=120.5,
            format="mp3",
            sample_rate=44100,
            channels=2,
            transcript=transcript,
            language="en",
        )
        assert audio.sample_rate == 44100
        assert audio.channels == 2
        assert audio.transcript is not None
        assert audio.transcript.content == "Audio transcript"
        assert audio.language == "en"


class TestImageDocument:
    """Tests for ImageDocument."""

    def test_creation(self):
        """Test creating an ImageDocument with required fields."""
        image = ImageDocument(
            source_uri="https://example.com/image.jpg",
            format="jpeg",
        )
        assert image.source_uri == "https://example.com/image.jpg"
        assert image.format == "jpeg"

    def test_optional_fields(self):
        """Test ImageDocument with optional fields."""
        ocr_text = TextDocument(
            source_uri="https://example.com/ocr.txt",
            content="Extracted text from image",
            format=TextFormat.PLAIN,
        )
        image = ImageDocument(
            source_uri="https://example.com/image.jpg",
            format="jpeg",
            width=1920,
            height=1080,
            alt_text="A beautiful landscape",
            ocr_text=ocr_text,
            caption="Image caption",
        )
        assert image.width == 1920
        assert image.height == 1080
        assert image.alt_text == "A beautiful landscape"
        assert image.ocr_text is not None
        assert image.caption == "Image caption"


class TestVideoDocument:
    """Tests for VideoDocument."""

    def test_creation(self):
        """Test creating a VideoDocument with required fields."""
        video = VideoDocument(
            source_uri="https://example.com/video.mp4",
            duration_seconds=3600.0,
            format="mp4",
        )
        assert video.source_uri == "https://example.com/video.mp4"
        assert video.duration_seconds == 3600.0
        assert video.format == "mp4"

    def test_composition_with_audio_and_thumbnail(self):
        """Test VideoDocument with audio and thumbnail composition."""
        audio = AudioDocument(
            source_uri="https://example.com/audio.aac",
            duration_seconds=3600.0,
            format="aac",
            transcript=TextDocument(
                source_uri="https://example.com/transcript.txt",
                content="Video transcript",
                format=TextFormat.TRANSCRIPT,
            ),
        )
        thumbnail = ImageDocument(
            source_uri="https://example.com/thumb.jpg",
            format="jpeg",
            width=320,
            height=180,
        )
        video = VideoDocument(
            source_uri="https://example.com/video.mp4",
            duration_seconds=3600.0,
            format="mp4",
            resolution=(1920, 1080),
            fps=30.0,
            audio=audio,
            thumbnail=thumbnail,
            captions=TextDocument(
                source_uri="https://example.com/captions.srt",
                content="Caption text",
                format=TextFormat.TRANSCRIPT,
            ),
        )
        assert video.audio is not None
        assert video.audio.transcript is not None
        assert video.thumbnail is not None
        assert video.captions is not None
        assert video.resolution == (1920, 1080)
        assert video.fps == 30.0


class TestSpreadsheetDocument:
    """Tests for SpreadsheetDocument."""

    def test_creation_with_sheets(self):
        """Test creating a SpreadsheetDocument with sheets."""
        sheets = [
            SheetData(
                name="Sheet1",
                headers=["Name", "Age", "City"],
                rows=[["Alice", 30, "NYC"], ["Bob", 25, "LA"]],
                row_count=2,
                col_count=3,
            ),
            SheetData(
                name="Sheet2",
                headers=["Product", "Price"],
                rows=[["Widget", 10.99], ["Gadget", 25.50]],
                row_count=2,
                col_count=2,
            ),
        ]
        spreadsheet = SpreadsheetDocument(
            source_uri="https://example.com/data.xlsx",
            sheets=sheets,
            format="xlsx",
            sheet_count=2,
        )
        assert spreadsheet.source_uri == "https://example.com/data.xlsx"
        assert len(spreadsheet.sheets) == 2
        assert spreadsheet.sheet_count == 2

    def test_sheet_data(self):
        """Test SheetData structure."""
        sheet = SheetData(
            name="Data",
            headers=["ID", "Name", "Value"],
            rows=[[1, "Item1", 100], [2, "Item2", 200]],
            row_count=2,
            col_count=3,
        )
        assert sheet.name == "Data"
        assert sheet.headers == ["ID", "Name", "Value"]
        assert len(sheet.rows) == 2
        assert sheet.row_count == 2
        assert sheet.col_count == 3
