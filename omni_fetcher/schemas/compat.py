"""Backward compatibility shims for removed schemas.

This module provides deprecated aliases that raise helpful migration messages.
Removed in v0.3.0 - use atomic schemas instead.
"""

# Migration messages
_MIGRATION_MARKDOWN = (
    "MarkdownDocument has been removed. "
    "Use TextDocument(format=TextFormat.MARKDOWN) from omni_fetcher.schemas.atomics instead."
)

_MIGRATION_TEXT = (
    "TextDocument has been replaced with the atomic TextDocument. "
    "Import it from omni_fetcher.schemas.atomics instead."
)

_MIGRATION_STREAM_AUDIO = (
    "StreamAudio has been removed. Use AudioDocument from omni_fetcher.schemas.atomics instead."
)

_MIGRATION_LOCAL_AUDIO = (
    "LocalAudio has been removed. Use AudioDocument from omni_fetcher.schemas.atomics instead."
)

_MIGRATION_WEB_IMAGE = (
    "WebImage has been removed. Use ImageDocument from omni_fetcher.schemas.atomics instead."
)

_MIGRATION_LOCAL_IMAGE = (
    "LocalImage has been removed. Use ImageDocument from omni_fetcher.schemas.atomics instead."
)

_MIGRATION_CSV_DATA = (
    "CSVData has been removed. Use SpreadsheetDocument from omni_fetcher.schemas.atomics instead."
)


class _RemovedSchema:
    """Base class for removed schemas that raises ImportError on access."""

    def __init__(self, message: str):
        self._message = message

    def __getattr__(self, name):
        raise ImportError(self._message)

    def __call__(self, *args, **kwargs):
        raise ImportError(self._message)


# These will raise ImportError when accessed
MarkdownDocument = _RemovedSchema(_MIGRATION_MARKDOWN)
TextDocument = _RemovedSchema(_MIGRATION_TEXT)
StreamAudio = _RemovedSchema(_MIGRATION_STREAM_AUDIO)
LocalAudio = _RemovedSchema(_MIGRATION_LOCAL_AUDIO)
WebImage = _RemovedSchema(_MIGRATION_WEB_IMAGE)
LocalImage = _RemovedSchema(_MIGRATION_LOCAL_IMAGE)
CSVData = _RemovedSchema(_MIGRATION_CSV_DATA)
