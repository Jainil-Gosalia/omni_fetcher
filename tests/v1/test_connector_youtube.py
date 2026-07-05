"""External-behaviour tests for the v1 ``youtube`` connector (issue 006).

No network and no real yt-dlp extraction: the blocking ``_extract_info``
seam is replaced with a stub returning canned metadata (or raising a real
``yt_dlp.utils.DownloadError`` for the classification tests). Only the
public surface is exercised via ``fetch()``:

- a video yields a ``"video"`` node carrying a ``Video`` atom, the
  description as a plain ``Text`` atom, and descriptive fields in
  ``source_extra["youtube"]`` + the metadata core;
- a playlist yields a ``"playlist"`` container with per-entry video
  children;
- yt-dlp failure messages classify onto the ErrorKind taxonomy
  (unavailable -> NOT_FOUND, private -> PERMISSION_DENIED,
  timeout -> TRANSIENT), always as returned values.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
import yt_dlp

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors.youtube import YouTubeConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

pytestmark = pytest.mark.asyncio

WATCH_URI = "https://www.youtube.com/watch?v=xyz123"
SHORT_URI = "https://youtu.be/xyz123"


def _video_info(**overrides: Any) -> dict[str, Any]:
    """A representative yt-dlp video info dict (no download performed)."""
    info: dict[str, Any] = {
        "id": "xyz123",
        "title": "Test Video",
        "description": "A description of the video.",
        "uploader": "RickAstley",
        "channel": "RickAstley",
        "webpage_url": WATCH_URI,
        "upload_date": "20260115",
        "duration": 213,
        "view_count": 1200000,
        "like_count": 34000,
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "tags": ["music"],
    }
    info.update(overrides)
    return info


def _connector_with(
    info: Optional[dict[str, Any]] = None,
    exc: Optional[Exception] = None,
) -> YouTubeConnector:
    """A connector whose blocking ``_extract_info`` seam is scripted."""
    connector = YouTubeConnector()

    def fake_extract(uri: str) -> Optional[dict[str, Any]]:
        if exc is not None:
            raise exc
        return info

    connector._extract_info = fake_extract  # type: ignore[method-assign]
    return connector


# ---------------------------------------------------------------------------
# Videos


async def test_video_yields_video_node_with_atoms() -> None:
    """A video maps onto a ``"video"`` node with Video + Text atoms."""
    connector = _connector_with(_video_info())

    result = await connector.fetch(WATCH_URI)

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "video"
    assert len(node.find_atoms(AtomKind.VIDEO)) == 1
    texts = node.find_atoms(AtomKind.TEXT)
    assert len(texts) == 1
    assert texts[0].content == "A description of the video."
    assert texts[0].format == TextFormat.PLAIN


async def test_video_descriptive_fields_in_source_extra_and_core() -> None:
    """Descriptive fields live in ``source_extra["youtube"]`` + the core."""
    connector = _connector_with(_video_info())

    result = await connector.fetch(WATCH_URI)

    assert isinstance(result, Success)
    metadata = result.tree.metadata
    assert metadata.id == "xyz123"
    assert metadata.author == "RickAstley"
    assert metadata.created is not None and metadata.created.year == 2026
    assert metadata.source_url == WATCH_URI

    extra = metadata.source_extra["youtube"]
    assert extra["title"] == "Test Video"


async def test_short_url_shape_is_handled_too() -> None:
    """A ``youtu.be`` URI resolves like the full watch URL."""
    connector = _connector_with(_video_info())

    result = await connector.fetch(SHORT_URI)

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == "video"


# ---------------------------------------------------------------------------
# Playlists


async def test_playlist_yields_container_with_video_children() -> None:
    """A playlist maps onto a ``"playlist"`` container of video nodes."""
    playlist = {
        "_type": "playlist",
        "id": "PL123",
        "title": "Greatest Hits",
        "uploader": "RickAstley",
        "webpage_url": "https://www.youtube.com/playlist?list=PL123",
        "entries": [
            _video_info(id="v1", title="One"),
            None,  # unresolvable entries are skipped, not fabricated
            _video_info(id="v2", title="Two"),
        ],
    }
    connector = _connector_with(playlist)

    result = await connector.fetch("https://www.youtube.com/playlist?list=PL123")

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "playlist"
    children = node.find_by_kind("video")
    assert [child.metadata.id for child in children] == ["v1", "v2"]


# ---------------------------------------------------------------------------
# Typed failures


@pytest.mark.parametrize(
    ("message", "expected_kind"),
    [
        ("ERROR: Video unavailable", ErrorKind.NOT_FOUND),
        ("ERROR: Private video. Sign in.", ErrorKind.PERMISSION_DENIED),
        ("ERROR: request timed out", ErrorKind.TRANSIENT),
    ],
)
async def test_download_errors_classify_onto_the_taxonomy(
    message: str, expected_kind: ErrorKind
) -> None:
    """yt-dlp failure messages map onto typed ErrorKinds, never raises."""
    connector = _connector_with(exc=yt_dlp.utils.DownloadError(message))

    result = await connector.fetch(WATCH_URI)

    assert isinstance(result, Error)
    assert result.kind == expected_kind


async def test_missing_metadata_is_not_found() -> None:
    """A ``None`` extraction result is ``Error(NOT_FOUND)``."""
    connector = _connector_with(info=None)

    result = await connector.fetch(WATCH_URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_unexpected_extraction_failure_is_parse_error() -> None:
    """A non-DownloadError failure surfaces as a typed PARSE_ERROR."""
    connector = _connector_with(exc=ValueError("unexpected shape"))

    result = await connector.fetch(WATCH_URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PARSE_ERROR
