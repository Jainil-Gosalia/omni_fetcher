"""Audio fetcher for OmniFetcher."""

from __future__ import annotations

import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.atomics import AudioDocument


@source(
    name="audio",
    uri_patterns=["*.mp3", "*.wav", "*.flac", "*.ogg", "*.m4a", "*.aac", "*.wma"],
    mime_types=["audio/*"],
    priority=15,
    description="Fetch and parse audio files",
)
class AudioFetcher(BaseFetcher):
    """Fetcher for audio files (local and remote)."""

    name = "audio"
    priority = 15

    def __init__(self, timeout: float = 30.0):
        super().__init__()
        self.timeout = timeout
        mimetypes.init()

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this is an audio file URI."""
        if uri.startswith("s3://"):
            return False

        uri_lower = uri.lower()
        audio_extensions = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma")

        if uri.startswith("http://") or uri.startswith("https://"):
            return uri_lower.endswith(audio_extensions)
        if uri.startswith("file://"):
            path = uri[7:]
            if path.startswith("/"):
                path = path[1:]
            return Path(path).suffix.lower() in audio_extensions
        # For absolute paths, check extension
        return Path(uri).suffix.lower() in audio_extensions

    async def fetch(self, uri: str, **kwargs: Any) -> AudioDocument:
        """Fetch an audio file.

        Args:
            uri: Audio file path or URL

        Returns:
            AudioDocument with audio metadata
        """
        if uri.startswith("http://") or uri.startswith("https://"):
            return await self._fetch_remote(uri)
        return await self._fetch_local(uri)

    async def _fetch_local(self, uri: str) -> AudioDocument:
        """Fetch local audio file."""
        path = self._parse_path(uri)
        path_obj = Path(path).resolve()

        if not path_obj.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        if not path_obj.is_file():
            raise ValueError(f"Not a file: {path}")

        stat = path_obj.stat()
        file_size = stat.st_size
        mime_type = self._guess_mime_type(path)
        audio_format = mime_type.split("/")[-1] if mime_type else "unknown"

        tags = self._build_tags(file_size)

        return AudioDocument(
            source_uri=uri,
            duration_seconds=0.0,
            format=audio_format,
            file_name=path_obj.name,
            file_size_bytes=file_size,
            tags=tags,
        )

    async def _fetch_remote(self, uri: str) -> AudioDocument:
        """Fetch remote audio file."""
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(uri)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "audio/mpeg")
            mime_type = content_type.split(";")[0].strip()
            audio_format = mime_type.split("/")[-1] if "/" in mime_type else "mp3"

            file_size = len(response.content)
            tags = self._build_tags(file_size)

            content_disposition = response.headers.get("content-disposition", "")
            file_name = self._extract_filename(uri, content_disposition)

            return AudioDocument(
                source_uri=uri,
                duration_seconds=0.0,
                format=audio_format,
                file_name=file_name,
                file_size_bytes=file_size,
                tags=tags,
            )

    def _parse_path(self, uri: str) -> str:
        """Convert file:// URI to path."""
        if uri.startswith("file://"):
            path = uri[7:]
            if path.startswith("/") and os.name != "nt":
                pass
            elif os.name == "nt" and len(path) > 1 and path[1] == ":":
                pass
            else:
                path = uri.replace("file://localhost/", "").replace("file:///", "")
        else:
            path = uri
        return path

    def _guess_mime_type(self, path: str) -> Optional[str]:
        """Guess MIME type from file extension."""
        mime_type, _ = mimetypes.guess_type(path)
        return mime_type

    def _extract_filename(self, uri: str, content_disposition: str) -> str:
        """Extract filename from URL or Content-Disposition header."""
        if "filename=" in content_disposition:
            import re

            match = re.search(r'filename="?([^";\n]+)"?', content_disposition)
            if match:
                return match.group(1)
        return Path(uri).name or "audio"

    def _build_tags(self, file_size: int) -> list[str]:
        """Build tags for audio file."""
        tags = ["audio", "local" if file_size < 100_000_000 else "remote"]
        if file_size > 50 * 1024 * 1024:
            tags.append("large_file")
        return tags
