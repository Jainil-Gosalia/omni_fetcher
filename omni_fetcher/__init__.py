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

from omni_fetcher.schemas.github import (
    GitHubFile,
    GitHubIssue,
    GitHubPR,
    GitHubRelease,
    GitHubRepo,
    GitHubIssueContainer,
    GitHubReleaseContainer,
    GitHubPRContainer,
)

from omni_fetcher.schemas.google import (
    GoogleDriveFile,
    GoogleDriveFolder,
    GoogleDriveContainer,
    GoogleSheetsSpreadsheet,
    GoogleDocsDocument,
    GoogleSlidesPresentation,
)

from omni_fetcher.schemas.notion import (
    NotionPage,
    NotionDatabase,
    NotionBlock,
    NotionRichText,
    NotionUser,
    NotionProperty,
)

from omni_fetcher.schemas.confluence import (
    ConfluencePage,
    ConfluenceSpace,
    ConfluenceAttachment,
    ConfluenceUser,
    ConfluenceComment,
)

from omni_fetcher.schemas.slack import (
    SlackMessage,
    SlackThread,
    SlackChannel,
    SlackDM,
)

__version__ = "0.9.0"

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
    # GitHub
    "GitHubFile",
    "GitHubIssue",
    "GitHubPR",
    "GitHubRelease",
    "GitHubRepo",
    "GitHubIssueContainer",
    "GitHubReleaseContainer",
    "GitHubPRContainer",
    # Google Drive/Sheets/Docs/Slides
    "GoogleDriveFile",
    "GoogleDriveFolder",
    "GoogleDriveContainer",
    "GoogleSheetsSpreadsheet",
    "GoogleDocsDocument",
    "GoogleSlidesPresentation",
    # Notion
    "NotionPage",
    "NotionDatabase",
    "NotionBlock",
    "NotionRichText",
    "NotionUser",
    "NotionProperty",
    # Confluence
    "ConfluencePage",
    "ConfluenceSpace",
    "ConfluenceAttachment",
    "ConfluenceUser",
    "ConfluenceComment",
    # Slack
    "SlackMessage",
    "SlackThread",
    "SlackChannel",
    "SlackDM",
]
