"""OmniFetcher - Universal data fetcher with Pydantic schemas.

The supported public API is the v1 canonical contract under
``omni_fetcher.v1``. The legacy pre-v1 layer re-exported here is deprecated
and will be removed in 2.0; its names now resolve lazily (PEP 562), so
``import omni_fetcher`` -- and therefore ``import omni_fetcher.v1`` -- no
longer imports the legacy fetcher tree or triggers its deprecation warning.
"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any

try:
    __version__ = _pkg_version("omni_fetcher")
except PackageNotFoundError:  # running from source without an installed dist
    __version__ = "0.0.0+unknown"

# NOTE: Source-specific public schema classes (GitHub*, GoogleDrive*, Notion*,
# Confluence*, Slack*, Jira*, ...) are no longer part of the public API as of
# v1.0. The public contract is the canonical atoms + metadata (see the v1
# canonical contract and docs/migration-v1.md). The source-specific modules
# still exist under omni_fetcher.schemas for the legacy fetchers' internal use,
# but they are intentionally not re-exported here.

# Every legacy public name, mapped to (module, attribute). ``None`` as the
# attribute re-exports the module itself. Resolution is lazy: the legacy
# layer is imported -- and its DeprecationWarning fired -- only when one of
# these names is actually used.
_LEGACY_EXPORTS: dict[str, tuple[str, str | None]] = {
    "OmniFetcher": ("omni_fetcher.fetcher", "OmniFetcher"),
    "source": ("omni_fetcher.core.registry", "source"),
    "SourceRegistry": ("omni_fetcher.core.registry", "SourceRegistry"),
    "SourceInfo": ("omni_fetcher.core.registry", "SourceInfo"),
    "exceptions": ("omni_fetcher.core.exceptions", None),
    "BaseFetcher": ("omni_fetcher.fetchers.base", "BaseFetcher"),
    "FetchResult": ("omni_fetcher.fetchers.base", "FetchResult"),
    "BaseFetchedData": ("omni_fetcher.schemas", "BaseFetchedData"),
    "FetchMetadata": ("omni_fetcher.schemas", "FetchMetadata"),
    "MediaType": ("omni_fetcher.schemas", "MediaType"),
    "DataCategory": ("omni_fetcher.schemas", "DataCategory"),
    "VideoResolution": ("omni_fetcher.schemas", "VideoResolution"),
    "YouTubeVideo": ("omni_fetcher.schemas", "YouTubeVideo"),
    "LocalVideo": ("omni_fetcher.schemas", "LocalVideo"),
    "WebPageDocument": ("omni_fetcher.schemas", "WebPageDocument"),
    "PDFDocument": ("omni_fetcher.schemas", "PDFDocument"),
    "DOCXDocument": ("omni_fetcher.schemas", "DOCXDocument"),
    "PPTXDocument": ("omni_fetcher.schemas", "PPTXDocument"),
    "SlideDocument": ("omni_fetcher.schemas", "SlideDocument"),
    "AtomicBase": ("omni_fetcher.schemas.atomics", "AtomicBase"),
    "TextFormat": ("omni_fetcher.schemas.atomics", "TextFormat"),
    "TextDocument": ("omni_fetcher.schemas.atomics", "TextDocument"),
    "AudioDocument": ("omni_fetcher.schemas.atomics", "AudioDocument"),
    "ImageDocument": ("omni_fetcher.schemas.atomics", "ImageDocument"),
    "VideoDocument": ("omni_fetcher.schemas.atomics", "VideoDocument"),
    "SpreadsheetDocument": ("omni_fetcher.schemas.atomics", "SpreadsheetDocument"),
    "SheetData": ("omni_fetcher.schemas.atomics", "SheetData"),
    "MarkdownDocument": ("omni_fetcher.schemas.compat", "MarkdownDocument"),
    "OldTextDocument": ("omni_fetcher.schemas.compat", "TextDocument"),
    "StreamAudio": ("omni_fetcher.schemas.compat", "StreamAudio"),
    "LocalAudio": ("omni_fetcher.schemas.compat", "LocalAudio"),
    "WebImage": ("omni_fetcher.schemas.compat", "WebImage"),
    "LocalImage": ("omni_fetcher.schemas.compat", "LocalImage"),
    "CSVData": ("omni_fetcher.schemas.compat", "CSVData"),
}

__all__ = list(_LEGACY_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve a legacy export lazily, warning once about the deprecation."""
    try:
        module_path, attribute = _LEGACY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    from omni_fetcher._deprecation import warn_legacy_use

    warn_legacy_use()
    module = import_module(module_path)
    return module if attribute is None else getattr(module, attribute)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LEGACY_EXPORTS))
