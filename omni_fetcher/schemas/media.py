"""Media Pydantic models for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from omni_fetcher.schemas.base import DataCategory, MediaType
from omni_fetcher.schemas.atomics import (
    TextDocument,
    AudioDocument,
    ImageDocument,
)


class VideoResolution(str, Enum):
    """Standard video resolutions."""

    SD_480 = "854x480"
    HD_720 = "1280x720"
    FHD_1080 = "1920x1080"
    UHD_4K = "3840x2160"
    UHD_8K = "7680x4320"


class YouTubeVideo(BaseModel):
    """Model for YouTube video."""

    media_type: MediaType = MediaType.VIDEO_MP4
    category: DataCategory = DataCategory.MEDIA
    duration_seconds: Optional[float] = Field(None, ge=0, description="Duration in seconds")
    bitrate: Optional[int] = Field(None, ge=0, description="Bitrate in bits per second")
    width: Optional[int] = Field(None, ge=0, description="Video width in pixels")
    height: Optional[int] = Field(None, ge=0, description="Video height in pixels")
    codec: Optional[str] = Field(None, description="Video codec (e.g., h264, h265, vp9)")
    frame_rate: Optional[float] = Field(None, ge=0, description="Frame rate in fps")
    aspect_ratio: Optional[str] = Field(None, description="Aspect ratio (e.g., 16:9)")

    video_id: str = Field(..., description="YouTube video ID")
    title: str = Field(..., description="Video title")
    uploader: Optional[str] = Field(None, description="Channel name")
    uploader_url: Optional[str] = Field(None, description="Channel URL")
    upload_date: Optional[datetime] = Field(None, description="Upload date")
    view_count: Optional[int] = Field(None, ge=0, description="View count")
    like_count: Optional[int] = Field(None, ge=0, description="Like count")
    dislike_count: Optional[int] = Field(None, ge=0, description="Dislike count")
    comment_count: Optional[int] = Field(None, ge=0, description="Comment count")
    tags: Optional[list[str]] = Field(None, description="Video tags")
    video_category: Optional[str] = Field(None, description="Video category")
    license: Optional[str] = Field(None, description="License type")
    is_live: bool = Field(False, description="Whether video is live")
    is_private: bool = Field(False, description="Whether video is private")
    is_embedded: bool = Field(False, description="Whether embed is enabled")
    captions_available: bool = Field(False, description="Whether captions are available")

    text: Optional[TextDocument] = None
    transcript: Optional[TextDocument] = None
    audio: Optional[AudioDocument] = None
    thumbnail: Optional[ImageDocument] = None


class LocalVideo(BaseModel):
    """Model for local video file."""

    media_type: MediaType = MediaType.VIDEO_MP4
    category: DataCategory = DataCategory.MEDIA
    duration_seconds: Optional[float] = Field(None, ge=0, description="Duration in seconds")
    bitrate: Optional[int] = Field(None, ge=0, description="Bitrate in bits per second")
    width: Optional[int] = Field(None, ge=0, description="Video width in pixels")
    height: Optional[int] = Field(None, ge=0, description="Video height in pixels")
    codec: Optional[str] = Field(None, description="Video codec (e.g., h264, h265, vp9)")
    frame_rate: Optional[float] = Field(None, ge=0, description="Frame rate in fps")
    aspect_ratio: Optional[str] = Field(None, description="Aspect ratio (e.g., 16:9)")

    file_path: str = Field(..., description="Full path to file")
    file_name: str = Field(..., description="File name")
    created_at: Optional[datetime] = Field(None, description="File creation time")
    modified_at: Optional[datetime] = Field(None, description="File modification time")

    audio: Optional[AudioDocument] = None
    thumbnail: Optional[ImageDocument] = None
    captions: Optional[TextDocument] = None
