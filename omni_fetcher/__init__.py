"""OmniFetcher - Universal data fetcher with Pydantic schemas."""

from omni_fetcher.fetcher import OmniFetcher
from omni_fetcher.core.registry import source, SourceRegistry, SourceInfo
from omni_fetcher.core import exceptions
from omni_fetcher.fetchers.base import BaseFetcher, FetchResult
from omni_fetcher.schemas import (
    BaseFetchedData,
    FetchMetadata,
    MediaType,
    DataCategory,
    VideoResolution,
    YouTubeVideo,
    LocalVideo,
    HTMLDocument,
    PDFDocument,
)
from omni_fetcher.schemas.atomics import (
    AtomicBase,
    TextFormat,
    TextDocument,
    AudioDocument,
    ImageDocument,
    VideoDocument,
    SpreadsheetDocument,
    SheetData,
)

from omni_fetcher.schemas.compat import (
    MarkdownDocument,
    TextDocument as OldTextDocument,
    StreamAudio,
    LocalAudio,
    WebImage,
    LocalImage,
    CSVData,
)

__version__ = "0.3.1"

__all__ = [
    "OmniFetcher",
    "source",
    "SourceRegistry",
    "SourceInfo",
    "exceptions",
    "BaseFetcher",
    "FetchResult",
    "BaseFetchedData",
    "FetchMetadata",
    "MediaType",
    "DataCategory",
    "VideoResolution",
    "YouTubeVideo",
    "LocalVideo",
    "HTMLDocument",
    "PDFDocument",
    "AtomicBase",
    "TextFormat",
    "TextDocument",
    "AudioDocument",
    "ImageDocument",
    "VideoDocument",
    "SpreadsheetDocument",
    "SheetData",
    "MarkdownDocument",
    "OldTextDocument",
    "StreamAudio",
    "LocalAudio",
    "WebImage",
    "LocalImage",
    "CSVData",
]
