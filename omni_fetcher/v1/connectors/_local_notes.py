"""Shared base for the local knowledge-base connectors (Obsidian, Logseq, Markdown).

These three sources all address a **local** Markdown/Org vault: a single note by
path (a ``kind="note"`` node) or a folder (a ``kind="collection"`` of notes).
They differ only in their URI scheme, their ``source_extra`` namespace, and
which subfolders a vault walk considers -- so the whole behaviour lives here as
``LocalNotesConnector`` and each connector is a thin subclass setting three
class attributes. All note parsing / node building is delegated to the family's
``_wiki_notes`` spec; this module owns only the local-filesystem addressing and
walk.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import unquote

from omni_fetcher.v1.atoms import TextFormat
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.connectors._wiki_notes import (
    build_vault_collection,
    note_node_from_file,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import SequenceCounter, now_utc, stamp_temporal
from omni_fetcher.v1.result import Error, Result, error, from_exception, success
from omni_fetcher.v1.zoom import ZoomSpec

# Note file extensions and their canonical ``TextFormat``. Markdown-family files
# are asserted Markdown; Org files carry structure that is not Markdown, so they
# are the honest ``PLAIN`` (their raw text is preserved losslessly either way).
_MARKDOWN_EXTS = frozenset({".md", ".markdown", ".mdx"})
_ORG_EXTS = frozenset({".org"})
_NOTE_EXTS = _MARKDOWN_EXTS | _ORG_EXTS

# Default cap on notes in a single vault fetch, so a large vault degrades to an
# honest Partial rather than an unbounded read.
_DEFAULT_NOTE_CAP = 500


def _uri_to_path(raw: str) -> str:
    """Resolve a scheme-stripped path part to a filesystem path.

    Mirrors the ``sqlite`` / ``duckdb`` connectors' file-URI slash handling so a
    Windows drive path, an absolute POSIX path, and a relative path all work.
    """
    path = unquote(raw)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        return path[1:]
    if path.startswith("//"):
        return "/" + path.lstrip("/")
    return path


def _text_format_for(path: Path) -> TextFormat:
    """Pick the canonical ``TextFormat`` for a note file by extension."""
    return TextFormat.MARKDOWN if path.suffix.lower() in _MARKDOWN_EXTS else TextFormat.PLAIN


def _walk_notes(
    root: Path, subdirs: Optional[tuple[str, ...]], cap: int
) -> tuple[list[Path], bool]:
    """Collect note files under ``root`` (or its ``subdirs``), sorted and capped.

    When ``subdirs`` is given, only those that exist under ``root`` are walked
    (Logseq's ``pages``/``journals``); if none exist, the whole ``root`` is
    walked as a fallback. Returns ``(paths, truncated)``.
    """
    search_roots: list[Path] = []
    if subdirs:
        search_roots = [root / name for name in subdirs if (root / name).is_dir()]
    if not search_roots:
        search_roots = [root]

    found: list[Path] = []
    for search_root in search_roots:
        for path in sorted(search_root.rglob("*")):
            if path.is_file() and path.suffix.lower() in _NOTE_EXTS:
                found.append(path)

    truncated = len(found) > cap
    return found[:cap], truncated


def _map_os_error(exc: BaseException) -> ErrorKind:
    """Map a filesystem/decode error onto the v1 taxonomy."""
    if isinstance(exc, FileNotFoundError):
        return ErrorKind.NOT_FOUND
    if isinstance(exc, PermissionError):
        return ErrorKind.PERMISSION_DENIED
    if isinstance(exc, UnicodeDecodeError):
        return ErrorKind.PARSE_ERROR
    return ErrorKind.TRANSIENT


class LocalNotesConnector(BaseFetcher):
    """
    Base connector for a local Markdown/Org vault (note or folder)
    ===============================================
    Fetches a single note (``kind="note"``) or a whole folder
    (``kind="collection"`` of notes) from a local vault. Subclasses set
    ``SCHEME``, ``NAMESPACE``, and (optionally) ``SUBDIRS``; all parsing and
    node building is delegated to the ``_wiki_notes`` spec.
    ===============================================
    NOTE:
        1. Implements only ``stream()``; ``fetch()`` is inherited.
        2. A folder is fetched as a capped collection (``NOTE_CAP``); over the
           cap degrades to a ``Partial`` with a typed gap, never a silent drop.
        3. Local files need no credential; ``auth``/``zoom`` are accepted for
           protocol conformance.

    Methods
    -------
        stream:
        can_handle:
    """

    # Subclass contract.
    SCHEME: str = ""
    NAMESPACE: str = ""
    SUBDIRS: Optional[tuple[str, ...]] = None
    NOTE_CAP: int = _DEFAULT_NOTE_CAP

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Report whether ``uri`` uses this connector's scheme."""
        return uri.startswith(cls.SCHEME)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical result for one note or vault folder

        Yields exactly one ``Result``: a ``Success`` note/collection, a
        ``Partial`` when a vault exceeded the note cap, or a typed ``Error``
        (bad URI, missing path, unreadable/undecodable file).

        Parameters
        ----------
            uri:
                The ``<scheme>://<path>`` source URI.
            auth:
                Unused; accepted for protocol conformance.
            zoom:
                Unused; accepted for protocol conformance.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        del auth, zoom

        try:
            path = self._parse(uri)
        except ValueError as exc:
            yield error(ErrorKind.INVALID_INPUT, message=str(exc), locator=uri)
            return

        if not path.exists():
            yield error(ErrorKind.NOT_FOUND, message=f"path not found: {path}", locator=uri)
            return

        try:
            result = await asyncio.to_thread(self._build, uri, path)
        except (OSError, UnicodeDecodeError) as exc:
            yield from_exception(exc, kind=_map_os_error(exc), locator=uri)
            return

        if not isinstance(result, Error):
            stamp_temporal(result.tree, sequence=SequenceCounter().next(), timestamp=now_utc())
        yield result

    def _parse(self, uri: str) -> Path:
        """Resolve a ``<scheme>://<path>`` URI to a filesystem path."""
        if not uri.startswith(self.SCHEME):
            raise ValueError(f"not a {self.SCHEME} URI: {uri}")
        raw = uri[len(self.SCHEME) :]
        raw, _, _query = raw.partition("?")
        path_str = _uri_to_path(raw)
        if not path_str:
            raise ValueError(f"{self.SCHEME} URI carries no path: {uri}")
        return Path(path_str)

    def _build(self, uri: str, path: Path) -> Result:
        """Build the note or vault-collection result (runs on a worker thread)."""
        if path.is_dir():
            paths, truncated = _walk_notes(path, self.SUBDIRS, self.NOTE_CAP)
            children = [
                note_node_from_file(
                    note_path,
                    uri=f"{self.SCHEME}{note_path.as_posix()}",
                    namespace=self.NAMESPACE,
                    text_format=_text_format_for(note_path),
                )
                for note_path in paths
            ]
            return build_vault_collection(
                uri=uri,
                namespace=self.NAMESPACE,
                notes=children,
                extra_fields={"root": str(path)},
                truncated=truncated,
                note_cap=self.NOTE_CAP,
            )

        node = note_node_from_file(
            path,
            uri=uri,
            namespace=self.NAMESPACE,
            text_format=_text_format_for(path),
        )
        return success(node)
