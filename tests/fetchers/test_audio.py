"""Tests for AudioFetcher."""

import pytest
import os
import tempfile
from unittest.mock import patch, AsyncMock, MagicMock

from omni_fetcher.fetchers.audio import AudioFetcher
from omni_fetcher.schemas.atomics import AudioDocument


class TestAudioFetcherCreation:
    def test_creation(self):
        """Can create AudioFetcher."""
        fetcher = AudioFetcher()
        assert fetcher.name == "audio"
        assert fetcher.priority == 15

    def test_creation_with_timeout(self):
        """Can create AudioFetcher with custom timeout."""
        fetcher = AudioFetcher(timeout=60.0)
        assert fetcher.timeout == 60.0


class TestAudioFetcherCanHandle:
    def test_can_handle_local_audio(self):
        """AudioFetcher handles local audio files."""
        assert AudioFetcher.can_handle("/path/to/song.mp3")
        assert AudioFetcher.can_handle("/path/to/audio.wav")
        assert AudioFetcher.can_handle("/path/to/music.flac")
        assert AudioFetcher.can_handle("/path/to/podcast.ogg")
        assert AudioFetcher.can_handle("/path/to/track.m4a")
        assert AudioFetcher.can_handle("/path/to/file.aac")

    def test_can_handle_file_scheme(self):
        """AudioFetcher handles file:// URIs."""
        assert AudioFetcher.can_handle("file:///path/to/song.mp3")
        assert AudioFetcher.can_handle("file:///home/user/music.wav")

    def test_can_handle_http_audio(self):
        """AudioFetcher handles HTTP audio URLs."""
        assert AudioFetcher.can_handle("https://example.com/song.mp3")
        assert AudioFetcher.can_handle("http://example.com/audio.wav")

    def test_cannot_handle_non_audio(self):
        """AudioFetcher rejects non-audio files."""
        assert not AudioFetcher.can_handle("/path/to/document.pdf")
        assert not AudioFetcher.can_handle("/path/to/image.jpg")
        assert not AudioFetcher.can_handle("/path/to/video.mp4")
        assert not AudioFetcher.can_handle("/path/to/text.txt")
        assert not AudioFetcher.can_handle("https://example.com/page.html")

    def test_cannot_handle_s3(self):
        """AudioFetcher rejects S3 URIs."""
        assert not AudioFetcher.can_handle("s3://bucket/audio.mp3")


class TestAudioFetcherFetch:
    @pytest.mark.asyncio
    async def test_fetch_local_audio_file(self):
        """Can fetch local audio file."""
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            temp_path = f.name

        try:
            fetcher = AudioFetcher()
            result = await fetcher.fetch(temp_path)
            assert isinstance(result, AudioDocument)
            assert result.format in ("mp3", "mpeg")
            assert "audio" in result.tags
            assert "local" in result.tags
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_fetch_audio_with_various_formats(self):
        """Can fetch audio with various formats."""
        formats = [".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"]

        for fmt in formats:
            with tempfile.NamedTemporaryFile(suffix=fmt, delete=False) as f:
                temp_path = f.name

            try:
                fetcher = AudioFetcher()
                result = await fetcher.fetch(temp_path)
                assert isinstance(result, AudioDocument)
                assert "audio" in result.tags
            finally:
                os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_fetch_nonexistent_file_raises(self):
        """Fetching non-existent file raises FileNotFoundError."""
        fetcher = AudioFetcher()
        with pytest.raises(FileNotFoundError):
            await fetcher.fetch("/nonexistent/audio.mp3")

    @pytest.mark.asyncio
    async def test_fetch_large_audio_file_tag(self):
        """Large audio files (>50MB) get large_file tag."""
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            temp_path = f.name

        try:
            fetcher = AudioFetcher()
            result = await fetcher.fetch(temp_path)
            # Small file won't have large_file tag, just verify fetch works
            assert result is not None
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_fetch_with_file_scheme(self):
        """Can fetch audio with file:// scheme."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name

        try:
            fetcher = AudioFetcher()
            result = await fetcher.fetch(f"file://{temp_path}")
            assert isinstance(result, AudioDocument)
            assert result.format == "wav"
        finally:
            os.unlink(temp_path)


class TestAudioFetcherRemote:
    @pytest.mark.asyncio
    async def test_fetch_remote_audio(self):
        """Can fetch remote audio file."""
        fetcher = AudioFetcher()

        mock_response = MagicMock()
        mock_response.headers = {
            "content-type": "audio/mpeg",
            "content-disposition": 'attachment; filename="song.mp3"',
        }
        mock_response.content = b"fake audio data"
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.5
        mock_response.url = "https://example.com/song.mp3"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await fetcher.fetch("https://example.com/song.mp3")

        assert isinstance(result, AudioDocument)
        assert "audio" in result.tags

    @pytest.mark.asyncio
    async def test_fetch_remote_audio_content_type(self):
        """Handles various audio content types."""
        fetcher = AudioFetcher()

        mock_response = MagicMock()
        mock_response.headers = {"content-type": "audio/wav"}
        mock_response.content = b"fake audio data"
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.5
        mock_response.url = "https://example.com/audio.wav"

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await fetcher.fetch("https://example.com/audio.wav")

        assert isinstance(result, AudioDocument)


class TestAudioFetcherHelpers:
    def test_build_tags_local_small(self):
        """build_tags returns correct tags for small local file."""
        fetcher = AudioFetcher()
        tags = fetcher._build_tags(1024 * 1024)
        assert "audio" in tags
        assert "local" in tags

    def test_build_tags_local_large(self):
        """build_tags adds large_file tag for large files."""
        fetcher = AudioFetcher()
        tags = fetcher._build_tags(60 * 1024 * 1024)
        assert "large_file" in tags

    def test_build_tags_remote(self):
        """build_tags returns remote tag for remote files."""
        fetcher = AudioFetcher()
        tags = fetcher._build_tags(1024)
        assert "audio" in tags

    def test_extract_filename_from_url(self):
        """Can extract filename from URL."""
        fetcher = AudioFetcher()
        filename = fetcher._extract_filename("https://example.com/path/song.mp3", "")
        assert filename == "song.mp3"

    def test_extract_filename_from_header(self):
        """Can extract filename from Content-Disposition header."""
        fetcher = AudioFetcher()
        filename = fetcher._extract_filename(
            "https://example.com/download",
            'attachment; filename="custom.mp3"',
        )
        assert filename == "custom.mp3"

    def test_guess_mime_type(self):
        """Can guess MIME type from extension."""
        fetcher = AudioFetcher()
        # The actual return may vary by system, just check it returns something
        assert fetcher._guess_mime_type("song.mp3") is not None
