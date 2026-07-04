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
    WebPageDocument,
    PDFDocument,
    DOCXDocument,
    PPTXDocument,
    SlideDocument,
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

# NOTE: Source-specific public schema classes (GitHub*, GoogleDrive*, Notion*,
# Confluence*, Slack*, Jira*, ...) are no longer part of the public API as of
# v1.0. The public contract is the canonical atoms + metadata (see the v1
# canonical contract and docs/migration-v1.md). The source-specific modules
# still exist under omni_fetcher.schemas for the legacy fetchers' internal use,
# but they are intentionally not re-exported here.

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("omni_fetcher")
except PackageNotFoundError:  # running from source without an installed dist
    __version__ = "0.0.0+unknown"

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
    "WebPageDocument",
    "PDFDocument",
    "DOCXDocument",
    "PPTXDocument",
    "SlideDocument",
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
