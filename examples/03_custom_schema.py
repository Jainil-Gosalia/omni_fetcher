"""Custom schema example - extending base schemas with atomics."""

import asyncio
from datetime import datetime
from typing import Optional, List

from pydantic import Field

from omni_fetcher.schemas.media import Video, LocalVideo
from omni_fetcher.schemas.atomics import (
    TextDocument,
    AudioDocument,
    ImageDocument,
    TextFormat,
)
from omni_fetcher.schemas.base import FetchMetadata, DataCategory


class PodcastVideo(Video):
    """Extended video schema for podcast content."""

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


class PodcastWithTranscript(PodcastVideo):
    """Podcast video with full transcript support using atomics."""

    full_transcript: Optional[TextDocument] = None
    show_notes: Optional[TextDocument] = None
    cover_image: Optional[ImageDocument] = None
    audio_version: Optional[AudioDocument] = None

    def __init__(self, **data):
        super().__init__(**data)
        self.transcript_available = bool(self.full_transcript)


async def main():
    print("=" * 60)
    print("Custom Schema Examples")
    print("=" * 60)

    print("\n1. PodcastVideo Schema (extends Video)")
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

    print("\n2. VideoProject Schema (extends LocalVideo)")
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

    print("\n3. PodcastWithTranscript - using atomic schemas")
    print("-" * 40)

    podcast_atomic = PodcastWithTranscript(
        metadata=FetchMetadata(
            source_uri="https://example.com/podcast/ep124.mp4",
            fetched_at=datetime.now(),
        ),
        video_id="ep124",
        title="Episode 124: Advanced Patterns",
        duration_seconds=2400,
        width=1920,
        height=1080,
        codec="h264",
        podcast_name="Python Podcast",
        episode_number=124,
        season_number=3,
        full_transcript=TextDocument(
            source_uri="https://example.com/podcast/ep124_transcript.txt",
            content="Welcome to episode 124. Today we'll discuss advanced patterns...",
            format=TextFormat.TRANSCRIPT,
            language="en",
        ),
        show_notes=TextDocument(
            source_uri="https://example.com/podcast/ep124_notes.md",
            content="- Topic 1: Decorators\n- Topic 2: Context Managers\n- Topic 3: Generators",
            format=TextFormat.MARKDOWN,
        ),
        cover_image=ImageDocument(
            source_uri="https://example.com/podcast/ep124_cover.jpg",
            format="jpeg",
            width=1400,
            height=1400,
            alt_text="Episode 124 cover art",
        ),
        audio_version=AudioDocument(
            source_uri="https://example.com/podcast/ep124_audio.m4a",
            duration_seconds=2400,
            format="aac",
            sample_rate=44100,
            channels=2,
            transcript=TextDocument(
                source_uri="https://example.com/podcast/ep124_audio.txt",
                content="Audio version of episode 124...",
                format=TextFormat.TRANSCRIPT,
            ),
        ),
    )

    print(f"Title: {podcast_atomic.title}")
    print(f"Transcript: {podcast_atomic.full_transcript.word_count} words")
    print(f"Show notes: {podcast_atomic.show_notes.format}")
    print(f"Cover image: {podcast_atomic.cover_image.width}x{podcast_atomic.cover_image.height}")
    print(
        f"Audio: {podcast_atomic.audio_version.format} ({podcast_atomic.audio_version.sample_rate}Hz)"
    )
    print(f"Transcript available: {podcast_atomic.transcript_available}")

    print("\n4. Inheriting from Video with atomic components")
    print("-" * 40)

    class TutorialVideo(Video):
        """Video tutorial with structured content."""

        course_name: str = Field(..., description="Course name")
        lesson_number: int = Field(..., ge=1, description="Lesson number")
        description: str = Field(..., description="Lesson description")
        code_examples: Optional[List[TextDocument]] = None
        resource_links: Optional[List[str]] = None

    tutorial = TutorialVideo(
        metadata=FetchMetadata(
            source_uri="https://learn.example.com/python/lesson1.mp4",
            fetched_at=datetime.now(),
        ),
        video_id="py-lesson-1",
        duration_seconds=1800,
        width=1920,
        height=1080,
        codec="h264",
        course_name="Python Fundamentals",
        lesson_number=1,
        description="Introduction to Python basics",
        code_examples=[
            TextDocument(
                source_uri="https://learn.example.com/python/lesson1/ex1.py",
                content="print('Hello, World!')",
                format=TextFormat.CODE,
                language="python",
            ),
            TextDocument(
                source_uri="https://learn.example.com/python/lesson1/ex2.py",
                content="x = 10\ny = 20\nprint(x + y)",
                format=TextFormat.CODE,
                language="python",
            ),
        ],
        resource_links=[
            "https://docs.python.org/",
            "https://realpython.com/",
        ],
    )

    print(f"Course: {tutorial.course_name}")
    print(f"Lesson: {tutorial.lesson_number}")
    print(f"Description: {tutorial.description}")
    print(f"Code examples: {len(tutorial.code_examples)} files")
    for example in tutorial.code_examples:
        print(f"  - {example.source_uri}: {example.content[:30]}...")

    print("\n" + "=" * 60)
    print(f"PodcastVideo is Video: {issubclass(PodcastVideo, Video)}")
    print(f"VideoProject is LocalVideo: {issubclass(VideoProject, LocalVideo)}")
    print(
        f"PodcastWithTranscript is PodcastVideo: {issubclass(PodcastWithTranscript, PodcastVideo)}"
    )
    print("Custom schemas can include atomic schemas as nested attributes!")


if __name__ == "__main__":
    asyncio.run(main())
