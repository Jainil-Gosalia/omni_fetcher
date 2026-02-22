"""OmniFetcher schemas."""

from omni_fetcher.schemas.base import (
    BaseFetchedData,
    FetchMetadata,
    MediaType,
    DataCategory,
)

from omni_fetcher.schemas.media import (
    YouTubeVideo,
    LocalVideo,
    VideoResolution,
)

from omni_fetcher.schemas.documents import (
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
    TextDocument as DeprecatedTextDocument,
    StreamAudio,
    LocalAudio,
    WebImage,
    LocalImage,
    CSVData,
)

from omni_fetcher.schemas.github import (
    GitHubFile,
    GitHubIssue,
    GitHubPR,
    GitHubRelease,
    GitHubRepo,
    GitHubIssueContainer,
    GitHubReleaseContainer,
    GitHubPRContainer,
    detect_language,
)

OldTextDocument = DeprecatedTextDocument

__all__ = [
    # Base
    "BaseFetchedData",
    "FetchMetadata",
    "MediaType",
    "DataCategory",
    # Media
    "YouTubeVideo",
    "LocalVideo",
    "VideoResolution",
    # Documents
    "WebPageDocument",
    "PDFDocument",
    "DOCXDocument",
    "PPTXDocument",
    "SlideDocument",
    # Atomics
    "AtomicBase",
    "TextFormat",
    "TextDocument",
    "AudioDocument",
    "ImageDocument",
    "VideoDocument",
    "SpreadsheetDocument",
    "SheetData",
    # Backward compatibility (deprecated - raise ImportError)
    "MarkdownDocument",
    "OldTextDocument",
    "StreamAudio",
    "LocalAudio",
    "WebImage",
    "LocalImage",
    "CSVData",
    # GitHub
    "GitHubFile",
    "GitHubIssue",
    "GitHubPR",
    "GitHubRelease",
    "GitHubRepo",
    "GitHubIssueContainer",
    "GitHubReleaseContainer",
    "GitHubPRContainer",
    "detect_language",
]
