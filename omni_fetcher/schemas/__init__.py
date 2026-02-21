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
    VideoResolution,
)

from omni_fetcher.schemas.documents import (
    BaseDocument,
    HTMLDocument,
    PDFDocument,
)

from omni_fetcher.schemas.structured import (
    BaseStructuredData,
    JSONData,
    YAMLData,
    XMLData,
    GraphQLResponse,
)

from omni_fetcher.schemas.atomics import (
    AtomicBase,
    TextFormat,
    TextDocument as AtomicsTextDocument,
    AudioDocument,
    ImageDocument,
    VideoDocument,
    SpreadsheetDocument,
    SheetData,
)

# Backward compatibility shims (will raise ImportError with migration message)
from omni_fetcher.schemas.compat import (
    MarkdownDocument,
    TextDocument as DeprecatedTextDocument,
    StreamAudio,
    LocalAudio,
    WebImage,
    LocalImage,
    CSVData,
)

# Re-export atomics TextDocument with explicit name
TextDocument = AtomicsTextDocument

# Also expose the deprecated one for backward compat via __all__
OldTextDocument = DeprecatedTextDocument

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
    "VideoResolution",
    # Documents
    "BaseDocument",
    "HTMLDocument",
    "PDFDocument",
    # Structured
    "BaseStructuredData",
    "JSONData",
    "YAMLData",
    "XMLData",
    "GraphQLResponse",
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
    "MarkdownDocument",  # deprecated
    "OldTextDocument",  # deprecated, use atomics.TextDocument
    "StreamAudio",  # deprecated
    "LocalAudio",  # deprecated
    "WebImage",  # deprecated
    "LocalImage",  # deprecated
    "CSVData",  # deprecated, use SpreadsheetDocument
]
