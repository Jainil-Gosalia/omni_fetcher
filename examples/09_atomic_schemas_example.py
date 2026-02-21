"""Atomic schemas example - demonstrating all 5 atomic document types."""

import asyncio

from omni_fetcher.schemas.atomics import (
    TextDocument,
    AudioDocument,
    ImageDocument,
    VideoDocument,
    SpreadsheetDocument,
    SheetData,
    TextFormat,
)


async def main():
    print("=" * 60)
    print("Atomic Schemas Example")
    print("=" * 60)

    print("\n1. TextDocument - with TextFormat enum")
    print("-" * 40)

    text_plain = TextDocument(
        source_uri="file:///docs/readme.txt",
        content="Hello, this is plain text content.",
        format=TextFormat.PLAIN,
    )
    print(
        f"Plain text: {text_plain.format} | chars: {text_plain.char_count} | hash: {text_plain.content_hash[:16]}..."
    )

    text_markdown = TextDocument(
        source_uri="file:///docs/article.md",
        content="# Title\n\nSome **bold** and *italic* content.",
        format=TextFormat.MARKDOWN,
    )
    print(
        f"Markdown: {text_markdown.format} | words: {text_markdown.word_count} | hash: {text_markdown.content_hash[:16]}..."
    )

    text_html = TextDocument(
        source_uri="file:///docs/page.html",
        content="<html><body><h1>Hello</h1></body></html>",
        format=TextFormat.HTML,
    )
    print(f"HTML: {text_html.format} | chars: {text_html.char_count}")

    text_code = TextDocument(
        source_uri="file:///src/main.py",
        content="def hello():\n    print('Hello World')",
        format=TextFormat.CODE,
        language="python",
    )
    print(f"Code: {text_code.format} | lang: {text_code.language}")

    text_transcript = TextDocument(
        source_uri="file:///media/subs.vtt",
        content="00:00:00 --> Hello world\n00:00:03 --> Welcome to the show",
        format=TextFormat.TRANSCRIPT,
        language="en",
    )
    print(f"Transcript: {text_transcript.format} | lang: {text_transcript.language}")

    print("\n2. AudioDocument - with optional transcript")
    print("-" * 40)

    audio = AudioDocument(
        source_uri="file:///music/podcast.mp3",
        duration_seconds=1800.0,
        format="mp3",
        sample_rate=44100,
        channels=2,
        language="en",
        transcript=TextDocument(
            source_uri="file:///music/podcast_transcript.txt",
            content="Welcome to today's episode where we discuss...",
            format=TextFormat.TRANSCRIPT,
            language="en",
        ),
    )

    print(f"Audio: {audio.format} | {audio.duration_seconds / 60:.0f} min | {audio.sample_rate}Hz")
    print(f"Transcript: {audio.transcript.word_count} words")
    print(f"Content hash (from transcript): {audio.content_hash[:16]}...")

    audio_no_transcript = AudioDocument(
        source_uri="file:///music/song.mp3",
        duration_seconds=180.0,
        format="mp3",
    )
    print(f"Audio without transcript hash: {audio_no_transcript.content_hash[:16]}...")

    print("\n3. ImageDocument - with alt_text, caption, or OCR")
    print("-" * 40)

    image_simple = ImageDocument(
        source_uri="file:///photos/landscape.jpg",
        format="jpeg",
        width=3840,
        height=2160,
        alt_text="Mountain landscape at sunset",
    )
    print(f"Image: {image_simple.width}x{image_simple.height} | alt: {image_simple.alt_text}")
    print(f"Hash (from alt_text): {image_simple.content_hash[:16]}...")

    image_with_caption = ImageDocument(
        source_uri="file:///photos/family.jpg",
        format="png",
        width=1920,
        height=1080,
        caption="Family vacation 2024",
    )
    print(f"Image with caption: {image_with_caption.caption}")
    print(f"Hash (from caption): {image_with_caption.content_hash[:16]}...")

    image_with_ocr = ImageDocument(
        source_uri="file:///docs/screenshot.png",
        format="png",
        width=1280,
        height=720,
        ocr_text=TextDocument(
            source_uri="file:///docs/screenshot_ocr.txt",
            content="Important Document\nGenerated: 2024-01-15",
            format=TextFormat.PLAIN,
        ),
    )
    print(f"Image with OCR: {image_with_ocr.ocr_text.content[:30]}...")
    print(f"Hash (from OCR): {image_with_ocr.content_hash[:16]}...")

    print("\n4. VideoDocument - composing Audio + Image + Text")
    print("-" * 40)

    video = VideoDocument(
        source_uri="file:///videos/presentation.mp4",
        duration_seconds=3600.0,
        format="mp4",
        resolution=(1920, 1080),
        fps=30.0,
        thumbnail=ImageDocument(
            source_uri="file:///videos/presentation_thumb.jpg",
            format="jpeg",
            width=320,
            height=180,
            caption="Presentation thumbnail",
        ),
        audio=AudioDocument(
            source_uri="file:///videos/presentation.mp3",
            duration_seconds=3600.0,
            format="mp3",
            sample_rate=48000,
            channels=2,
            transcript=TextDocument(
                source_uri="file:///videos/presentation.txt",
                content="Today we'll cover the following topics...",
                format=TextFormat.TRANSCRIPT,
            ),
        ),
        captions=TextDocument(
            source_uri="file:///videos/presentation.vtt",
            content="00:00:00 --> Introduction\n00:01:30 --> Main Topic",
            format=TextFormat.TRANSCRIPT,
        ),
    )

    print(f"Video: {video.format} | {video.duration_seconds / 60:.0f} min | {video.resolution}")
    print(f"Thumbnail: {video.thumbnail.width}x{video.thumbnail.height} ({video.thumbnail.format})")
    print(
        f"Audio: {video.audio.format} | {video.audio.sample_rate}Hz | transcript: {video.audio.transcript.word_count} words"
    )
    print(f"Captions: {video.captions.format}")
    print(f"Hash (from captions): {video.content_hash[:16]}...")

    print("\n5. SpreadsheetDocument - multiple sheets")
    print("-" * 40)

    spreadsheet = SpreadsheetDocument(
        source_uri="file:///data/quarterly_report.xlsx",
        format="xlsx",
        sheet_count=3,
        sheets=[
            SheetData(
                name="Q1 Sales",
                headers=["Product", "Revenue", "Units"],
                rows=[
                    ["Widget A", 50000, 1000],
                    ["Widget B", 35000, 700],
                ],
                row_count=2,
                col_count=3,
            ),
            SheetData(
                name="Q2 Sales",
                headers=["Product", "Revenue", "Units"],
                rows=[
                    ["Widget A", 65000, 1300],
                    ["Widget B", 42000, 840],
                ],
                row_count=2,
                col_count=3,
            ),
            SheetData(
                name="Summary",
                headers=["Metric", "Q1", "Q2"],
                rows=[
                    ["Total Revenue", 85000, 107000],
                    ["Total Units", 1700, 2140],
                ],
                row_count=2,
                col_count=3,
            ),
        ],
    )

    print(f"Spreadsheet: {spreadsheet.format} | {spreadsheet.sheet_count} sheets")
    for sheet in spreadsheet.sheets:
        print(f"  - {sheet.name}: {sheet.row_count} rows, {sheet.col_count} cols")
        if sheet.headers:
            print(f"    Headers: {sheet.headers}")
    print(f"Hash: {spreadsheet.content_hash[:16]}...")

    print("\n6. Content hash auto-computation")
    print("-" * 40)

    doc1 = TextDocument(
        source_uri="test://doc1",
        content="Same content",
        format=TextFormat.PLAIN,
    )
    doc2 = TextDocument(
        source_uri="test://doc2",
        content="Same content",
        format=TextFormat.PLAIN,
    )
    doc3 = TextDocument(
        source_uri="test://doc3",
        content="Different content",
        format=TextFormat.PLAIN,
    )

    print(f"Doc 1 hash: {doc1.content_hash[:16]}...")
    print(f"Doc 2 hash: {doc1.content_hash[:16]} (same content = same hash)")
    print(f"Doc 3 hash: {doc3.content_hash[:16]} (different content = different hash)")

    print("\n7. Composing VideoDocument from separate components")
    print("-" * 40)

    thumbnail = ImageDocument(
        source_uri="https://example.com/thumb.jpg",
        format="jpeg",
        width=640,
        height=360,
        alt_text="Video thumbnail",
    )

    audio_track = AudioDocument(
        source_uri="https://example.com/audio.m4a",
        duration_seconds=600.0,
        format="aac",
        sample_rate=44100,
        channels=2,
    )

    subtitles = TextDocument(
        source_uri="https://example.com/subs.vtt",
        content="1\n00:00:00,000 --> 00:00:05,000\nHello, welcome to the video!",
        format=TextFormat.TRANSCRIPT,
    )

    composed_video = VideoDocument(
        source_uri="https://example.com/video.mp4",
        duration_seconds=600.0,
        format="mp4",
        resolution=(1920, 1080),
        fps=30.0,
        thumbnail=thumbnail,
        audio=audio_track,
        captions=subtitles,
    )

    print(f"Composed video from separate atomics:")
    print(f"  - Source: {composed_video.source_uri}")
    print(f"  - Thumbnail: {composed_video.thumbnail.source_uri}")
    print(
        f"  - Audio: {composed_video.audio.source_uri} ({composed_video.audio.duration_seconds}s)"
    )
    print(f"  - Captions: {composed_video.captions.source_uri}")
    print(f"  - Hash: {composed_video.content_hash[:16]}...")

    print("\n" + "=" * 60)
    print("Summary: 5 Atomic Schemas")
    print("=" * 60)
    print("1. TextDocument - text content with format enum")
    print("2. AudioDocument - audio with optional transcript")
    print("3. ImageDocument - images with alt_text/caption/OCR")
    print("4. VideoDocument - composes Audio + Image + Text")
    print("5. SpreadsheetDocument - multiple sheets with rows")
    print("\nKey features:")
    print("- content_hash auto-computed from primary content")
    print("- TextFormat enum: PLAIN, MARKDOWN, HTML, RST, CODE, TRANSCRIPT")
    print("- Nested atomics enable rich document composition")


if __name__ == "__main__":
    asyncio.run(main())
