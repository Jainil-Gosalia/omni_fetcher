"""External-behaviour tests for the v1 ``audio`` connector.

These tests exercise only the public surface of the connector: ``stream()``
and the inherited ``fetch()``. They create real temp audio files (a tiny WAV
written via the stdlib :mod:`wave` module, requiring no third-party package and
no network) and assert that the output is a canonical ``CompositionNode`` tree:

- advisory ``kind`` ``"audio_file"``,
- content lives in a single ``Audio`` atom (format + intrinsic signal
  properties only),
- ID3-style descriptive fields live in ``source_extra["audio"]`` and are
  asserted to be *absent* from the atom,
- no transcription is performed (no ``Text``/transcript atom is emitted),
- a missing file returns a typed ``NOT_FOUND`` error, never raising.

No connector internals are touched.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from omni_fetcher.v1.atoms import Atom, AtomKind, Audio
from omni_fetcher.v1.connectors.audio import (
    AUDIO_KIND,
    SOURCE_NAMESPACE,
    AudioConnector,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Error, Success


@pytest.fixture
def fetcher() -> AudioConnector:
    """A fresh connector instance under test."""
    return AudioConnector()


def _write_wav(
    path: Path,
    *,
    channels: int = 2,
    sample_rate: int = 44100,
    frames: int = 44100,
) -> Path:
    """Write a tiny silent WAV file with known signal properties."""
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00\x00" * channels * frames)
    return path


async def _collect(fetcher: AudioConnector, uri: str):
    """Drain ``stream()`` into a list of results (bounded source)."""
    items = []
    async for item in fetcher.stream(uri):
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Canonical Audio atom (content)


async def test_wav_yields_canonical_audio_node(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """A WAV becomes a kind='audio_file' node with one Audio atom."""
    target = _write_wav(tmp_path / "clip.wav")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    tree = result.tree
    assert isinstance(tree, CompositionNode)
    assert tree.metadata.kind == AUDIO_KIND

    atoms = list(tree.iter_atoms())
    assert len(atoms) == 1
    atom = atoms[0]
    assert isinstance(atom, Audio)
    assert atom.kind is AtomKind.AUDIO


async def test_audio_atom_carries_content_signal_fields(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """Format + intrinsic signal properties are read onto the Audio atom."""
    target = _write_wav(
        tmp_path / "tone.wav", channels=2, sample_rate=44100, frames=44100
    )

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    atom = next(result.tree.iter_atoms())
    assert isinstance(atom, Audio)
    assert atom.format == "wav"
    assert atom.channels == 2
    assert atom.sample_rate == 44100
    # 44100 frames at 44100 Hz == 1.0 second.
    assert atom.duration_seconds == pytest.approx(1.0)
    # The bytes are referenced, never inlined for a file source.
    assert atom.uri is not None
    assert atom.data is None


# ---------------------------------------------------------------------------
# Descriptive fields -> metadata, NEVER on the atom


async def test_descriptive_fields_live_in_source_extra_not_atom(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """File descriptors are namespaced in source_extra, not on the atom."""
    target = _write_wav(tmp_path / "song.wav")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    extra = result.tree.metadata.source_extra[SOURCE_NAMESPACE]
    assert extra["name"] == "song.wav"
    assert extra["size"] > 0
    assert "mtime" in extra

    # The Audio atom is content-only: its field set is exactly the canonical
    # Audio shape, with no descriptive ID3 keys (artist/album/year/genre/title)
    # ever leaking onto it.
    atom = next(result.tree.iter_atoms())
    assert isinstance(atom, Audio)
    atom_keys = set(atom.model_dump().keys())
    assert atom_keys == {
        "kind",
        "format",
        "data",
        "uri",
        "duration_seconds",
        "sample_rate",
        "channels",
    }
    for descriptive in ("artist", "album", "year", "genre", "title"):
        assert descriptive not in atom_keys


async def test_atom_construction_rejects_descriptive_fields() -> None:
    """The Audio atom structurally forbids inlined descriptive ID3 fields."""
    with pytest.raises(Exception):
        # ``extra="forbid"`` on the atom rejects descriptive metadata outright.
        Audio(format="mp3", uri="file:///x.mp3", artist="Nobody")  # type: ignore[call-arg]


async def test_source_url_recorded_on_metadata(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """The requested URI is recorded as the node's source_url."""
    target = _write_wav(tmp_path / "x.wav")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    assert result.tree.metadata.source_url == str(target)


# ---------------------------------------------------------------------------
# No transcription performed (metadata-only extraction boundary)


async def test_no_transcription_is_performed(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """Only an Audio atom is emitted -- never a Text/transcript atom."""
    target = _write_wav(tmp_path / "speech.wav")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    # Exactly one atom, and it is Audio: no transcript text was produced.
    assert len(atoms) == 1
    assert all(isinstance(a, Atom) for a in atoms)
    assert all(a.kind is AtomKind.AUDIO for a in atoms)
    assert not any(a.kind is AtomKind.TEXT for a in atoms)


# ---------------------------------------------------------------------------
# Streaming temporal stamping


async def test_stream_stamps_temporal_position(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """A streamed node carries a monotonic sequence + wall-clock timestamp."""
    target = _write_wav(tmp_path / "x.wav")

    items = await _collect(fetcher, str(target))

    assert len(items) == 1
    assert isinstance(items[0], Success)
    temporal = items[0].tree.metadata.temporal
    assert temporal.sequence == 0
    assert temporal.timestamp is not None
    assert temporal.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# Error handling -- returned, never raised


async def test_missing_file_returns_not_found(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """A missing file returns a NOT_FOUND error (no exception raised)."""
    missing = tmp_path / "does_not_exist.mp3"

    result = await fetcher.fetch(str(missing))

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND
    assert result.locator == str(missing)


async def test_missing_file_stream_does_not_raise(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """stream() yields a typed Error for a missing file rather than raising."""
    missing = tmp_path / "nope.wav"

    items = await _collect(fetcher, str(missing))

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind is ErrorKind.NOT_FOUND


async def test_directory_returns_invalid_input(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """Pointing at a directory returns INVALID_INPUT, not a success."""
    result = await fetcher.fetch(str(tmp_path))

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.INVALID_INPUT


async def test_unreadable_wav_returns_parse_error(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """A corrupt .wav file returns a PARSE_ERROR, never raising."""
    target = tmp_path / "broken.wav"
    target.write_bytes(b"not a real wave file at all")

    result = await fetcher.fetch(str(target))

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.PARSE_ERROR


# ---------------------------------------------------------------------------
# URI handling + can_handle


async def test_file_uri_is_accepted(
    fetcher: AudioConnector, tmp_path: Path
) -> None:
    """A file:// URI resolves to the same audio content as a bare path."""
    target = _write_wav(tmp_path / "u.wav")
    uri = target.as_uri()

    result = await fetcher.fetch(uri)

    assert isinstance(result, Success)
    atom = next(result.tree.iter_atoms())
    assert isinstance(atom, Audio)
    assert atom.format == "wav"


def test_can_handle_classifies_uris(tmp_path: Path) -> None:
    """can_handle claims audio file:// URIs and absolute audio paths only."""
    abs_audio = str(tmp_path / "clip.wav")
    abs_text = str(tmp_path / "notes.txt")
    assert AudioConnector.can_handle("file:///tmp/song.mp3") is True
    assert AudioConnector.can_handle(abs_audio) is True
    # Non-audio extensions and relative paths are not claimed.
    assert AudioConnector.can_handle(abs_text) is False
    assert AudioConnector.can_handle("relative/song.mp3") is False
