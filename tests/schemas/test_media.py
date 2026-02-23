"""Tests for media Pydantic models."""

from omni_fetcher.schemas.media import YouTubeVideo, LocalVideo, VideoResolution
from omni_fetcher.schemas.atomics import TextDocument, AudioDocument, ImageDocument, TextFormat
from omni_fetcher.schemas.base import MediaType


class TestYouTubeVideo:
    def test_creation(self):
        yt_video = YouTubeVideo(
            video_id="abc123",
            title="Test Video",
            uploader="TestUploader",
            view_count=1000000,
            duration_seconds=600.0,
            width=1920,
            height=1080,
            codec="h264",
        )
        assert yt_video.video_id == "abc123"
        assert yt_video.title == "Test Video"
        assert yt_video.media_type == MediaType.VIDEO_MP4

    def test_with_atomics(self):
        text_doc = TextDocument(
            source_uri="https://example.com/transcript.txt",
            content="Video transcript content",
            format=TextFormat.TRANSCRIPT,
        )
        audio_doc = AudioDocument(
            source_uri="https://example.com/audio.mp3",
            duration_seconds=600.0,
            format="mp3",
        )
        thumbnail_doc = ImageDocument(
            source_uri="https://example.com/thumb.jpg",
            format="jpeg",
            width=320,
            height=180,
        )
        yt_video = YouTubeVideo(
            video_id="abc123",
            title="Test Video",
            transcript=text_doc,
            audio=audio_doc,
            thumbnail=thumbnail_doc,
        )
        assert yt_video.transcript is not None
        assert yt_video.transcript.content == "Video transcript content"
        assert yt_video.audio is not None
        assert yt_video.thumbnail is not None
        assert yt_video.thumbnail.width == 320

    def test_optional_fields(self):
        yt_video = YouTubeVideo(
            video_id="abc123",
            title="Minimal Video",
        )
        assert yt_video.video_id == "abc123"
        assert yt_video.title == "Minimal Video"
        assert yt_video.uploader is None
        assert yt_video.duration_seconds is None
        assert yt_video.width is None
        assert yt_video.height is None
        assert yt_video.text is None
        assert yt_video.transcript is None
        assert yt_video.audio is None
        assert yt_video.thumbnail is None


class TestLocalVideo:
    def test_creation(self):
        local_video = LocalVideo(
            file_path="/home/user/videos/movie.mp4",
            file_name="movie.mp4",
            duration_seconds=7200.0,
            width=1920,
            height=1080,
            codec="h264",
        )
        assert local_video.file_path == "/home/user/videos/movie.mp4"
        assert local_video.file_name == "movie.mp4"
        assert local_video.media_type == MediaType.VIDEO_MP4

    def test_with_atomics(self):
        audio_doc = AudioDocument(
            source_uri="file:///home/user/videos/movie.mp3",
            duration_seconds=7200.0,
            format="mp3",
        )
        thumbnail_doc = ImageDocument(
            source_uri="file:///home/user/videos/movie_thumb.jpg",
            format="jpeg",
            width=320,
            height=180,
        )
        captions_doc = TextDocument(
            source_uri="file:///home/user/videos/movie.srt",
            content="Caption content",
            format=TextFormat.TRANSCRIPT,
        )
        local_video = LocalVideo(
            file_path="/home/user/videos/movie.mp4",
            file_name="movie.mp4",
            audio=audio_doc,
            thumbnail=thumbnail_doc,
            captions=captions_doc,
        )
        assert local_video.audio is not None
        assert local_video.thumbnail is not None
        assert local_video.captions is not None
        assert local_video.captions.content == "Caption content"

    def test_optional_fields(self):
        local_video = LocalVideo(
            file_path="/home/user/videos/movie.mp4",
            file_name="movie.mp4",
        )
        assert local_video.file_path == "/home/user/videos/movie.mp4"
        assert local_video.file_name == "movie.mp4"
        assert local_video.duration_seconds is None
        assert local_video.width is None
        assert local_video.height is None
        assert local_video.codec is None
        assert local_video.created_at is None
        assert local_video.modified_at is None
        assert local_video.audio is None
        assert local_video.thumbnail is None
        assert local_video.captions is None


class TestVideoResolution:
    def test_enum_values(self):
        assert VideoResolution.SD_480.value == "854x480"
        assert VideoResolution.HD_720.value == "1280x720"
        assert VideoResolution.FHD_1080.value == "1920x1080"
        assert VideoResolution.UHD_4K.value == "3840x2160"
        assert VideoResolution.UHD_8K.value == "7680x4320"

    def test_enum_count(self):
        assert len(VideoResolution) == 5
