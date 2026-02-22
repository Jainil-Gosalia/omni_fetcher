"""Backward compatibility shims for removed schemas.

This module provides deprecated aliases that raise helpful migration messages.
"""

_MIGRATION_BASE_MEDIA = (
    "BaseMedia has been removed. "
    "Use specific atomics (AudioDocument, ImageDocument, VideoDocument) "
    "or composites (YouTubeVideo, LocalVideo, PDFDocument, HTMLDocument)."
)

_MIGRATION_VIDEO = (
    "Video has been removed. "
    "Use VideoDocument for raw video, YouTubeVideo or LocalVideo for video with metadata."
)

_MIGRATION_AUDIO = (
    "Audio has been removed. Use AudioDocument from omni_fetcher.schemas.atomics instead."
)

_MIGRATION_IMAGE = (
    "Image has been removed. Use ImageDocument from omni_fetcher.schemas.atomics instead."
)

_MIGRATION_BASE_DOCUMENT = "BaseDocument has been removed. Use specific schemas instead."

_MIGRATION_TEXT_DOCUMENT = (
    "TextDocument has been replaced with the atomic TextDocument. "
    "Use omni_fetcher.schemas.atomics.TextDocument instead."
)

_MIGRATION_MARKDOWN = (
    "MarkdownDocument has been removed. "
    "Use TextDocument(format=TextFormat.MARKDOWN) from omni_fetcher.schemas.atomics instead."
)

_MIGRATION_STREAM_AUDIO = "StreamAudio has been removed. Use AudioDocument instead."

_MIGRATION_LOCAL_AUDIO = (
    "LocalAudio has been removed. "
    "Use AudioDocument with optional metadata fields (artist, album, etc.)."
)

_MIGRATION_LOCAL_IMAGE = (
    "LocalImage has been removed. "
    "Use ImageDocument with optional metadata fields (camera_make, gps_latitude, etc.)."
)

_MIGRATION_WEB_IMAGE = "WebImage has been removed. Use ImageDocument with optional page_url field."

_MIGRATION_CSV_DATA = "CSVData has been removed. Use SpreadsheetDocument instead."


class _RemovedSchema:
    """Base class for removed schemas that raises ImportError on access."""

    def __init__(self, message: str):
        self._message = message

    def __getattr__(self, name):
        raise ImportError(self._message)

    def __call__(self, *args, **kwargs):
        raise ImportError(self._message)


BaseMedia = _RemovedSchema(_MIGRATION_BASE_MEDIA)
Video = _RemovedSchema(_MIGRATION_VIDEO)
Audio = _RemovedSchema(_MIGRATION_AUDIO)
Image = _RemovedSchema(_MIGRATION_IMAGE)
BaseDocument = _RemovedSchema(_MIGRATION_BASE_DOCUMENT)
TextDocument = _RemovedSchema(_MIGRATION_TEXT_DOCUMENT)
MarkdownDocument = _RemovedSchema(_MIGRATION_MARKDOWN)
StreamAudio = _RemovedSchema(_MIGRATION_STREAM_AUDIO)
LocalAudio = _RemovedSchema(_MIGRATION_LOCAL_AUDIO)
LocalImage = _RemovedSchema(_MIGRATION_LOCAL_IMAGE)
WebImage = _RemovedSchema(_MIGRATION_WEB_IMAGE)
CSVData = _RemovedSchema(_MIGRATION_CSV_DATA)
