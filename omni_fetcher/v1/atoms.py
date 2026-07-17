"""Canonical content atoms for the OmniFetcher v1 contract.

Atoms are the small, fixed, governed leaf vocabulary of the canonical
composition tree: ``Text``, ``Image``, ``Audio``, ``Video``, ``Table``.

By contract they carry **content only**. Everything that *describes* an
artifact (author, timestamps, EXIF, ID3, source IDs, permissions, captions
that are really descriptions, etc.) lives in the separate ``Metadata``
channel on the owning ``CompositionNode`` -- never inline on an atom.

See ``PHILOSOPHY.md`` (sections 4 and 5) and the v1 PRD/plan for the
governing principles. The behavioural rule of thumb: if a field answers
"what is the content?" it belongs here; if it answers "what do we know
*about* this content?" it belongs in ``Metadata``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AtomKind(str, Enum):
    """
    The canonical atom vocabulary
    ===============================================
    Enumerates the small, fixed set of content atom types. The set is
    governed by a high bar (see ``PHILOSOPHY.md`` section 4): a new atom is
    added only when content is genuinely reusable across multiple sources.
    ===============================================

    Attributes
    ----------
        TEXT:
            Textual content.
        IMAGE:
            Raster or vector image content.
        AUDIO:
            Audio content.
        VIDEO:
            Video content.
        TABLE:
            Tabular content (rows and columns).
    """

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    TABLE = "table"


class TextFormat(str, Enum):
    """
    Text content format
    ===============================================
    Describes the surface syntax of a ``Text`` atom's content so consumers
    can render or parse it correctly. This is a property of the *content*
    itself (not descriptive metadata), so it lives on the atom.
    ===============================================
    NOTE:
        1. Every member is a positive assertion about the content's syntax,
           made by the connector that produced it. ``OPAQUE`` is the one
           member that asserts nothing, and is therefore the default: an
           unlabelled atom is unknown, never assumed to be prose.
        2. ``omni_fetcher.v1.decompose`` dispatches on this enum to decide
           whether content has a prose structure it can split. A wrong label
           here produces wrong (though still lossless) decomposition, so
           connectors must assert only what they actually know.

    Attributes
    ----------
        OPAQUE:
            Decoded to text, with no claim about surface syntax (e.g. an
            arbitrary broker payload or log line). The default.
        PLAIN:
            Unformatted plain text, positively asserted to be prose.
        MARKDOWN:
            Markdown-formatted text.
        HTML:
            HTML markup.
        RST:
            reStructuredText.
        CODE:
            Source code or a structured serialisation (e.g. JSON).
        TRANSCRIPT:
            A transcript (e.g. of audio/video), as text.
    """

    OPAQUE = "opaque"
    PLAIN = "plain"
    MARKDOWN = "markdown"
    HTML = "html"
    RST = "rst"
    CODE = "code"
    TRANSCRIPT = "transcript"


class Atom(BaseModel):
    """
    Base class for all canonical content atoms
    ===============================================
    Common shape for the five canonical atoms. Subclasses set a fixed
    ``kind`` discriminator and add their content fields. Atoms are frozen
    value objects: they hold content only and never inline descriptive
    metadata.
    ===============================================
    NOTE:
        1. ``kind`` is a discriminator used by the ``CompositionNode`` union.
        2. Atoms are immutable (``frozen=True``) to reinforce that they are
           value-typed content.
        3. ``extra="forbid"`` is the structural enforcement of the
           content-vs-metadata split: an attempt to attach a descriptive
           field (``author``, ``id``, ``created``, EXIF/ID3 tags, ...) to an
           atom is rejected at construction. Descriptive fields belong on
           ``Metadata`` only.

    Attributes
    ----------
        kind:
            The atom's canonical type discriminator.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AtomKind


class BinaryAtom(Atom):
    """
    Base for binary-content atoms (image / audio / video)
    ===============================================
    Shared shape for atoms whose content is opaque bytes that may be carried
    inline (``data``) or referenced (``uri``). Enforces the content-location
    invariant: exactly one of ``data`` / ``uri`` must be populated, so a
    producer can never emit a binary atom with no content and never emit one
    with two conflicting sources of truth.
    ===============================================
    NOTE:
        1. ``format`` is a property of the content (container/codec), not
           descriptive metadata, so it lives here.

    Attributes
    ----------
        format:
            Container/codec format of the binary content.
        data:
            Raw bytes, when carried inline.
        uri:
            A reference to the bytes, when not carried inline.
    """

    format: str
    data: Optional[bytes] = None
    uri: Optional[str] = None

    @model_validator(mode="after")
    def _require_exactly_one_location(self) -> "BinaryAtom":
        """Require exactly one of ``data`` / ``uri`` to be set."""
        has_data = self.data is not None
        has_uri = self.uri is not None and self.uri != ""
        if has_data == has_uri:
            raise ValueError("binary atom must carry exactly one of 'data' or 'uri'")
        return self


class Text(Atom):
    """
    Text atom
    ===============================================
    A unit of textual content.
    ===============================================
    NOTE:
        1. ``format`` defaults to ``TextFormat.OPAQUE`` -- "we make no claim
           about this content's syntax" -- so an atom is never *assumed* to
           be prose. A connector that knows its content is prose (or
           markdown, HTML, ...) must say so explicitly; that assertion is
           what licenses ``decompose`` to split it.

    Attributes
    ----------
        kind:
            Always ``AtomKind.TEXT``.
        content:
            The text itself.
        format:
            Surface syntax of ``content`` (plain, markdown, code, ...).
            Defaults to ``OPAQUE``: unknown unless asserted.
        language:
            Optional BCP-47 / ISO language hint for the content.
        encoding:
            Optional character encoding of the source text.
    """

    kind: Literal[AtomKind.TEXT] = AtomKind.TEXT
    content: str
    format: TextFormat = TextFormat.OPAQUE
    language: Optional[str] = None
    encoding: Optional[str] = None


class Image(BinaryAtom):
    """
    Image atom
    ===============================================
    Image content. Carries the bytes (or a reference) and intrinsic
    rendering dimensions only. EXIF, camera, GPS, photographer, licence and
    page-context fields are descriptive and live in ``Metadata.source_extra``
    -- not here.
    ===============================================
    NOTE:
        1. Exactly one of ``data`` or ``uri`` must be populated; this is
           enforced by ``BinaryAtom``.

    Attributes
    ----------
        kind:
            Always ``AtomKind.IMAGE``.
        format:
            Image container/codec format (e.g. ``png``, ``jpeg``).
        data:
            Raw image bytes, when carried inline.
        uri:
            A reference to the image bytes, when not carried inline.
        width:
            Intrinsic pixel width, if known (must be positive).
        height:
            Intrinsic pixel height, if known (must be positive).
    """

    kind: Literal[AtomKind.IMAGE] = AtomKind.IMAGE
    width: Optional[int] = Field(default=None, gt=0)
    height: Optional[int] = Field(default=None, gt=0)


class Audio(BinaryAtom):
    """
    Audio atom
    ===============================================
    Audio content. Carries the bytes (or a reference) and intrinsic signal
    properties only. ID3/artist/album/track/genre and other descriptive tags
    live in ``Metadata.source_extra`` -- not here. Transcription is a Phase-2
    concern and is not produced by the deterministic core.
    ===============================================
    NOTE:
        1. Exactly one of ``data`` or ``uri`` must be populated; this is
           enforced by ``BinaryAtom``.

    Attributes
    ----------
        kind:
            Always ``AtomKind.AUDIO``.
        format:
            Audio container/codec format (e.g. ``mp3``, ``wav``).
        data:
            Raw audio bytes, when carried inline.
        uri:
            A reference to the audio bytes, when not carried inline.
        duration_seconds:
            Playback duration in seconds, if known (non-negative).
        sample_rate:
            Sampling rate in Hz, if known (must be positive).
        channels:
            Number of audio channels, if known (must be positive).
    """

    kind: Literal[AtomKind.AUDIO] = AtomKind.AUDIO
    duration_seconds: Optional[float] = Field(default=None, ge=0)
    sample_rate: Optional[int] = Field(default=None, gt=0)
    channels: Optional[int] = Field(default=None, gt=0)


class Video(BinaryAtom):
    """
    Video atom
    ===============================================
    Video content. Carries the bytes (or a reference) and intrinsic signal
    properties only. Descriptive fields live in ``Metadata.source_extra``.
    Any embedded audio/caption decomposition is expressed structurally via
    child ``CompositionNode``s, not inline here.
    ===============================================
    NOTE:
        1. Exactly one of ``data`` or ``uri`` must be populated; this is
           enforced by ``BinaryAtom``.

    Attributes
    ----------
        kind:
            Always ``AtomKind.VIDEO``.
        format:
            Video container/codec format (e.g. ``mp4``, ``webm``).
        data:
            Raw video bytes, when carried inline.
        uri:
            A reference to the video bytes, when not carried inline.
        duration_seconds:
            Playback duration in seconds, if known (non-negative).
        width:
            Intrinsic pixel width, if known (must be positive).
        height:
            Intrinsic pixel height, if known (must be positive).
        fps:
            Frames per second, if known (must be positive).
    """

    kind: Literal[AtomKind.VIDEO] = AtomKind.VIDEO
    duration_seconds: Optional[float] = Field(default=None, ge=0)
    width: Optional[int] = Field(default=None, gt=0)
    height: Optional[int] = Field(default=None, gt=0)
    fps: Optional[float] = Field(default=None, gt=0)


class Table(Atom):
    """
    Table atom
    ===============================================
    Tabular content: an ordered list of rows, with optional header labels.
    Cells are arbitrary scalar values. Sheet/workbook names, provenance and
    other descriptive data live in ``Metadata`` -- not here.
    ===============================================
    NOTE:
        1. A single ``Table`` atom is one logical grid. A multi-sheet
           workbook is expressed as a ``CompositionNode`` with one ``Table``
           child per sheet, each sheet's name carried in that node's
           ``Metadata``.

    Attributes
    ----------
        kind:
            Always ``AtomKind.TABLE``.
        headers:
            Optional ordered column header labels.
        rows:
            Ordered rows; each row is an ordered list of cell values.
    """

    kind: Literal[AtomKind.TABLE] = AtomKind.TABLE
    headers: Optional[list[str]] = None
    rows: list[list[Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rows_match_headers(self) -> "Table":
        """Require every row's width to match the header count."""
        if self.headers is None:
            return self
        width = len(self.headers)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(
                    f"row {index} has {len(row)} cells; expected {width} to match headers"
                )
        return self


# Discriminated union over the canonical atom set. The ``kind`` field is the
# discriminator, so a ``CompositionNode`` can hold any atom and round-trip
# cleanly through (de)serialisation.
AnyAtom = Union[Text, Image, Audio, Video, Table]
