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

from omni_fetcher.schemas.jira import (
    JiraIssue,
    JiraEpic,
    JiraSprint,
    JiraProject,
)

from omni_fetcher.schemas.linear import (
    LinearIssue,
    LinearTeam,
    LinearProject,
    LinearCycle,
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
    # Jira
    "JiraIssue",
    "JiraEpic",
    "JiraSprint",
    "JiraProject",
    # Linear
    "LinearIssue",
    "LinearTeam",
    "LinearProject",
    "LinearCycle",
]
