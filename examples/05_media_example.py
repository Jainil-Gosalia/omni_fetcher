"""Media handling example for OmniFetcher."""
import asyncio
from datetime import datetime

from omni_fetcher.schemas.media import LocalVideo, LocalAudio, LocalImage, YouTubeVideo
from omni_fetcher.schemas.base import FetchMetadata


async def main():
    print("=" * 60)
    print("Media Handling Examples")
    print("=" * 60)
    
    print("\n1. Local Video")
    print("-" * 40)
    
    video_metadata = FetchMetadata(
        source_uri="file:///home/user/videos/movie.mp4",
        fetched_at=datetime.now(),
        mime_type="video/mp4",
        file_size=1_500_000_000,
    )
    
    local_video = LocalVideo(
        metadata=video_metadata,
        file_path="/home/user/videos/movie.mp4",
        file_name="movie.mp4",
        duration_seconds=7200.0,
        width=1920,
        height=1080,
        codec="h264",
    )
    
    print(f"File: {local_video.file_name}")
    print(f"Duration: {local_video.duration_seconds / 3600:.1f} hours")
    print(f"Resolution: {local_video.width}x{local_video.height}")
    print(f"Codec: {local_video.codec}")
    
    print("\n2. Local Audio")
    print("-" * 40)
    
    audio_metadata = FetchMetadata(
        source_uri="file:///music/song.flac",
        fetched_at=datetime.now(),
        mime_type="audio/flac",
    )
    
    local_audio = LocalAudio(
        metadata=audio_metadata,
        file_path="/music/artist/song.flac",
        file_name="song.flac",
        duration_seconds=245.0,
        artist="Test Artist",
        album="Test Album",
        title="Awesome Song",
        genre="Electronic",
    )
    
    print(f"Title: {local_audio.title}")
    print(f"Artist: {local_audio.artist}")
    print(f"Album: {local_audio.album}")
    print(f"Duration: {local_audio.duration_seconds / 60:.1f} min")
    
    print("\n3. Local Image")
    print("-" * 40)
    
    image_metadata = FetchMetadata(
        source_uri="file:///photos/vacation.png",
        fetched_at=datetime.now(),
        mime_type="image/png",
    )
    
    local_image = LocalImage(
        metadata=image_metadata,
        file_path="/photos/vacation.png",
        file_name="vacation.png",
        width=3840,
        height=2160,
        format="PNG",
        has_alpha=True,
        color_mode="RGBA",
        camera_make="Canon",
        camera_model="EOS R5",
    )
    
    print(f"File: {local_image.file_name}")
    print(f"Resolution: {local_image.width}x{local_image.height}")
    print(f"Format: {local_image.format}")
    print(f"Camera: {local_image.camera_make} {local_image.camera_model}")
    
    print("\n4. YouTube Video Schema")
    print("-" * 40)
    
    yt_metadata = FetchMetadata(
        source_uri="https://youtube.com/watch?v=dQw4w9WgXcQ",
        fetched_at=datetime.now(),
    )
    
    yt_video = YouTubeVideo(
        metadata=yt_metadata,
        video_id="dQw4w9WgXcQ",
        title="Never Gonna Give You Up",
        duration_seconds=213.0,
        uploader="RickAstley",
        view_count=1_200_000_000,
        like_count=12_000_000,
    )
    
    print(f"Title: {yt_video.title}")
    print(f"Video ID: {yt_video.video_id}")
    print(f"Uploader: {yt_video.uploader}")
    print(f"Views: {yt_video.view_count:,}")
    
    print("\n" + "=" * 60)
    print("All media types properly structured with Pydantic!")


if __name__ == "__main__":
    asyncio.run(main())
