"""OmniFetcher - Universal data fetcher with Pydantic schemas."""

from omni_fetcher.fetcher import OmniFetcher
from omni_fetcher.core.registry import source, SourceRegistry, SourceInfo
from omni_fetcher.core.exceptions import (
    OmniFetcherError,
    SourceNotFoundError,
    FetchError,
    ValidationError,
    SourceRegistrationError,
    SchemaError,
)
from omni_fetcher.fetchers.base import BaseFetcher, FetchResult
from omni_fetcher.schemas import (
    # Base
    BaseFetchedData,
    FetchMetadata,
    MediaType,
    DataCategory,
    # Media
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
    # Documents
    BaseDocument,
    TextDocument,
    MarkdownDocument,
    HTMLDocument,
    PDFDocument,
    CSVData,
    # Structured
    BaseStructuredData,
    JSONData,
    YAMLData,
    XMLData,
    GraphQLResponse,
)

__version__ = "0.1.0"

__all__ = [
    # Main class
    "OmniFetcher",
    # Core
    "source",
    "SourceRegistry",
    "SourceInfo",
    # Exceptions
    "OmniFetcherError",
    "SourceNotFoundError",
    "FetchError",
    "ValidationError",
    "SourceRegistrationError",
    "SchemaError",
    # Fetchers
    "BaseFetcher",
    "FetchResult",
    # Schemas - Base
    "BaseFetchedData",
    "FetchMetadata",
    "MediaType",
    "DataCategory",
    # Schemas - Media
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
    # Schemas - Documents
    "BaseDocument",
    "TextDocument",
    "MarkdownDocument",
    "HTMLDocument",
    "PDFDocument",
    "CSVData",
    # Schemas - Structured
    "BaseStructuredData",
    "JSONData",
    "YAMLData",
    "XMLData",
    "GraphQLResponse",
]
