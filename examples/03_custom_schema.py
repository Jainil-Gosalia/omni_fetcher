"""Custom schema example - building schemas with atomics."""

import asyncio
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field

from omni_fetcher.schemas.atomics import (
    TextDocument,
    AudioDocument,
    ImageDocument,
    VideoDocument,
    SpreadsheetDocument,
    SheetData,
    TextFormat,
)


class PodcastEpisode(BaseModel):
    """Custom schema for podcast episodes using atomics."""

    episode_id: str = Field(..., description="Unique episode identifier")
    title: str = Field(..., description="Episode title")
    podcast_name: str = Field(..., description="Name of the podcast")
    episode_number: int = Field(..., ge=1, description="Episode number")
    season_number: Optional[int] = Field(None, description="Season number")
    description: Optional[TextDocument] = Field(None, description="Episode description")
    audio: Optional[AudioDocument] = Field(None, description="Audio file")
    video: Optional[VideoDocument] = Field(None, description="Video file (if video podcast)")
    cover_image: Optional[ImageDocument] = Field(None, description="Cover image")
    transcript: Optional[TextDocument] = Field(None, description="Full transcript")
    show_notes: Optional[TextDocument] = Field(None, description="Show notes in markdown")
    chapters: Optional[List[dict]] = Field(None, description="Chapter markers")
    published_at: Optional[datetime] = Field(None, description="Publication date")
    duration_seconds: Optional[float] = Field(None, description="Total duration in seconds")

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

    @property
    def formatted_duration(self) -> Optional[str]:
        if self.duration_seconds:
            return self._format_duration(self.duration_seconds)
        if self.audio and self.audio.duration_seconds:
            return self._format_duration(self.audio.duration_seconds)
        return None


class ProductDocument(BaseModel):
    """Custom schema for product documentation using atomics."""

    product_id: str = Field(..., description="Product SKU or ID")
    name: str = Field(..., description="Product name")
    category: str = Field(..., description="Product category")
    description: Optional[TextDocument] = Field(None, description="Product description")
    specifications: Optional[SpreadsheetDocument] = Field(
        None, description="Technical specifications"
    )
    images: List[ImageDocument] = Field(default_factory=list, description="Product images")
    manual: Optional[TextDocument] = Field(None, description="User manual")
    warranty_info: Optional[TextDocument] = Field(None, description="Warranty information")
    faq: Optional[TextDocument] = Field(None, description="Frequently asked questions")
    price: Optional[float] = Field(None, ge=0, description="Price in USD")
    currency: str = Field("USD", description="Currency code")
    in_stock: bool = Field(True, description="Availability status")
    tags: List[str] = Field(default_factory=list, description="Product tags")

    @property
    def primary_image(self) -> Optional[ImageDocument]:
        return self.images[0] if self.images else None

    @property
    def formatted_price(self) -> str:
        return f"{self.currency} {self.price:.2f}" if self.price else "N/A"


class CourseMaterial(BaseModel):
    """Custom schema for online course materials using atomics."""

    course_id: str = Field(..., description="Course identifier")
    title: str = Field(..., description="Course title")
    description: TextDocument = Field(..., description="Course overview")
    lessons: List[VideoDocument] = Field(default_factory=list, description="Video lessons")
    resources: List[TextDocument] = Field(
        default_factory=list, description="Downloadable resources"
    )
    thumbnail: Optional[ImageDocument] = Field(None, description="Course thumbnail")
    duration_seconds: Optional[float] = Field(None, description="Total course duration")
    difficulty: str = Field("intermediate", description="Difficulty level")
    instructor: Optional[str] = Field(None, description="Instructor name")

    @property
    def lesson_count(self) -> int:
        return len(self.lessons)


async def main():
    print("=" * 60)
    print("Custom Schema Examples with Atomics")
    print("=" * 60)

    print("\n1. PodcastEpisode Schema")
    print("-" * 40)

    podcast = PodcastEpisode(
        episode_id="ep-123",
        title="Building Better APIs",
        podcast_name="DevTalks",
        episode_number=123,
        season_number=3,
        description=TextDocument(
            source_uri="https://devtalks.io/ep123/description",
            content="In this episode we discuss API design best practices...",
            format=TextFormat.PLAIN,
            language="en",
        ),
        audio=AudioDocument(
            source_uri="https://devtalks.io/ep123/audio.m4a",
            duration_seconds=3723,
            format="aac",
            sample_rate=44100,
            channels=2,
            transcript=TextDocument(
                source_uri="https://devtalks.io/ep123/transcript.txt",
                content="Welcome to DevTalks episode 123...",
                format=TextFormat.TRANSCRIPT,
                language="en",
            ),
        ),
        cover_image=ImageDocument(
            source_uri="https://devtalks.io/ep123/cover.jpg",
            format="jpeg",
            width=1400,
            height=1400,
            alt_text="DevTalks Episode 123 cover",
        ),
        transcript=TextDocument(
            source_uri="https://devtalks.io/ep123/transcript.txt",
            content="Full transcript of the episode...",
            format=TextFormat.TRANSCRIPT,
            language="en",
        ),
        show_notes=TextDocument(
            source_uri="https://devtalks.io/ep123/notes.md",
            content="- Topic 1: REST APIs\n- Topic 2: GraphQL\n- Topic 3: gRPC",
            format=TextFormat.MARKDOWN,
        ),
        chapters=[
            {"time": 0, "title": "Intro"},
            {"time": 300, "title": "REST APIs"},
            {"time": 1200, "title": "GraphQL"},
            {"time": 2400, "title": "gRPC"},
            {"time": 3400, "title": "Conclusion"},
        ],
        published_at=datetime(2024, 1, 15),
        duration_seconds=3723,
    )

    print(f"Title: {podcast.title}")
    print(f"Podcast: {podcast.podcast_name}")
    print(f"Episode: S{podcast.season_number}E{podcast.episode_number}")
    print(f"Duration: {podcast.formatted_duration}")
    print(f"Has transcript: {podcast.transcript is not None}")
    print(f"Has audio: {podcast.audio is not None}")
    if podcast.audio and podcast.audio.transcript:
        print(f"Audio transcript: {podcast.audio.transcript.word_count} words")

    print("\n2. ProductDocument Schema")
    print("-" * 40)

    product = ProductDocument(
        product_id="PROD-001",
        name="Wireless Headphones Pro",
        category="Electronics",
        description=TextDocument(
            source_uri="https://shop.example.com/prod-001/description",
            content="Premium wireless headphones with noise cancellation",
            format=TextFormat.PLAIN,
            language="en",
        ),
        specifications=SpreadsheetDocument(
            source_uri="https://shop.example.com/prod-001/specs",
            sheets=[
                SheetData(
                    name="General",
                    headers=["Property", "Value"],
                    rows=[["Battery Life", "30 hours"], ["Weight", "250g"], ["Wireless", "Yes"]],
                    row_count=3,
                    col_count=2,
                ),
                SheetData(
                    name="Audio",
                    headers=["Property", "Value"],
                    rows=[
                        ["Driver Size", "40mm"],
                        ["Frequency Response", "20Hz - 20kHz"],
                        ["Impedance", "32 Ohms"],
                    ],
                    row_count=3,
                    col_count=2,
                ),
            ],
            format="xlsx",
            sheet_count=2,
        ),
        images=[
            ImageDocument(
                source_uri="https://shop.example.com/prod-001/front.jpg",
                format="jpeg",
                width=1200,
                height=1200,
                alt_text="Front view of headphones",
            ),
            ImageDocument(
                source_uri="https://shop.example.com/prod-001/side.jpg",
                format="jpeg",
                width=1200,
                height=800,
                alt_text="Side view of headphones",
            ),
        ],
        manual=TextDocument(
            source_uri="https://shop.example.com/prod-001/manual.pdf",
            content="1. Unbox the headphones\n2. Charge for 2 hours\n3. Connect via Bluetooth",
            format=TextFormat.PLAIN,
        ),
        warranty_info=TextDocument(
            source_uri="https://shop.example.com/prod-001/warranty",
            content="2-year limited warranty covering manufacturing defects",
            format=TextFormat.PLAIN,
        ),
        price=299.99,
        currency="USD",
        in_stock=True,
        tags=["audio", "wireless", "noise-cancelling", "premium"],
    )

    print(f"Product: {product.name}")
    print(f"Category: {product.category}")
    print(f"Price: {product.formatted_price}")
    print(f"In Stock: {product.in_stock}")
    print(f"Primary Image: {product.primary_image.width}x{product.primary_image.height}")
    print(f"Tags: {', '.join(product.tags)}")
    if product.specifications:
        print(f"Specs sheets: {product.specifications.sheet_count}")
        for sheet in product.specifications.sheets:
            print(f"  - {sheet.name}: {sheet.row_count} rows")

    print("\n3. CourseMaterial Schema")
    print("-" * 40)

    course = CourseMaterial(
        course_id="PY-101",
        title="Python Fundamentals",
        description=TextDocument(
            source_uri="https://learn.example.com/py101/description",
            content="Learn Python from scratch. This course covers basics...",
            format=TextFormat.MARKDOWN,
            language="en",
        ),
        lessons=[
            VideoDocument(
                source_uri="https://learn.example.com/py101/lesson1.mp4",
                duration_seconds=1800,
                format="mp4",
                resolution=(1920, 1080),
                fps=30.0,
            ),
            VideoDocument(
                source_uri="https://learn.example.com/py101/lesson2.mp4",
                duration_seconds=2400,
                format="mp4",
                resolution=(1920, 1080),
                fps=30.0,
            ),
            VideoDocument(
                source_uri="https://learn.example.com/py101/lesson3.mp4",
                duration_seconds=2100,
                format="mp4",
                resolution=(1920, 1080),
                fps=30.0,
            ),
        ],
        resources=[
            TextDocument(
                source_uri="https://learn.example.com/py101/cheatsheet.pdf",
                content="Python quick reference guide",
                format=TextFormat.PLAIN,
            ),
            TextDocument(
                source_uri="https://learn.example.com/py101/exercises.py",
                content="Practice exercises for the course",
                format=TextFormat.CODE,
                language="python",
            ),
        ],
        thumbnail=ImageDocument(
            source_uri="https://learn.example.com/py101/thumb.jpg",
            format="jpeg",
            width=1280,
            height=720,
            alt_text="Python Fundamentals course thumbnail",
        ),
        duration_seconds=6300,
        difficulty="beginner",
        instructor="Jane Doe",
    )

    print(f"Course: {course.title}")
    print(f"Instructor: {course.instructor}")
    print(f"Difficulty: {course.difficulty}")
    print(f"Lessons: {course.lesson_count}")
    print(f"Total Duration: {course.duration_seconds / 3600:.1f} hours")
    print(f"Resources: {len(course.resources)} files")
    for res in course.resources:
        print(
            f"  - {res.source_uri.split('/')[-1]}: {res.format.value if hasattr(res.format, 'value') else res.format}"
        )

    print("\n" + "=" * 60)
    print("Summary: Custom schemas built with atomics")
    print("-" * 40)
    print("PodcastEpisode uses: TextDocument, AudioDocument, VideoDocument, ImageDocument")
    print("ProductDocument uses: TextDocument, ImageDocument, SpreadsheetDocument")
    print("CourseMaterial uses: TextDocument, VideoDocument, ImageDocument")
    print("\nAtomics are composable - mix and match as needed!")


if __name__ == "__main__":
    asyncio.run(main())
