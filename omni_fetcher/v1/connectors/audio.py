"""The ``audio`` connector for the OmniFetcher v1 canonical contract.

Maps a single audio file on the local filesystem onto the canonical
composition tree: a ``CompositionNode`` of advisory ``kind`` ``"audio_file"``
carrying exactly one canonical ``Audio`` atom, wrapped in a ``Result``.

The split between content and description is strict (see ``atoms.py`` and
``PHILOSOPHY.md`` sections 4 and 5):

- The ``Audio`` atom carries **content only** -- the container ``format`` plus
  intrinsic signal properties (``duration_seconds``, ``sample_rate``,
  ``channels``) and a ``uri`` reference to the bytes.
- Everything *descriptive* -- ID3-style ``artist`` / ``album`` / ``year`` /
  ``genre`` / ``title`` tags, plus file ``name`` / ``size`` / ``mime_type`` /
  ``mtime`` -- lives in the metadata channel: the common core (``author`` for
  the artist) and the namespaced ``source_extra["audio"]`` mapping. These
  descriptive fields are **never** inlined onto the atom.

Extraction is metadata-only and deterministic: signal properties for WAV
files come from the stdlib :mod:`wave` reader; richer container/codec metadata
and ID3 tags are read via the optional :mod:`mutagen` package when it is
installed, and are simply omitted otherwise. **No transcription / ASR is ever
performed** -- that is a separate, non-deterministic concern outside this
connector's boundary. The connector is read-only and ignores ``auth`` (local
audio files need no credential).

Expected failures are returned as typed ``Result`` values, never raised: a
missing path is ``NOT_FOUND``, a non-file path is ``INVALID_INPUT``, and an
unreadable/corrupt file is ``PARSE_ERROR``.
"""

from __future__ import annotations

import contextlib
import mimetypes
import os
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import unquote, urlparse

from omni_fetcher.v1.atoms import Audio
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import (
    SequenceCounter,
    build_node,
    now_utc,
    stamp_temporal,
)
from omni_fetcher.v1.result import (
    Error,
    Result,
    error,
    from_exception,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which all descriptive ``audio`` fields are filed in
# ``Metadata.source_extra``.
SOURCE_NAMESPACE = "audio"

# Advisory semantic ``kind`` for every node this connector emits.
AUDIO_KIND = "audio_file"

# Recognised audio file extensions (lower-case, with leading dot).
_AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"})

# ID3-style descriptive tag keys (in ``source_extra["audio"]``). These are
# descriptive, not content, so they never touch the ``Audio`` atom.
_DESCRIPTIVE_TAG_KEYS = ("title", "artist", "album", "year", "genre")


def _uri_to_path(uri: str) -> str:
    """Resolve a ``file://`` URI or a bare path to a filesystem path string.

    A ``file://`` URI is parsed and percent-decoded into a local path
    (handling the ``localhost`` host form and Windows drive paths such as
    ``file:///C:/...``, where ``urlparse`` leaves a leading slash before the
    drive letter). A bare path is returned untouched.
    """
    if not uri.startswith("file://"):
        return uri
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path


def _format_for(path: Path, mime_type: Optional[str]) -> str:
    """Derive the audio container ``format`` from extension/MIME.

    The file extension is authoritative for the canonical format token (e.g.
    ``mp3``, ``wav``); the MIME subtype is the fallback. ``unknown`` is used
    when neither is informative.
    """
    suffix = path.suffix.lower().lstrip(".")
    if suffix:
        return suffix
    if mime_type and "/" in mime_type:
        return mime_type.split("/")[-1]
    return "unknown"


def _read_wave_signal(path: Path) -> dict[str, Any]:
    """Read intrinsic signal properties from a WAV file via stdlib ``wave``.

    Returns a mapping with any of ``duration_seconds`` / ``sample_rate`` /
    ``channels`` that could be determined. Raises on a malformed WAV so the
    caller can map it to a ``PARSE_ERROR``.
    """
    with contextlib.closing(wave.open(str(path), "rb")) as reader:
        channels = reader.getnchannels()
        sample_rate = reader.getframerate()
        frames = reader.getnframes()
        signal: dict[str, Any] = {}
        if channels > 0:
            signal["channels"] = channels
        if sample_rate > 0:
            signal["sample_rate"] = sample_rate
            signal["duration_seconds"] = round(frames / float(sample_rate), 6)
        return signal


def _read_mutagen_metadata(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read signal + descriptive metadata via the optional ``mutagen`` package.

    Returns a ``(signal, tags)`` pair: ``signal`` holds intrinsic content
    properties (duration/sample_rate/channels) for the ``Audio`` atom, ``tags``
    holds ID3-style descriptive fields for ``source_extra``. When ``mutagen``
    is not installed, or the file carries no readable metadata, both mappings
    are empty -- this is metadata-only and never raises for a missing optional
    dependency.
    """
    try:
        import mutagen  # type: ignore[import-not-found]
    except ImportError:
        return {}, {}

    audio = mutagen.File(str(path), easy=True)  # type: ignore[attr-defined]
    if audio is None:
        return {}, {}

    signal: dict[str, Any] = {}
    info = getattr(audio, "info", None)
    if info is not None:
        length = getattr(info, "length", None)
        if isinstance(length, (int, float)) and length >= 0:
            signal["duration_seconds"] = round(float(length), 6)
        sample_rate = getattr(info, "sample_rate", None)
        if isinstance(sample_rate, int) and sample_rate > 0:
            signal["sample_rate"] = sample_rate
        channels = getattr(info, "channels", None)
        if isinstance(channels, int) and channels > 0:
            signal["channels"] = channels

    tags: dict[str, Any] = {}
    # ``easy=True`` exposes ID3/Vorbis tags as a plain str->list[str] mapping
    # under a small common vocabulary, so we read them uniformly across formats.
    raw_tags = dict(audio) if hasattr(audio, "keys") else {}
    key_map = {
        "title": "title",
        "artist": "artist",
        "album": "album",
        "date": "year",
        "genre": "genre",
    }
    for raw_key, out_key in key_map.items():
        value = raw_tags.get(raw_key)
        if isinstance(value, list) and value:
            value = value[0]
        if value:
            tags[out_key] = str(value)
    return signal, tags


def _source_fields(
    path: Path,
    stat: os.stat_result,
    mime_type: Optional[str],
    tags: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the namespaced descriptive fields for an audio node.

    File-level descriptors (name/size/mime/mtime) are merged with the ID3-style
    descriptive tags (title/artist/album/year/genre). All of this is
    *descriptive*, so it lives here in ``source_extra``, never on the atom.
    """
    fields: dict[str, Any] = {
        "name": path.name,
        "size": stat.st_size,
        "mime_type": mime_type,
        "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }
    for key in _DESCRIPTIVE_TAG_KEYS:
        if key in tags:
            fields[key] = tags[key]
    return fields


class AudioConnector(BaseFetcher):
    """
    Canonical v1 connector for local audio files
    ===============================================
    Reads one audio file and yields a single canonical ``CompositionNode`` of
    advisory ``kind`` ``"audio_file"`` carrying exactly one ``Audio`` atom. The
    atom holds content only (format + intrinsic signal properties); ID3-style
    descriptive tags and file descriptors live in the metadata core and the
    namespaced ``source_extra["audio"]`` mapping. Deterministic and read-only;
    ignores ``auth`` and performs no transcription.
    ===============================================
    NOTE:
        1. Only *metadata* is extracted -- never transcription/ASR.
        2. Signal properties come from the stdlib ``wave`` reader (WAV) and the
           optional ``mutagen`` package (other formats + ID3 tags); when
           ``mutagen`` is absent, signal/tag fields are simply omitted.
        3. Expected failures are returned as typed ``Result`` values
           (``NOT_FOUND`` for a missing path, ``INVALID_INPUT`` for a non-file,
           ``PARSE_ERROR`` for an unreadable/corrupt file), never raised.

    Methods
    -------
        stream:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether ``uri`` names a local audio file

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for a ``file://`` URI or an absolute filesystem path
                whose extension is a recognised audio extension.
        """
        if uri.startswith("file://"):
            candidate = _uri_to_path(uri)
        elif os.path.isabs(uri):
            candidate = uri
        else:
            return False
        return Path(candidate).suffix.lower() in _AUDIO_EXTENSIONS

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical node for a local audio file (the primitive)

        Resolves ``uri`` to a path, extracts audio metadata (signal properties
        + ID3-style tags) without performing any transcription, and yields
        exactly one ``Result`` carrying a ``kind`` ``"audio_file"`` node. The
        node is stamped with a per-stream sequence and a wall-clock timestamp.

        NOTE:
            1. ``auth`` is ignored -- local audio files need no credential.
            2. Exactly one ``Result`` is yielded; expected failures are yielded
               as typed ``Error`` values, never raised.

        Parameters
        ----------
            uri:
                The ``file://`` URI or absolute path to read.
            auth:
                Ignored.
            zoom:
                Ignored; a single audio file is its own natural granularity.

        Return
        ------
            results:
                A bounded async iterator yielding one ``Result``.
        """
        counter = SequenceCounter()
        result = self._read_one(uri)
        if not isinstance(result, Error):
            # Success / Partial both carry a ``tree`` to stamp; an Error has no
            # node to order.
            stamp_temporal(result.tree, sequence=counter.next(), timestamp=now_utc())
        yield result

    def _read_one(self, uri: str) -> Result:
        """Resolve, read metadata for, and map one audio file to a ``Result``."""
        path_str = _uri_to_path(uri)
        try:
            path = Path(path_str).resolve()
        except OSError as exc:  # pragma: no cover - exotic path errors.
            return from_exception(exc, kind=ErrorKind.INVALID_INPUT, locator=uri)

        if not path.exists():
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"audio file not found: {path_str}",
                locator=uri,
            )
        if not path.is_file():
            return error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"not a regular file: {path_str}",
                locator=uri,
            )

        try:
            stat = path.stat()
        except OSError as exc:
            return from_exception(exc, locator=uri)

        mime_type, _ = mimetypes.guess_type(str(path))
        return self._build_audio_node(uri, path, stat, mime_type)

    def _build_audio_node(
        self,
        uri: str,
        path: Path,
        stat: os.stat_result,
        mime_type: Optional[str],
    ) -> Result:
        """Extract metadata and assemble the canonical ``"audio_file"`` node."""
        audio_format = _format_for(path, mime_type)

        # Intrinsic signal properties (content) + descriptive tags. WAV signal
        # comes from stdlib ``wave``; everything else (and ID3 tags) from the
        # optional ``mutagen`` package when present. Never any transcription.
        signal: dict[str, Any] = {}
        tags: dict[str, Any] = {}
        try:
            if path.suffix.lower() == ".wav":
                signal = _read_wave_signal(path)
            else:
                signal, tags = _read_mutagen_metadata(path)
        except wave.Error as exc:
            return from_exception(exc, kind=ErrorKind.PARSE_ERROR, locator=uri)
        except (OSError, ValueError, EOFError) as exc:
            return from_exception(exc, kind=ErrorKind.PARSE_ERROR, locator=uri)

        # The atom carries content only: format + signal + a uri reference to
        # the bytes. Descriptive tags are deliberately excluded here.
        atom = Audio(
            format=audio_format,
            uri=path.as_uri(),
            duration_seconds=signal.get("duration_seconds"),
            sample_rate=signal.get("sample_rate"),
            channels=signal.get("channels"),
        )

        # The artist promotes to the common-core ``author`` field; the title is
        # left in ``source_extra`` (there is no canonical core title field).
        author = tags.get("artist")

        node = build_node(
            kind=AUDIO_KIND,
            atoms=[atom],
            source_url=uri,
            author=author,
            updated=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            source_namespace=SOURCE_NAMESPACE,
            source_fields=_source_fields(path, stat, mime_type, tags),
        )
        return success(node)
