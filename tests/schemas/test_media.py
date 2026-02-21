"""Tests for media Pydantic models."""

import pytest
from datetime import datetime
from typing import Optional

from omni_fetcher.schemas.media import (
    BaseMedia,
    Video,
    Audio,
    Image,
    YouTubeVideo,
    LocalVideo,
    StreamAudio,
    LocalAudio,
    WebImage,
    LocalImage,
)
from omni_fetcher.schemas.base import FetchMetadata, MediaType, DataCategory


class TestBaseMedia:
    def test_base_media_creation(self):
        metadata = FetchMetadata(
            source_uri="https://example.com/media.mp4",
            fetched_at=datetime.now(),
            mime_type="video/mp4",
        )
        media = BaseMedia(
            metadata=metadata,
            category=DataCategory.MEDIA,
            media_type=MediaType.VIDEO_MP4,
            duration_seconds=120.5,
            file_size=104857600,
        )
        assert media.duration_seconds == 120.5

    def test_base_media_optional_fields(self):
        metadata = FetchMetadata(
            source_uri="test://test",
            fetched_at=datetime.now(),
        )
        media = BaseMedia(
            metadata=metadata,
            category=DataCategory.MEDIA,
            media_type=MediaType.VIDEO_MP4,
        )
        assert media.duration_seconds is None


class TestVideo:
    def test_video_creation(self):
        metadata = FetchMetadata(
            source_uri="https://example.com/video.mp4",
            fetched_at=datetime.now(),
            mime_type="video/mp4",
        )
        video = Video(
            metadata=metadata,
            duration_seconds=3600.0,
            width=1920,
            height=1080,
            codec="h264",
        )
        assert video.width == 1920
        assert video.height == 1080


class TestYouTubeVideo:
    def test_youtube_video_creation(self):
        metadata = FetchMetadata(
            source_uri="https://youtube.com/watch?v=abc123",
            fetched_at=datetime.now(),
        )
        yt_video = YouTubeVideo(
            metadata=metadata,
            video_id="abc123",
            title="Test Video",
            description="A test video",
            duration_seconds=600.0,
            width=1920,
            height=1080,
            uploader="TestUploader",
            view_count=1000000,
        )
        assert yt_video.video_id == "abc123"
        assert yt_video.title == "Test Video"

    def test_youtube_video_optional_fields(self):
        metadata = FetchMetadata(
            source_uri="https://youtu.be/abc123",
            fetched_at=datetime.now(),
        )
        yt_video = YouTubeVideo(
            metadata=metadata,
            video_id="abc123",
            title="Minimal Video",
        )
        assert yt_video.text is None


class TestLocalVideo:
    def test_local_video_creation(self):
        metadata = FetchMetadata(
            source_uri="file:///home/user/videos/movie.mp4",
            fetched_at=datetime.now(),
            mime_type="video/mp4",
            file_size=1500000000,
        )
        local_video = LocalVideo(
            metadata=metadata,
            file_path="/home/user/videos/movie.mp4",
            file_name="movie.mp4",
            duration_seconds=7200.0,
            width=1920,
            height=1080,
            codec="h264",
        )
        assert local_video.file_path == "/home/user/videos/movie.mp4"


class TestAudio:
    def test_audio_creation(self):
        metadata = FetchMetadata(
            source_uri="https://example.com/audio.mp3",
            fetched_at=datetime.now(),
            mime_type="audio/mp3",
        )
        audio = Audio(
            metadata=metadata,
            duration_seconds=180.0,
            bitrate=320000,
            sample_rate=44100,
            channels=2,
            codec="mp3",
        )
        assert audio.duration_seconds == 180.0
        assert audio.bitrate == 320000


class TestLocalAudio:
    def test_local_audio_creation(self):
        metadata = FetchMetadata(
            source_uri="file:///music/song.flac",
            fetched_at=datetime.now(),
            mime_type="audio/flac",
            file_size=35000000,
        )
        local_audio = LocalAudio(
            metadata=metadata,
            file_path="/music/song.flac",
            file_name="song.flac",
            duration_seconds=240.0,
            artist="Test Artist",
            album="Test Album",
            title="Test Song",
        )
        assert local_audio.artist == "Test Artist"


class TestImage:
    def test_image_creation(self):
        metadata = FetchMetadata(
            source_uri="https://example.com/image.jpg",
            fetched_at=datetime.now(),
            mime_type="image/jpeg",
        )
        image = Image(
            metadata=metadata,
            width=1920,
            height=1080,
            format="JPEG",
        )
        assert image.width == 1920
        assert image.height == 1080


class TestWebImage:
    def test_web_image_creation(self):
        metadata = FetchMetadata(
            source_uri="https://example.com/photo.jpg",
            fetched_at=datetime.now(),
        )
        web_image = WebImage(
            metadata=metadata,
            width=1920,
            height=1080,
            format="JPEG",
            alt_text="A beautiful sunset",
            source_url="https://example.com/photo.jpg",
        )
        assert web_image.alt_text == "A beautiful sunset"


class TestLocalImage:
    def test_local_image_creation(self):
        metadata = FetchMetadata(
            source_uri="file:///photos/vacation.png",
            fetched_at=datetime.now(),
            mime_type="image/png",
        )
        local_image = LocalImage(
            metadata=metadata,
            file_path="/photos/vacation.png",
            file_name="vacation.png",
            width=3840,
            height=2160,
            format="PNG",
        )
        assert local_image.file_path == "/photos/vacation.png"


class TestVideoInheritance:
    def test_youtube_video_is_video(self):
        assert issubclass(YouTubeVideo, Video)
        assert issubclass(YouTubeVideo, BaseMedia)

    def test_local_video_is_video(self):
        assert issubclass(LocalVideo, Video)
        assert issubclass(LocalVideo, BaseMedia)
