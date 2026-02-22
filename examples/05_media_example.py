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
from omni_fetcher.schemas.media import YouTubeVideo, LocalVideo


async def main():
    print("=" * 60)
    print("Media Handling Examples - Using Atomic Schemas")
    print("=" * 60)

    print("\n1. VideoDocument with file metadata")
    print("-" * 40)

    video = VideoDocument(
        source_uri="file:///home/user/videos/movie.mp4",
        duration_seconds=7200.0,
        format="mp4",
        resolution=(1920, 1080),
        fps=30.0,
        file_name="movie.mp4",
        file_size_bytes=1_500_000_000,
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
    if video.file_name and video.file_size_bytes:
        print(f"File: {video.file_name} ({video.file_size_bytes / 1_000_000_000:.2f} GB)")
    print(f"Duration: {video.duration_seconds / 3600:.1f} hours")
    print(f"Resolution: {video.resolution}")
    if video.thumbnail:
        print(
            f"Thumbnail: {video.thumbnail.width}x{video.thumbnail.height} ({video.thumbnail.format})"
        )
    print(f"Content hash: {video.content_hash[:16]}...")

    print("\n2. AudioDocument with music metadata")
    print("-" * 40)

    audio = AudioDocument(
        source_uri="file:///music/song.flac",
        duration_seconds=245.0,
        format="flac",
        sample_rate=44100,
        channels=2,
        language="en",
        file_name="song.flac",
        file_size_bytes=35_000_000,
        artist="The Artists",
        album="Great Album",
        genre="Rock",
        transcript=TextDocument(
            source_uri="file:///music/song.txt",
            content="Verse 1: This is the song lyrics...",
            format=TextFormat.TRANSCRIPT,
            language="en",
        ),
    )

    print(f"Source: {audio.source_uri}")
    if audio.file_size_bytes:
        print(f"File: {audio.file_name} ({audio.file_size_bytes / 1_000_000:.1f} MB)")
    print(f"Duration: {audio.duration_seconds / 60:.1f} min")
    print(f"Format: {audio.format} ({audio.sample_rate}Hz, {audio.channels}ch)")
    print(f"Artist: {audio.artist}, Album: {audio.album}, Genre: {audio.genre}")
    if audio.transcript:
        print(f"Transcript: {audio.transcript.word_count} words")
    print(f"Content hash: {audio.content_hash[:16]}...")

    print("\n3. ImageDocument with EXIF and web metadata")
    print("-" * 40)

    image = ImageDocument(
        source_uri="file:///photos/vacation.png",
        format="png",
        width=3840,
        height=2160,
        alt_text="Beach vacation photo",
        caption="A beautiful day at the beach",
        file_name="vacation.png",
        file_size_bytes=4_500_000,
        camera_make="Canon",
        camera_model="EOS R5",
        gps_latitude=25.7617,
        gps_longitude=-80.1918,
        page_url="https://example.com/photos/vacation",
        ocr_text=TextDocument(
            source_uri="file:///photos/vacation_ocr.txt",
            content="Vacation 2024",
            format=TextFormat.PLAIN,
        ),
    )

    print(f"Source: {image.source_uri}")
    if image.file_name and image.file_size_bytes:
        print(f"File: {image.file_name} ({image.file_size_bytes / 1_000_000:.1f} MB)")
    print(f"Resolution: {image.width}x{image.height}")
    print(f"Format: {image.format}")
    print(f"Camera: {image.camera_make} {image.camera_model}")
    print(f"GPS: ({image.gps_latitude}, {image.gps_longitude})")
    print(f"Page URL: {image.page_url}")
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

    print("\n6. YouTubeVideo composite")
    print("-" * 40)

    yt_video = YouTubeVideo(
        video_id="dQw4w9WgXcQ",
        title="Never Gonna Give You Up",
        duration_seconds=213.0,
        uploader="RickAstley",
        uploader_url="https://www.youtube.com/@RickAstley",
        upload_date=datetime(2009, 10, 25),
        view_count=1_200_000_000,
        like_count=12_000_000,
        dislike_count=50000,
        comment_count=100_000,
        width=1920,
        height=1080,
        codec="h264",
        bitrate=2000000,
        frame_rate=30.0,
        aspect_ratio="16:9",
        tags=["music", "pop", "80s"],
        video_category="Music",
        license="youtube",
        is_live=False,
        is_private=False,
        is_embedded=True,
        captions_available=True,
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
    print(f"Views: {yt_video.view_count:,}")
    print(f"Duration: {yt_video.duration_seconds}s")
    if yt_video.transcript:
        print(f"Transcript: {yt_video.transcript.format}")
    if yt_video.thumbnail:
        print(f"Thumbnail: {yt_video.thumbnail.width}x{yt_video.thumbnail.height}")

    print("\n7. LocalVideo composite")
    print("-" * 40)

    local_video = LocalVideo(
        file_path="/home/user/videos/party.mp4",
        file_name="party.mp4",
        duration_seconds=3600.0,
        width=1920,
        height=1080,
        codec="h264",
        frame_rate=30.0,
        bitrate=5000000,
        aspect_ratio="16:9",
        created_at=datetime(2024, 1, 15, 10, 30, 0),
        modified_at=datetime(2024, 1, 15, 14, 45, 0),
    )

    print(f"File: {local_video.file_name}")
    print(f"Path: {local_video.file_path}")
    if local_video.duration_seconds:
        print(f"Duration: {local_video.duration_seconds / 3600:.1f} hours")
    print(f"Resolution: {local_video.width}x{local_video.height}")
    print(f"Codec: {local_video.codec}")
    if local_video.frame_rate:
        print(f"Frame rate: {local_video.frame_rate} fps")

    print("\n" + "=" * 60)
    print("All media types use atomic schemas with auto content_hash!")
    print("TextFormat enum: PLAIN, MARKDOWN, HTML, RST, CODE, TRANSCRIPT")


if __name__ == "__main__":
    asyncio.run(main())
