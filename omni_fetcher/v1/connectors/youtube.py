"""The ``youtube`` connector for the OmniFetcher v1 canonical contract.

Emits a single ``video`` :class:`~omni_fetcher.v1.node.CompositionNode` per
YouTube video: one ``Video`` atom carrying the intrinsic signal properties
(format/duration/dimensions/fps, referenced by ``uri``), optional ``Text``
atoms for the video description and any *existing* caption track, with all
descriptive fields (title, uploader, view/like counts, tags, ...) namespaced
under ``source_extra["youtube"]`` -- never inline on an atom.

A playlist maps onto a ``playlist`` container node whose children are the
per-entry ``video`` nodes, in playlist order.

Extraction boundary (CRITICAL): caption reading is **read-only**. Only
captions that *already exist* on the video (yt-dlp's manual ``subtitles``)
are surfaced as a transcript ``Text`` atom. There is **no transcription /
ASR**: yt-dlp's ``automatic_captions`` (machine-generated) are never read and
a video with no manual captions simply omits the caption atom -- a transcript
is never synthesised. Metadata extraction reuses the v0.11 yt-dlp logic.

Error mapping: a private video is ``PERMISSION_DENIED``; an unavailable /
removed / nonexistent video is ``NOT_FOUND``; a network blip or timeout is
``TRANSIENT``; an otherwise-unparseable extraction is ``PARSE_ERROR``.
Expected failures are returned as typed ``Error`` results, never raised.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import yt_dlp

from omni_fetcher.v1.atoms import Text, TextFormat, Video
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Result,
    error,
    from_exception,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which all YouTube descriptive fields are stored.
SOURCE_NAMESPACE = "youtube"

# Advisory semantic ``kind`` for a single video node and a playlist node.
VIDEO_KIND = "video"
PLAYLIST_KIND = "playlist"

# Host fragments that mark a URI as a YouTube source.
_YOUTUBE_DOMAINS = (
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
)

# Substrings (lower-cased) in a yt-dlp failure message that classify the
# failure onto a taxonomy kind. Order matters: most specific first.
_MESSAGE_KINDS: tuple[tuple[str, ErrorKind], ...] = (
    ("private video", ErrorKind.PERMISSION_DENIED),
    ("sign in to confirm your age", ErrorKind.PERMISSION_DENIED),
    ("members-only", ErrorKind.PERMISSION_DENIED),
    ("members only", ErrorKind.PERMISSION_DENIED),
    ("video unavailable", ErrorKind.NOT_FOUND),
    ("does not exist", ErrorKind.NOT_FOUND),
    ("has been removed", ErrorKind.NOT_FOUND),
    ("removed by the uploader", ErrorKind.NOT_FOUND),
    ("account associated with this video has been terminated", ErrorKind.NOT_FOUND),
    ("not available", ErrorKind.NOT_FOUND),
    ("unable to download", ErrorKind.TRANSIENT),
    ("timed out", ErrorKind.TRANSIENT),
    ("timeout", ErrorKind.TRANSIENT),
    ("connection", ErrorKind.TRANSIENT),
    ("temporary failure", ErrorKind.TRANSIENT),
)


class YouTubeConnector(BaseFetcher):
    """
    Deterministic YouTube connector for the v1 canonical contract
    ===============================================
    Streams a single ``video`` composition node for a video URI (a ``Video``
    atom plus optional description / existing-caption ``Text`` atoms), or a
    ``playlist`` container node whose children are per-entry ``video`` nodes.
    Descriptive metadata is namespaced under ``source_extra["youtube"]``.
    Caption reading is read-only: only captions that already exist are
    surfaced; nothing is transcribed and machine ``automatic_captions`` are
    never read.
    ===============================================
    NOTE:
        1. Expected failures (private / unavailable video, network blip) are
           returned as typed ``Error`` results, never raised.
        2. A video with no existing captions omits the caption atom; a
           transcript is never synthesised (no ASR).
        3. ``fetch()`` is inherited from :class:`BaseFetcher`; only
           ``stream()`` is overridden.
        4. Authentication is optional and unused for public metadata; a
           credential is accepted transiently and never stored.

    Methods
    -------
        stream:
        can_handle:
    """

    name = "youtube"

    def __init__(self) -> None:
        """
        Create a YouTube connector

        The connector holds no per-call state: yt-dlp options are assembled
        per extraction so nothing (including any credential) is retained on
        the instance.
        """

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether this connector claims a URI

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` if ``uri`` names a YouTube domain.
        """
        lowered = uri.lower()
        return any(domain in lowered for domain in _YOUTUBE_DOMAINS)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical result for a YouTube URI

        Extracts the video (or playlist) metadata via yt-dlp -- read-only,
        no media or caption download -- and yields a single ``Result``: a
        ``success`` ``video`` node (or a ``playlist`` node whose children are
        the per-entry ``video`` nodes). Existing captions become a transcript
        ``Text`` atom; absent captions are simply omitted (no ASR). A private,
        unavailable, or otherwise failing extraction is yielded as a typed
        ``error`` rather than raised.

        Parameters
        ----------
            uri:
                The YouTube video or playlist URL.
            auth:
                The per-call credential, or ``None`` for an unauthenticated
                source. Optional and unused for public metadata; consumed
                transiently and never stored.
            zoom:
                Optional per-atom-type zoom spec; ``None`` means natural
                granularity. Accepted but not consulted: a video's natural
                decomposition is one node.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result`` for the
                bounded YouTube source.
        """
        try:
            info = await asyncio.to_thread(self._extract_info, uri)
        except yt_dlp.utils.DownloadError as exc:  # type: ignore[attr-defined]
            yield error(
                kind=_classify_message(str(exc)),
                message=str(exc),
                locator=uri,
            )
            return
        except Exception as exc:  # noqa: BLE001 - boundary: returned as Error
            yield from_exception(
                exc,
                kind=ErrorKind.PARSE_ERROR,
                message="failed to extract YouTube metadata",
                locator=uri,
            )
            return

        if info is None:
            yield error(
                kind=ErrorKind.NOT_FOUND,
                message="no metadata returned for URI",
                locator=uri,
            )
            return

        yield self._build_result(uri, info)

    def _extract_info(self, uri: str) -> Optional[dict[str, Any]]:
        """Extract metadata for a URI with yt-dlp (read-only, no download).

        Reuses the v0.11 yt-dlp configuration. ``extract_info`` is a blocking
        call, so this runs on a worker thread (see ``stream``). No media,
        caption file, or thumbnail is downloaded.
        """
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(uri, download=False)

    def _build_result(self, uri: str, info: dict[str, Any]) -> Result:
        """Assemble the canonical node (video or playlist) into a Result."""
        if _is_playlist(info):
            return success(self._build_playlist_node(uri, info))
        return success(self._build_video_node(uri, info))

    def _build_playlist_node(
        self,
        uri: str,
        info: dict[str, Any],
    ) -> CompositionNode:
        """Build a ``playlist`` container node with per-entry video children.

        Each non-empty entry is mapped onto its own ``video`` node, in
        playlist order. Entries yt-dlp could not resolve (``None``) are
        skipped rather than fabricated.
        """
        entries = info.get("entries") or []
        children = [
            self._build_video_node(
                entry.get("webpage_url") or entry.get("original_url") or uri,
                entry,
            )
            for entry in entries
            if entry
        ]
        source_fields = _playlist_fields(info)
        return build_node(
            kind=PLAYLIST_KIND,
            children=children,
            id=info.get("id"),
            author=info.get("uploader") or info.get("channel"),
            source_url=info.get("webpage_url") or uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )

    def _build_video_node(
        self,
        uri: str,
        info: dict[str, Any],
    ) -> CompositionNode:
        """Build a single ``video`` node: a Video atom + optional Text atoms.

        The ``Video`` atom carries content-only signal properties and a
        reference ``uri`` to the source page. The description (when present)
        becomes a plain ``Text`` atom; an *existing* caption track (when
        present) becomes a transcript ``Text`` atom. No caption is fabricated.
        """
        atoms: list[Any] = [_build_video_atom(uri, info)]

        description = info.get("description")
        if description and description.strip():
            atoms.append(Text(content=description, format=TextFormat.PLAIN))

        caption = _existing_caption_text(info)
        if caption is not None:
            text, language = caption
            atoms.append(
                Text(
                    content=text,
                    format=TextFormat.TRANSCRIPT,
                    language=language,
                )
            )

        source_fields = _video_fields(info)
        return build_node(
            kind=VIDEO_KIND,
            atoms=atoms,
            id=info.get("id"),
            created=_parse_upload_date(info.get("upload_date")),
            author=info.get("uploader") or info.get("channel"),
            source_url=info.get("webpage_url") or uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )


def _build_video_atom(uri: str, info: dict[str, Any]) -> Video:
    """Build the content-only ``Video`` atom for a video's signal properties.

    Carries a reference ``uri`` (the source page), the container ``format``
    (``ext``), and intrinsic ``duration``/``width``/``height``/``fps`` where
    yt-dlp reports them. Descriptive fields are deliberately excluded -- they
    live in ``Metadata.source_extra``.
    """
    page_url = info.get("webpage_url") or info.get("original_url") or uri
    return Video(
        uri=page_url,
        format=str(info.get("ext") or "mp4"),
        duration_seconds=_positive_number(info.get("duration")),
        width=_positive_int(info.get("width")),
        height=_positive_int(info.get("height")),
        fps=_positive_number(info.get("fps")),
    )


def _existing_caption_text(
    info: dict[str, Any],
) -> Optional[tuple[str, Optional[str]]]:
    """Return the text of an *existing* caption track, or ``None``.

    Reads only yt-dlp's manual ``subtitles`` -- captions that already exist on
    the video. ``automatic_captions`` (machine-generated ASR) are never read.
    yt-dlp does not download the caption file during flat metadata extraction;
    a test/host may stamp a ready-to-use ``content`` onto a subtitle entry.
    When no usable text is present, returns ``None`` so the caller omits the
    caption atom rather than synthesising a transcript.
    """
    subtitles = info.get("subtitles")
    if not isinstance(subtitles, dict) or not subtitles:
        return None

    for language, tracks in subtitles.items():
        if not tracks:
            continue
        for track in tracks:
            if not isinstance(track, dict):
                continue
            content = track.get("content") or track.get("data")
            if isinstance(content, str) and content.strip():
                return content, _normalise_language(language)
    return None


def _video_fields(info: dict[str, Any]) -> dict[str, Any]:
    """Collect a video's descriptive fields for ``source_extra['youtube']``.

    Reuses the v0.11 field set (title, uploader, counts, tags, licence, live /
    private status, thumbnail, categories). Only present values are kept;
    content fields (the media, the description, captions) live in atoms, not
    here.
    """
    categories = info.get("categories") or []
    thumbnails = info.get("thumbnails") or []
    thumbnail = info.get("thumbnail")
    if not thumbnail and thumbnails:
        first = thumbnails[0]
        if isinstance(first, dict):
            thumbnail = first.get("url")

    fields: dict[str, Any] = {
        "video_id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "uploader_url": info.get("uploader_url"),
        "channel": info.get("channel"),
        "channel_id": info.get("channel_id"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "youtube_tags": info.get("tags") or None,
        "category": categories[0] if categories else None,
        "categories": categories or None,
        "license": info.get("license"),
        "is_live": _live_status(info),
        "is_private": info.get("availability") == "private",
        "availability": info.get("availability"),
        "thumbnail": thumbnail,
        "webpage_url": info.get("webpage_url"),
    }
    return {key: value for key, value in fields.items() if value is not None}


def _playlist_fields(info: dict[str, Any]) -> dict[str, Any]:
    """Collect a playlist's descriptive fields for ``source_extra['youtube']``."""
    entries = info.get("entries") or []
    fields: dict[str, Any] = {
        "playlist_id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "channel": info.get("channel"),
        "channel_id": info.get("channel_id"),
        "playlist_count": info.get("playlist_count") or (len(entries) if entries else None),
        "webpage_url": info.get("webpage_url"),
    }
    return {key: value for key, value in fields.items() if value is not None}


def _is_playlist(info: dict[str, Any]) -> bool:
    """Report whether yt-dlp info describes a playlist (vs a single video)."""
    if info.get("_type") == "playlist":
        return True
    return "entries" in info and info.get("entries") is not None


def _live_status(info: dict[str, Any]) -> Optional[bool]:
    """Resolve a video's live status from the yt-dlp info, if known."""
    if info.get("is_live"):
        return True
    status = info.get("live_status")
    if status == "is_live":
        return True
    if status in {"not_live", "was_live", "post_live"}:
        return False
    return None


def _classify_message(message: str) -> ErrorKind:
    """Map a yt-dlp failure message onto a taxonomy ``ErrorKind``.

    A private / age-/members-gated video is ``PERMISSION_DENIED``; a removed,
    nonexistent, or unavailable video is ``NOT_FOUND``; a network/timeout blip
    is ``TRANSIENT``. An unrecognised failure defaults to ``TRANSIENT`` (an
    unknown extraction failure is assumed retryable rather than terminal).
    """
    lowered = message.lower()
    for fragment, kind in _MESSAGE_KINDS:
        if fragment in lowered:
            return kind
    return ErrorKind.TRANSIENT


def _normalise_language(language: Any) -> Optional[str]:
    """Normalise a subtitle language key to a non-empty string or ``None``."""
    if not isinstance(language, str):
        return None
    text = language.strip()
    return text or None


def _positive_number(value: Any) -> Optional[float]:
    """Coerce a value to a positive float, or ``None`` if not usable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 else None


def _positive_int(value: Any) -> Optional[int]:
    """Coerce a value to a positive int, or ``None`` if not usable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if number > 0 else None


def _parse_upload_date(value: Any) -> Optional[datetime]:
    """Parse a yt-dlp ``YYYYMMDD`` upload-date string to a UTC datetime.

    Deterministic and lenient: returns ``None`` for an absent or unparseable
    value rather than raising.
    """
    if not value:
        return None
    text = str(value)
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if not match:
        return None
    try:
        year, month, day = (int(part) for part in match.groups())
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None
