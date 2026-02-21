"""Custom schema example - extending base schemas."""
import asyncio
from datetime import datetime
from typing import Optional, List

from pydantic import Field

from omni_fetcher.schemas.media import Video, LocalVideo
from omni_fetcher.schemas.base import FetchMetadata, DataCategory


class PodcastVideo(Video):
    """Extended video schema for podcast content."""
    
    # Add title since Video doesn't have it
    title: Optional[str] = Field(None, description="Episode title")
    podcast_name: Optional[str] = Field(None, description="Name of the podcast")
    episode_number: Optional[int] = Field(None, description="Episode number")
    season_number: Optional[int] = Field(None, description="Season number")
    duration_formatted: Optional[str] = Field(None, description="Human-readable duration")
    transcript_available: bool = Field(False, description="Transcript available")
    chapters: Optional[List[dict]] = Field(None, description="Chapter markers")
    
    category: DataCategory = DataCategory.MEDIA
    
    def __init__(self, **data):
        super().__init__(**data)
        if self.duration_seconds and not self.duration_formatted:
            self.duration_formatted = self._format_duration(self.duration_seconds)
    
    def _format_duration(self, seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"


class VideoProject(LocalVideo):
    """Extended local video schema for video editing projects."""
    
    project_file: Optional[str] = Field(None, description="Project file path")
    color_space: Optional[str] = Field(None, description="Color space")
    bit_depth: Optional[int] = Field(None, description="Bit depth")
    audio_tracks: Optional[int] = Field(None, description="Number of audio tracks")
    tags: Optional[List[str]] = Field(None, description="User-defined tags")
    rating: Optional[int] = Field(None, ge=0, le=5, description="User rating (0-5)")


async def main():
    print("=" * 60)
    print("Custom Schema Examples")
    print("=" * 60)
    
    print("\n1. PodcastVideo Schema")
    print("-" * 40)
    
    metadata = FetchMetadata(
        source_uri="https://example.com/podcast/ep123.mp4",
        fetched_at=datetime.now(),
        mime_type="video/mp4",
    )
    
    podcast = PodcastVideo(
        metadata=metadata,
        video_id="ep123",
        title="Episode 123: Python Tips",
        duration_seconds=3723,
        width=1920,
        height=1080,
        codec="h264",
        podcast_name="Python Podcast",
        episode_number=123,
        season_number=3,
        transcript_available=True,
        chapters=[
            {"time": 0, "title": "Intro"},
            {"time": 300, "title": "Main Topic"},
        ],
    )
    
    print(f"Title: {podcast.title}")
    print(f"Duration: {podcast.duration_formatted}")
    print(f"Podcast: {podcast.podcast_name}")
    print(f"Episode: S{podcast.season_number}E{podcast.episode_number}")
    
    print("\n2. VideoProject Schema")
    print("-" * 40)
    
    project_metadata = FetchMetadata(
        source_uri="file:///projects/video/project.mov",
        fetched_at=datetime.now(),
    )
    
    project = VideoProject(
        metadata=project_metadata,
        file_path="/projects/video/project.mov",
        file_name="project.mov",
        duration_seconds=7200,
        codec="prores",
        project_file="/projects/video/project.prproj",
        color_space="Rec.709",
        audio_tracks=6,
        tags=["interview", "approved"],
        rating=5,
    )
    
    print(f"File: {project.file_name}")
    print(f"Project: {project.project_file}")
    print(f"Color: {project.color_space}")
    print(f"Rating: {project.rating}/5")
    
    print("\n" + "=" * 60)
    print(f"PodcastVideo is Video: {issubclass(PodcastVideo, Video)}")
    print(f"VideoProject is LocalVideo: {issubclass(VideoProject, LocalVideo)}")


if __name__ == "__main__":
    asyncio.run(main())
