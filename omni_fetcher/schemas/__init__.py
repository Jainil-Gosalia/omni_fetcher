"""OmniFetcher schemas."""

from omni_fetcher.schemas.base import (
    BaseFetchedData,
    FetchMetadata,
    MediaType,
    DataCategory,
)

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
    VideoResolution,
)

from omni_fetcher.schemas.documents import (
    BaseDocument,
    TextDocument,
    MarkdownDocument,
    HTMLDocument,
    PDFDocument,
    CSVData,
)

from omni_fetcher.schemas.structured import (
    BaseStructuredData,
    JSONData,
    YAMLData,
    XMLData,
    GraphQLResponse,
)

__all__ = [
    # Base
    "BaseFetchedData",
    "FetchMetadata",
    "MediaType",
    "DataCategory",
    # Media
    "BaseMedia",
    "Video",
    "Audio",
    "Image",
    "YouTubeVideo",
    "LocalVideo",
    "StreamAudio",
    "LocalAudio",
    "WebImage",
    "LocalImage",
    "VideoResolution",
    # Documents
    "BaseDocument",
    "TextDocument",
    "MarkdownDocument",
    "HTMLDocument",
    "PDFDocument",
    "CSVData",
    # Structured
    "BaseStructuredData",
    "JSONData",
    "YAMLData",
    "XMLData",
    "GraphQLResponse",
]
