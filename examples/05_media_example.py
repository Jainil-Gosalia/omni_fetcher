"""Media handling example for OmniFetcher - using atomic schemas."""

import asyncio
from datetime import datetime

from omni_fetcher.schemas.atomics import (
    TextDocument,
    AudioDocument,
    ImageDocument,
    VideoDocument,
    SpreadsheetDocument,
    SheetData,
    TextFormat,
)
from omni_fetcher.schemas.media import LocalVideo, LocalAudio, LocalImage, YouTubeVideo
from omni_fetcher.schemas.base import FetchMetadata


async def main():
    print("=" * 60)
    print("Media Handling Examples - Using Atomic Schemas")
    print("=" * 60)

    print("\n1. Atomic VideoDocument (replaces LocalVideo)")
    print("-" * 40)

    video = VideoDocument(
        source_uri="file:///home/user/videos/movie.mp4",
        duration_seconds=7200.0,
        format="mp4",
        resolution=(1920, 1080),
        fps=30.0,
        thumbnail=ImageDocument(
            source_uri="file:///home/user/videos/movie_thumb.jpg",
            format="jpeg",
            width=320,
            height=180,
            alt_text="Movie thumbnail",
        ),
        captions=TextDocument(
            source_uri="file:///home/user/videos/movie.vtt",
            content="00:00:00 --> Hello and welcome...",
            format=TextFormat.TRANSCRIPT,
        ),
    )

    print(f"Source: {video.source_uri}")
    print(f"Duration: {video.duration_seconds / 3600:.1f} hours")
    print(f"Resolution: {video.resolution}")
    print(f"Thumbnail: {video.thumbnail.width}x{video.thumbnail.height} ({video.thumbnail.format})")
    print(f"Content hash: {video.content_hash[:16]}...")

    print("\n2. Atomic AudioDocument (replaces LocalAudio)")
    print("-" * 40)

    audio = AudioDocument(
        source_uri="file:///music/song.flac",
        duration_seconds=245.0,
        format="flac",
        sample_rate=44100,
        channels=2,
        language="en",
        transcript=TextDocument(
            source_uri="file:///music/song.txt",
            content="Verse 1: This is the song lyrics...",
            format=TextFormat.TRANSCRIPT,
            language="en",
        ),
    )

    print(f"Source: {audio.source_uri}")
    print(f"Duration: {audio.duration_seconds / 60:.1f} min")
    print(f"Format: {audio.format} ({audio.sample_rate}Hz, {audio.channels}ch)")
    if audio.transcript:
        print(f"Transcript: {audio.transcript.word_count} words")
    print(f"Content hash: {audio.content_hash[:16]}...")

    print("\n3. Atomic ImageDocument (replaces LocalImage)")
    print("-" * 40)

    image = ImageDocument(
        source_uri="file:///photos/vacation.png",
        format="png",
        width=3840,
        height=2160,
        alt_text="Beach vacation photo",
        caption="A beautiful day at the beach",
        ocr_text=TextDocument(
            source_uri="file:///photos/vacation_ocr.txt",
            content="Vacation 2024",
            format=TextFormat.PLAIN,
        ),
    )

    print(f"Source: {image.source_uri}")
    print(f"Resolution: {image.width}x{image.height}")
    print(f"Format: {image.format}")
    print(f"Alt text: {image.alt_text}")
    print(f"Caption: {image.caption}")
    if image.ocr_text:
        print(f"OCR text: {image.ocr_text.content}")
    print(f"Content hash: {image.content_hash[:16]}...")

    print("\n4. TextDocument with TextFormat enum")
    print("-" * 40)

    text_doc = TextDocument(
        source_uri="https://example.com/article.md",
        content="# Hello World\n\nThis is a **markdown** document.",
        format=TextFormat.MARKDOWN,
        language="en",
    )

    print(f"Format: {text_doc.format}")
    print(f"Char count: {text_doc.char_count}")
    print(f"Word count: {text_doc.word_count}")
    print(f"Content hash: {text_doc.content_hash[:16]}...")

    print("\n5. SpreadsheetDocument")
    print("-" * 40)

    spreadsheet = SpreadsheetDocument(
        source_uri="file:///data/report.xlsx",
        format="xlsx",
        sheet_count=2,
        sheets=[
            SheetData(
                name="Sales",
                headers=["Product", "Q1", "Q2", "Q3", "Q4"],
                rows=[
                    ["Widget A", 100, 150, 200, 175],
                    ["Widget B", 80, 90, 120, 110],
                ],
                row_count=2,
                col_count=5,
            ),
            SheetData(
                name="Summary",
                headers=["Metric", "Value"],
                rows=[
                    ["Total Sales", 2025],
                    ["Top Product", "Widget A"],
                ],
                row_count=2,
                col_count=2,
            ),
        ],
    )

    print(f"Source: {spreadsheet.source_uri}")
    print(f"Format: {spreadsheet.format}")
    print(f"Sheet count: {spreadsheet.sheet_count}")
    for sheet in spreadsheet.sheets:
        print(f"  - {sheet.name}: {sheet.row_count} rows x {sheet.col_count} cols")
    print(f"Content hash: {spreadsheet.content_hash[:16]}...")

    print("\n6. YouTubeVideo with atomics (backward compatibility)")
    print("-" * 40)

    yt_metadata = FetchMetadata(
        source_uri="https://youtube.com/watch?v=dQw4w9WgXcQ",
        fetched_at=datetime.now(),
    )

    yt_video = YouTubeVideo(
        metadata=yt_metadata,
        video_id="dQw4w9WgXcQ",
        title="Never Gonna Give You Up",
        duration_seconds=213.0,
        uploader="RickAstley",
        view_count=1_200_000_000,
        like_count=12_000_000,
        transcript=TextDocument(
            source_uri="https://youtube.com/api/transcript/dQw4w9WgXcQ",
            content="Never gonna give you up...",
            format=TextFormat.TRANSCRIPT,
        ),
        thumbnail=ImageDocument(
            source_uri="https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            format="jpeg",
            width=1280,
            height=720,
        ),
    )

    print(f"Title: {yt_video.title}")
    print(f"Video ID: {yt_video.video_id}")
    print(f"Uploader: {yt_video.uploader}")
    if yt_video.transcript:
        print(f"Transcript: {yt_video.transcript.format}")
    if yt_video.thumbnail:
        print(f"Thumbnail: {yt_video.thumbnail.width}x{yt_video.thumbnail.height}")

    print("\n7. Legacy LocalVideo (backward compatibility)")
    print("-" * 40)

    legacy_video_metadata = FetchMetadata(
        source_uri="file:///home/user/videos/legacy.mp4",
        fetched_at=datetime.now(),
        mime_type="video/mp4",
    )

    legacy_video = LocalVideo(
        metadata=legacy_video_metadata,
        file_path="/home/user/videos/legacy.mp4",
        file_name="legacy.mp4",
        duration_seconds=3600.0,
        width=1920,
        height=1080,
        codec="h264",
    )

    print(f"File: {legacy_video.file_name}")
    print(f"Duration: {legacy_video.duration_seconds / 3600:.1f} hours")
    print("Note: Legacy schemas still work for backward compatibility")

    print("\n" + "=" * 60)
    print("All media types use atomic schemas with auto content_hash!")
    print("TextFormat enum: PLAIN, MARKDOWN, HTML, RST, CODE, TRANSCRIPT")


if __name__ == "__main__":
    asyncio.run(main())
