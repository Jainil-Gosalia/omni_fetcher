"""Audio and container examples for OmniFetcher v0.5.0."""

import asyncio
import os

from omni_fetcher import OmniFetcher
from omni_fetcher.schemas.containers import RSSFeed, YouTubePlaylist, S3Bucket


async def main():
    print("=" * 60)
    print("OmniFetcher v0.5.0 - Audio & Container Examples")
    print("=" * 60)

    fetcher = OmniFetcher()

    print("\n" + "=" * 60)
    print("F3: Local Audio with Metadata Extraction")
    print("=" * 60)

    audio_file = "path/to/your/audio/file.mp3"
    if os.path.exists(audio_file):
        print("\n1. Fetch local audio file with metadata")
        print("-" * 40)

        result = await fetcher.fetch(audio_file)
        print(f"Source: {result.source_uri}")
        print(f"Format: {result.format}")
        print(f"Duration: {result.duration_seconds:.1f}s")
        if result.sample_rate:
            print(f"Sample Rate: {result.sample_rate} Hz")
        if result.channels:
            print(f"Channels: {result.channels}")
        if result.file_name:
            print(f"File: {result.file_name}")
        if result.file_size_bytes:
            print(f"Size: {result.file_size_bytes / 1_000_000:.1f} MB")
        if result.artist:
            print(f"Artist: {result.artist}")
        if result.album:
            print(f"Album: {result.album}")
        if result.title:
            print(f"Title: {result.title}")
        if result.year:
            print(f"Year: {result.year}")
        if result.genre:
            print(f"Genre: {result.genre}")
        if result.transcript:
            print(f"Transcript: {result.transcript.content[:100]}...")

        print("\n2. Fetch with Whisper transcription")
        print("-" * 40)
        print("Note: Requires openai-whisper installed (pip install openai-whisper)")

        result = await fetcher.fetch(audio_file, transcribe=True, whisper_model="base")
        if result.transcript:
            print(f"Transcript: {result.transcript.content[:200]}...")
            print(f"Language: {result.transcript.language}")
    else:
        print(f"\nAudio file not found: {audio_file}")
        print("Skipping audio examples (no test file)")

    print("\n" + "=" * 60)
    print("F4: Container Schemas")
    print("=" * 60)

    print("\n1. RSS Feed Container")
    print("-" * 40)
    print("Note: Requires network access")

    try:
        feed = await fetcher.fetch("https://blog.python.org/feeds/posts/default")
        if isinstance(feed, RSSFeed):
            print(f"Title: {feed.title}")
            print(f"Description: {feed.description}")
            print(f"Item count: {feed.item_count}")
            print(f"Fetched fully: {feed.fetched_fully}")
            print("Items:")
            for i, item in enumerate(feed.items[:3]):
                print(f"  [{i + 1}] {item.title}")
                if item.link:
                    print(f"      Link: {item.link[:60]}...")
                if item.author:
                    print(f"      Author: {item.author}")
    except Exception as e:
        print(f"RSS feed fetch failed: {e}")

    print("\n2. YouTube Playlist Container")
    print("-" * 40)
    print("Note: Requires valid playlist URL and network access")

    playlist_url = "https://www.youtube.com/playlist?list=PL2rA9A4JnR5uGqkE0U0Z"
    try:
        playlist = await fetcher.fetch(playlist_url, max_items=5)
        if isinstance(playlist, YouTubePlaylist):
            print(f"Title: {playlist.title}")
            print(
                f"Description: {playlist.description[:100] if playlist.description else 'N/A'}..."
            )
            print(f"Channel: {playlist.uploader}")
            print(f"Total items: {playlist.item_count_total}")
            print(f"Fetched items: {playlist.item_count}")
            print("Videos:")
            for i, video in enumerate(playlist.items[:3]):
                print(f"  [{i + 1}] {video.title}")
                print(f"      Duration: {video.duration_seconds}s")
                print(
                    f"      Views: {video.view_count:,}" if video.view_count else "      Views: N/A"
                )
    except Exception as e:
        print(f"YouTube playlist fetch failed: {e}")

    print("\n3. S3 Bucket Container")
    print("-" * 40)
    print("Note: Requires valid S3 URI and AWS credentials")

    s3_uri = "s3://your-bucket-name/"
    try:
        bucket = await fetcher.fetch(s3_uri, max_keys=10)
        if isinstance(bucket, S3Bucket):
            print(f"Bucket: {bucket.bucket_name}")
            if bucket.prefix:
                print(f"Prefix: {bucket.prefix}")
            print(f"Item count: {bucket.item_count}")
            print(f"Fetched fully: {bucket.fetched_fully}")
            print("Objects:")
            for obj in bucket.items[:5]:
                print(f"  - {obj.key}")
                if obj.size:
                    print(f"    Size: {obj.size:,} bytes")
    except Exception as e:
        print(f"S3 bucket fetch failed: {e}")
        print("  (This is expected without valid AWS credentials)")

    print("\n" + "=" * 60)
    print("Container Features:")
    print("  - item_count: Total number of items")
    print("  - fetched_fully: Whether all items were fetched")
    print("  - next_page_token: For pagination support")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
