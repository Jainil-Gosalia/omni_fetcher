"""Media Pydantic models for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from omni_fetcher.schemas.base import BaseFetchedData, DataCategory, MediaType, FetchMetadata


class VideoResolution(str, Enum):
    """Standard video resolutions."""
    SD_480 = "854x480"
    HD_720 = "1280x720"
    FHD_1080 = "1920x1080"
    UHD_4K = "3840x2160"
    UHD_8K = "7680x4320"


class BaseMedia(BaseFetchedData):
    """Base model for media (audio/video/image)."""
    duration_seconds: Optional[float] = Field(None, ge=0, description="Duration in seconds")
    bitrate: Optional[int] = Field(None, ge=0, description="Bitrate in bits per second")
    
    category: DataCategory = DataCategory.MEDIA


class Video(BaseMedia):
    """Model for video content."""
    media_type: MediaType = MediaType.VIDEO_MP4
    width: Optional[int] = Field(None, ge=0, description="Video width in pixels")
    height: Optional[int] = Field(None, ge=0, description="Video height in pixels")
    codec: Optional[str] = Field(None, description="Video codec (e.g., h264, h265, vp9)")
    frame_rate: Optional[float] = Field(None, ge=0, description="Frame rate in fps")
    aspect_ratio: Optional[str] = Field(None, description="Aspect ratio (e.g., 16:9)")
    

class Audio(BaseMedia):
    """Model for audio content."""
    media_type: MediaType = MediaType.AUDIO_MP3
    sample_rate: Optional[int] = Field(None, ge=0, description="Sample rate in Hz")
    channels: Optional[int] = Field(None, ge=0, description="Number of audio channels")
    audio_codec: Optional[str] = Field(None, description="Audio codec (e.g., aac, mp3, flac)")
    audio_bitrate: Optional[int] = Field(None, ge=0, description="Audio bitrate in bps")


class Image(BaseMedia):
    """Model for image content."""
    media_type: MediaType = MediaType.IMAGE_JPEG
    width: Optional[int] = Field(None, ge=0, description="Image width in pixels")
    height: Optional[int] = Field(None, ge=0, description="Image height in pixels")
    format: Optional[str] = Field(None, description="Image format (JPEG, PNG, etc.)")
    color_mode: Optional[str] = Field(None, description="Color mode (RGB, RGBA, etc.)")
    has_alpha: bool = Field(False, description="Whether image has alpha channel")
    dpi: Optional[tuple[int, int]] = Field(None, description="DPI (x, y)")
    

class YouTubeVideo(Video):
    """Model for YouTube video."""
    media_type: MediaType = MediaType.VIDEO_MP4
    
    video_id: str = Field(..., description="YouTube video ID")
    title: str = Field(..., description="Video title")
    description: Optional[str] = Field(None, description="Video description")
    uploader: Optional[str] = Field(None, description="Channel name")
    uploader_url: Optional[str] = Field(None, description="Channel URL")
    upload_date: Optional[datetime] = Field(None, description="Upload date")
    view_count: Optional[int] = Field(None, ge=0, description="View count")
    like_count: Optional[int] = Field(None, ge=0, description="Like count")
    dislike_count: Optional[int] = Field(None, ge=0, description="Dislike count")
    comment_count: Optional[int] = Field(None, ge=0, description="Comment count")
    tags: Optional[list[str]] = Field(None, description="Video tags")
    category: Optional[str] = Field(None, description="Video category")
    license: Optional[str] = Field(None, description="License type")
    is_live: bool = Field(False, description="Whether video is live")
    is_private: bool = Field(False, description="Whether video is private")
    is_embedded: bool = Field(False, description="Whether embed is enabled")
    thumbnail_url: Optional[str] = Field(None, description="Thumbnail URL")
    captions_available: bool = Field(False, description="Whether captions are available")
    transcript: Optional[str] = Field(None, description="Video transcript")


class LocalVideo(Video):
    """Model for local video file."""
    file_path: str = Field(..., description="Full path to file")
    file_name: str = Field(..., description="File name")
    audio_codec: Optional[str] = Field(None, description="Audio codec")
    audio_bitrate: Optional[int] = Field(None, description="Audio bitrate in bps")
    created_at: Optional[datetime] = Field(None, description="File creation time")
    modified_at: Optional[datetime] = Field(None, description="File modification time")


class StreamAudio(Audio):
    """Model for streaming audio."""
    stream_url: str = Field(..., description="URL of the audio stream")
    format: Optional[str] = Field(None, description="Stream format")
    is_live: bool = Field(False, description="Whether this is a live stream")


class LocalAudio(Audio):
    """Model for local audio file."""
    file_path: str = Field(..., description="Full path to file")
    file_name: str = Field(..., description="File name")
    artist: Optional[str] = Field(None, description="Artist name")
    album: Optional[str] = Field(None, description="Album name")
    title: Optional[str] = Field(None, description="Track title")
    track_number: Optional[int] = Field(None, ge=0, description="Track number")
    year: Optional[int] = Field(None, description="Release year")
    genre: Optional[str] = Field(None, description="Genre")
    composer: Optional[str] = Field(None, description="Composer")
    created_at: Optional[datetime] = Field(None, description="File creation time")


class WebImage(Image):
    """Model for web image."""
    alt_text: Optional[str] = Field(None, description="Alt text")
    source_url: str = Field(..., description="Direct URL to image")
    page_url: Optional[str] = Field(None, description="URL of page containing image")
    photographer: Optional[str] = Field(None, description="Photographer name")
    license: Optional[str] = Field(None, description="Image license")


class LocalImage(Image):
    """Model for local image file."""
    file_path: str = Field(..., description="Full path to file")
    file_name: str = Field(..., description="File name")
    created_at: Optional[datetime] = Field(None, description="File creation time")
    modified_at: Optional[datetime] = Field(None, description="File modification time")
    camera_make: Optional[str] = Field(None, description="Camera manufacturer")
    camera_model: Optional[str] = Field(None, description="Camera model")
    gps_latitude: Optional[float] = Field(None, description="GPS latitude")
    gps_longitude: Optional[float] = Field(None, description="GPS longitude")
    orientation: Optional[int] = Field(None, description="EXIF orientation")
