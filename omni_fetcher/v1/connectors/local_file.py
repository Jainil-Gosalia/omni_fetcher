"""The ``local_file`` connector for the OmniFetcher v1 contract.

Reads a single file from the local filesystem and maps it onto the canonical
contract: a ``CompositionNode`` of advisory ``kind`` ``"file"`` carrying one
content atom, wrapped in a ``Result``. Text-like files become a ``Text`` atom
(plain / markdown / html / code by detected type); CSV/TSV-like files become a
``Table`` atom. Descriptive fields (path, size, mime, mtime, encoding) live in
the namespaced ``source_extra["local_file"]`` mapping -- never inline on the
atom.

This connector is the spine tracer: deterministic, read-only, and needs no
real auth (the ``auth`` parameter is ignored). It reuses the v0.11
``local_file`` fetcher's URI-to-path resolution, MIME detection, and
encoding-fallback parsing logic; only the *output* is re-mapped to canonical
nodes. Expected failures (missing file, not-a-file, unreadable bytes) are
returned as typed ``Error`` / ``Partial`` values, never raised.
"""

from __future__ import annotations

import csv
import io
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional, Union
from urllib.parse import unquote, urlparse

from omni_fetcher.v1.atoms import Table, Text, TextFormat
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
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace under which all descriptive ``local_file`` fields are stored
# in ``Metadata.source_extra``.
SOURCE_NAMESPACE = "local_file"

# Advisory semantic ``kind`` for every node this connector emits.
FILE_KIND = "file"

# MIME types parsed into a ``Table`` atom rather than a ``Text`` atom.
_CSV_MIME = "text/csv"
_TSV_MIMES = frozenset({"text/tab-separated-values", "text/tsv"})

# Map a text-ish MIME type onto the canonical ``TextFormat`` for its content.
_TEXT_FORMATS: dict[str, TextFormat] = {
    "text/markdown": TextFormat.MARKDOWN,
    "text/html": TextFormat.HTML,
    "text/x-rst": TextFormat.RST,
}


def _uri_to_path(uri: str) -> str:
    """Resolve a ``file://`` URI or bare path to a filesystem path string.

    A ``file://`` URI is parsed and percent-decoded into a local path
    (handling the ``localhost`` host form and Windows drive paths such as
    ``file:///C:/...``, where ``urlparse`` leaves a leading slash before the
    drive letter). A bare path is returned untouched.
    """
    if not uri.startswith("file://"):
        return uri
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    # On Windows a drive path comes through as ``/C:/...``; drop the leading
    # slash so it is a usable filesystem path.
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path


def _guess_mime_type(path: str) -> Optional[str]:
    """Guess a MIME type from a path's extension (reused v0.11 logic)."""
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type


def _read_text(path: Path) -> tuple[str, str]:
    """Read a file as text, falling back from UTF-8 to latin-1.

    Returns the decoded content and the encoding actually used. Mirrors the
    v0.11 fetcher's decode-fallback behaviour.
    """
    try:
        return path.read_text(encoding="utf-8"), "utf-8"
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1"), "latin-1"


def _text_format_for(mime_type: Optional[str], path: Path) -> TextFormat:
    """Pick the canonical ``TextFormat`` for a text-like file."""
    if mime_type in _TEXT_FORMATS:
        return _TEXT_FORMATS[mime_type]
    if mime_type and mime_type.startswith("text/") and mime_type != "text/plain":
        # An unmapped ``text/*`` subtype (e.g. ``text/x-python``) is source
        # code as far as the canonical vocabulary is concerned.
        return TextFormat.CODE
    return TextFormat.PLAIN


def _is_tabular(mime_type: Optional[str], path: Path) -> bool:
    """Report whether a file should be parsed into a ``Table`` atom."""
    if mime_type == _CSV_MIME or mime_type in _TSV_MIMES:
        return True
    # ``mimetypes`` does not always know ``.tsv``; fall back to the suffix.
    return path.suffix.lower() in {".csv", ".tsv"}


def _parse_table(text: str, path: Path, mime_type: Optional[str]) -> Table:
    """Parse delimited text into a canonical ``Table`` atom.

    The first row is treated as headers when every subsequent row matches its
    width; otherwise the grid is emitted header-less so the ``Table``
    width-invariant is never violated.
    """
    delimiter = "\t" if (path.suffix.lower() == ".tsv" or mime_type in _TSV_MIMES) else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    grid = [list(row) for row in reader]
    if not grid:
        return Table(headers=None, rows=[])
    headers = grid[0]
    body = grid[1:]
    if body and all(len(row) == len(headers) for row in body):
        return Table(headers=headers, rows=body)
    # Ragged rows: emit header-less so no row/header width mismatch occurs.
    return Table(headers=None, rows=grid)


def _source_fields(
    path: Path, stat: os.stat_result, mime_type: Optional[str], encoding: Optional[str]
) -> dict[str, object]:
    """Assemble the namespaced descriptive fields for a file node."""
    fields: dict[str, object] = {
        "path": str(path),
        "name": path.name,
        "size": stat.st_size,
        "mime_type": mime_type,
        "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }
    if encoding is not None:
        fields["encoding"] = encoding
    return fields


class LocalFileFetcher(BaseFetcher):
    """
    Canonical v1 connector for local filesystem files
    ===============================================
    Reads one file and yields a single canonical ``CompositionNode`` of
    ``kind`` ``"file"`` carrying one content atom (``Text`` for text-like
    files, ``Table`` for CSV/TSV-like files). Descriptive fields live in the
    namespaced ``source_extra["local_file"]`` mapping. Deterministic and
    read-only; ignores ``auth``.
    ===============================================
    NOTE:
        1. Expected failures are returned as typed ``Result`` values
           (``NOT_FOUND`` for a missing path, ``INVALID_INPUT`` for a
           non-file, ``PARSE_ERROR`` for unreadable bytes), never raised.
        2. A binary file with no text/table representation yields a
           ``partial`` node (empty ``Text`` atom + an ``UNSUPPORTED`` gap)
           rather than a silently-empty success.

    Methods
    -------
        stream:
        can_handle:
    """

    name = SOURCE_NAMESPACE

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether ``uri`` names a local file

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` for a ``file://`` URI or an absolute filesystem path.
        """
        return uri.startswith("file://") or os.path.isabs(uri)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical node for a local file (the primitive)

        Resolves ``uri`` to a path, detects its type, reads/parses its content
        with the v0.11 encoding-fallback logic, and yields exactly one
        ``Result`` carrying a ``kind`` ``"file"`` node. The node is stamped
        with a per-stream sequence and a wall-clock timestamp.

        NOTE:
            1. ``auth`` is ignored -- local files need no credential.
            2. Exactly one ``Result`` is yielded; expected failures are
               yielded as typed ``Error`` / ``Partial`` values, never raised.

        Parameters
        ----------
            uri:
                The ``file://`` URI or absolute path to read.
            auth:
                Ignored.
            zoom:
                Honored for text: finer-than-natural text levels decompose
                the file's Text atoms into section/paragraph/sentence child
                nodes. Other atom kinds keep their natural granularity.

        Return
        ------
            results:
                A bounded async iterator yielding one ``Result``.
        """
        counter = SequenceCounter()
        # Zoom is applied centrally (BaseFetcher.fetch / orchestrator.stream).
        result = self._read_one(uri)
        if not isinstance(result, Error):
            # Success / Partial both carry a ``tree`` to stamp; an Error has
            # no node to order.
            stamp_temporal(result.tree, sequence=counter.next(), timestamp=now_utc())
        yield result

    def _read_one(self, uri: str) -> Result:
        """Resolve, read, and map one file to a single ``Result`` value."""
        path_str = _uri_to_path(uri)
        try:
            path = Path(path_str).resolve()
        except OSError as exc:  # pragma: no cover - exotic path errors.
            return from_exception(exc, kind=ErrorKind.INVALID_INPUT, locator=uri)

        if not path.exists():
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"file not found: {path_str}",
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

        mime_type = _guess_mime_type(str(path))
        return self._build_file_node(uri, path, stat, mime_type)

    def _build_file_node(
        self,
        uri: str,
        path: Path,
        stat: os.stat_result,
        mime_type: Optional[str],
    ) -> Result:
        """Read content and assemble the canonical ``"file"`` node."""
        is_text = mime_type is None or mime_type.startswith("text/")
        tabular = _is_tabular(mime_type, path)

        if not is_text and not tabular:
            # Binary content with no canonical text/table representation: be
            # explicit about the gap rather than emit a silent empty success.
            node = build_node(
                kind=FILE_KIND,
                atoms=[Text(content="", format=TextFormat.OPAQUE)],
                source_url=uri,
                updated=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                source_namespace=SOURCE_NAMESPACE,
                source_fields=_source_fields(path, stat, mime_type, None),
            )
            return partial(
                node,
                [
                    gap(
                        kind=ErrorKind.UNSUPPORTED,
                        locator=uri,
                        detail=f"binary content not decoded ({mime_type})",
                    )
                ],
            )

        try:
            text, encoding = _read_text(path)
        except (UnicodeError, OSError) as exc:
            return from_exception(exc, kind=ErrorKind.PARSE_ERROR, locator=uri)

        atom: Union[Table, Text]
        if tabular:
            try:
                atom = _parse_table(text, path, mime_type)
            except (csv.Error, ValueError) as exc:
                return from_exception(exc, kind=ErrorKind.PARSE_ERROR, locator=uri)
        else:
            atom = Text(
                content=text,
                format=_text_format_for(mime_type, path),
                encoding=encoding,
            )

        node = build_node(
            kind=FILE_KIND,
            atoms=[atom],
            source_url=uri,
            updated=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            source_namespace=SOURCE_NAMESPACE,
            source_fields=_source_fields(path, stat, mime_type, encoding),
        )
        return success(node)
