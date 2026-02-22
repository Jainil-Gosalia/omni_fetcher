"""YouTube fetcher for OmniFetcher."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

import yt_dlp

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata
from omni_fetcher.schemas.media import YouTubeVideo
from omni_fetcher.schemas.atomics import (
    TextDocument,
    ImageDocument,
    AudioDocument,
    TextFormat,
)
from omni_fetcher.auth import AuthConfig


@source(
    name="youtube",
    uri_patterns=["youtube.com", "youtu.be", "ytb:"],
    mime_types=["video/*"],
    priority=5,
    description="Fetch video metadata from YouTube",
)
class YouTubeFetcher(BaseFetcher):
    """Fetcher for YouTube videos and playlists."""

    name = "youtube"
    priority = 5

    YOUTUBE_DOMAINS = [
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be",
    ]

    def __init__(self, api_key: Optional[str] = None):
        super().__init__()
        self.api_key = api_key
        self._ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this is a YouTube URL."""
        uri_lower = uri.lower()
        return any(domain in uri_lower for domain in cls.YOUTUBE_DOMAINS)

    async def fetch(self, uri: str, **kwargs: Any) -> YouTubeVideo:
        """Fetch video information from YouTube.

        Args:
            uri: YouTube URL

        Returns:
            YouTubeVideo with video metadata
        """
        video_id = self._extract_video_id(uri)

        if not video_id:
            raise ValueError(f"Could not extract video ID from: {uri}")

        video_info = await self._get_video_info(video_id)

        # Extract video ID from the info (might differ from requested)
        actual_id = video_info.get("id", video_id)

        metadata = FetchMetadata(
            source_uri=uri,
            fetched_at=datetime.now(),
            source_name=self.name,
            mime_type="video/mp4",
        )

        # Parse duration
        duration = video_info.get("duration")
        duration_seconds = duration if isinstance(duration, (int, float)) else None

        # Parse upload date
        upload_date = video_info.get("upload_date")
        if upload_date:
            try:
                upload_date = datetime.strptime(upload_date, "%Y%m%d")
            except ValueError:
                upload_date = None

        # Parse view count and like count
        view_count = video_info.get("view_count")
        like_count = video_info.get("like_count")

        # Extract tags
        tags = video_info.get("tags", [])

        # Extract categories
        categories = video_info.get("categories", [])
        category = categories[0] if categories else None

        # Check if live
        is_live = video_info.get("is_live", False) or video_info.get("live_status") == "is_live"

        # Get thumbnail
        thumbnails = video_info.get("thumbnail", "")
        thumbnail_url = (
            video_info.get("thumbnails", [{}])[0].get("url")
            if video_info.get("thumbnails")
            else thumbnails
        )

        return YouTubeVideo(
            metadata=metadata,
            video_id=actual_id,
            title=video_info.get("title", "Unknown"),
            text=TextDocument(
                source_uri=uri,
                content=video_info.get("description", ""),
                format=TextFormat.PLAIN,
            )
            if video_info.get("description")
            else None,
            duration_seconds=duration_seconds,
            width=video_info.get("width"),
            height=video_info.get("height"),
            uploader=video_info.get("uploader", ""),
            uploader_url=video_info.get("uploader_url", ""),
            upload_date=upload_date,
            view_count=view_count,
            like_count=like_count,
            tags=tags if tags else None,
            video_category=category,
            license=video_info.get("license"),
            is_live=is_live,
            is_private=video_info.get("private", False),
            thumbnail=ImageDocument(
                source_uri=thumbnail_url,
                format="jpeg",
            )
            if thumbnail_url
            else None,
        )

    def _extract_video_id(self, uri: str) -> Optional[str]:
        """Extract video ID from various YouTube URL formats."""
        # youtube.com/watch?v=VIDEO_ID
        match = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", uri)
        if match:
            return match.group(1)

        # youtu.be/VIDEO_ID
        match = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", uri)
        if match:
            return match.group(1)

        # youtube.com/shorts/VIDEO_ID
        match = re.search(r"youtube\.com/shorts/([a-zA-Z0-9_-]+)", uri)
        if match:
            return match.group(1)

        # youtube.com/embed/VIDEO_ID
        match = re.search(r"youtube\.com/embed/([a-zA-Z0-9_-]+)", uri)
        if match:
            return match.group(1)

        # youtube.com/v/VIDEO_ID
        match = re.search(r"youtube\.com/v/([a-zA-Z0-9_-]+)", uri)
        if match:
            return match.group(1)

        # Handle youtu.be with additional params
        match = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", uri)
        if match:
            return match.group(1)

        return None

    async def _get_video_info(self, video_id: str) -> dict[str, Any]:
        """Get video information using yt-dlp.

        This runs in a thread to avoid blocking the event loop.
        """

        def _fetch():
            ydl = yt_dlp.YoutubeDL(self._ydl_opts)
            info = ydl.extract_info(f"https://youtube.com/watch?v={video_id}", download=False)
            return info

        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)

    async def fetch_metadata(self, uri: str) -> dict[str, Any]:
        """Fetch only metadata (faster, less data)."""
        video_id = self._extract_video_id(uri)

        if not video_id:
            raise ValueError(f"Could not extract video ID from: {uri}")

        def _fetch_minimal():
            ydl = yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True})
            info = ydl.extract_info(f"https://youtube.com/watch?v={video_id}", download=False)
            return info

        import asyncio

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, _fetch_minimal)

        return {
            "video_id": video_id,
            "title": info.get("title"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "thumbnail": info.get("thumbnail"),
        }
