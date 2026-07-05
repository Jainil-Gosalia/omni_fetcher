"""DOCX connector for the OmniFetcher v1 canonical contract.

Maps a Microsoft Word ``.docx`` document onto the canonical composition
tree. The document becomes a single ``CompositionNode`` (advisory ``kind``
``"document"``) whose content lives in canonical atoms:

- paragraph text  -> ``Text`` atoms (in document order),
- tables           -> ``Table`` atoms (in document order, interleaved with
  the paragraphs), and
- embedded images  -> ``Image`` atoms (carried inline as bytes).

Everything that *describes* the document -- title, author, core properties,
``has_images`` / ``has_tables`` flags -- lives in the metadata channel
(common core + the namespaced ``source_extra["docx"]`` mapping), never inline
on an atom (atoms are content-only; see ``atoms.py``).

The connector is deterministic and read-only: it parses bytes already on
disk, performs no OCR, and never mutates the source. Expected failures
(missing file, unreadable/corrupt document) are returned as typed ``error``
results; an embedded object that cannot be read is reported as a ``gap`` in a
``partial`` result rather than silently dropped.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from omni_fetcher.v1.atoms import Image, Table, Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.decompose import decompose_result
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import NodeChild
from omni_fetcher.v1.result import (
    Gap,
    Result,
    from_exception,
    gap_from_exception,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# The advisory semantic ``kind`` for the node a parsed document maps onto, and
# the namespace its source-specific descriptive fields are filed under.
DOCUMENT_KIND = "document"
SOURCE_NAMESPACE = "docx"

_DOCX_SUFFIX = ".docx"


class DocxConnector(BaseFetcher):
    """
    Canonical-contract connector for Microsoft Word documents
    ===============================================
    Streams a single ``Result`` carrying the document as one canonical
    ``CompositionNode``: body paragraphs and tables become ``Text`` / ``Table``
    atoms in document order, embedded images become inline ``Image`` atoms, and
    every descriptive field is filed in the metadata core or the namespaced
    ``source_extra["docx"]`` mapping. Deterministic, read-only, and OCR-free.
    ===============================================
    NOTE:
        1. Only ``stream()`` is implemented; ``fetch()`` is inherited and
           collects the single-item bounded stream into one ``Result``.
        2. Expected failures are returned as typed ``error`` results, never
           raised; an unreadable embedded object becomes a ``gap`` in a
           ``partial`` result so nothing is dropped silently.

    Methods
    -------
        stream:
        can_handle:
    """

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether this connector handles a URI

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` when ``uri`` names a ``.docx`` document.
        """
        return _local_path(uri).lower().endswith(_DOCX_SUFFIX)

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the document as a single canonical result

        Reads the ``.docx`` bytes from the local path named by ``uri``, parses
        them off the event loop, and yields exactly one ``Result``: a
        ``success`` carrying the document node, or a ``partial`` when some
        embedded content could not be read, or a typed ``error`` when the file
        is missing or cannot be parsed at all.

        NOTE:
            1. This is a bounded, single-item stream; ``auth`` and ``zoom`` are
               accepted for interface conformance but a local document needs no
               credential and is emitted at its natural granularity.

        Parameters
        ----------
            uri:
                The source URI (a local path or ``file://`` URI) to read.
            auth:
                Unused; a local document needs no credential.
            zoom:
                Unused; the document is emitted at natural granularity.

        Return
        ------
            results:
                An async iterator yielding the one ``Result`` for the document.
        """
        del auth  # Accepted for interface conformance; not needed here.

        path = Path(_local_path(uri))
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            yield from_exception(
                exc,
                kind=ErrorKind.NOT_FOUND,
                message="docx file not found",
                locator=uri,
            )
            return
        except (PermissionError, OSError) as exc:
            yield from_exception(exc, message="cannot read docx file", locator=uri)
            return

        try:
            parsed = await asyncio.to_thread(_parse_docx, data)
        except Exception as exc:  # noqa: BLE001 -- boundary: return, never raise.
            yield from_exception(
                exc,
                kind=ErrorKind.PARSE_ERROR,
                message="cannot parse docx document",
                locator=uri,
            )
            return

        node = build_node(
            kind=DOCUMENT_KIND,
            atoms=parsed.atoms,
            created=parsed.created,
            updated=parsed.modified,
            author=parsed.author,
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=parsed.source_fields,
        )

        result = partial(node, parsed.gaps) if parsed.gaps else success(node)
        if zoom is not None:
            # Finer-than-natural text zoom (see v1.decompose); lossless.
            result = decompose_result(result, zoom)
        yield result


class _ParsedDocx:
    """In-memory result of parsing a docx: canonical atoms + descriptive data.

    Holds the ordered content atoms, the typed gaps for any content that could
    not be read, the common-core descriptive fields, and the namespaced
    ``source_extra["docx"]`` payload. This is a plain value carrier handed back
    from the (synchronous) parse to the (async) ``stream`` method.
    """

    __slots__ = (
        "atoms",
        "gaps",
        "author",
        "created",
        "modified",
        "source_fields",
    )

    def __init__(
        self,
        *,
        atoms: list[NodeChild],
        gaps: list[Gap],
        author: Optional[str],
        created: Any,
        modified: Any,
        source_fields: dict[str, Any],
    ) -> None:
        self.atoms = atoms
        self.gaps = gaps
        self.author = author
        self.created = created
        self.modified = modified
        self.source_fields = source_fields


def _local_path(uri: str) -> str:
    """Normalise a ``file://`` URI (or bare path) to a local filesystem path."""
    if uri.startswith("file://"):
        path = uri[len("file://") :]
        # Strip a leading slash from ``file:///C:/...`` style URIs on Windows.
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        return path
    return uri


def _parse_docx(data: bytes) -> _ParsedDocx:
    """Parse docx bytes into canonical atoms and descriptive metadata.

    Runs synchronously (off the event loop via ``asyncio.to_thread``). Body
    paragraphs and tables are emitted as ``Text`` / ``Table`` atoms in true
    document order by walking the body XML; embedded images are appended as
    inline ``Image`` atoms in relationship order. Any single image that cannot
    be read is recorded as a typed gap rather than aborting the parse.
    """
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml.ns import qn
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(data))
    body = doc.element.body
    paragraph_tag = qn("w:p")
    table_tag = qn("w:tbl")

    atoms: list[NodeChild] = []
    gaps: list[Gap] = []
    has_tables = False

    # Walk the body's children in declared order so paragraphs and tables are
    # interleaved exactly as they appear in the document.
    for element in body.iterchildren():
        if element.tag == paragraph_tag:
            text = Paragraph(element, doc).text
            atoms.append(Text(content=text, format=TextFormat.PLAIN))
        elif element.tag == table_tag:
            has_tables = True
            atoms.append(_table_atom(DocxTable(element, doc)))

    # Embedded images: collected from the package relationships in a stable
    # order. python-docx does not expose inline image position cheaply, so they
    # are appended after the body text/tables (still deterministic).
    image_count = 0
    for rel_id in sorted(doc.part.rels):
        rel = doc.part.rels[rel_id]
        if rel.reltype != RT.IMAGE:
            continue
        try:
            image_part = rel.target_part
            atoms.append(Image(format=image_part.content_type, data=image_part.blob))
            image_count += 1
        except Exception as exc:  # noqa: BLE001 -- record gap, keep parsing.
            gaps.append(
                gap_from_exception(
                    exc,
                    kind=ErrorKind.PARSE_ERROR,
                    locator=rel.target_ref,
                )
            )

    core = doc.core_properties
    title = core.title or None
    author = core.author or None
    created = core.created or None
    modified = core.modified or None

    source_fields: dict[str, Any] = {
        "title": title,
        "author": author,
        "subject": core.subject or None,
        "keywords": core.keywords or None,
        "category": core.category or None,
        "comments": core.comments or None,
        "last_modified_by": core.last_modified_by or None,
        "revision": core.revision or None,
        "created": created.isoformat() if created else None,
        "modified": modified.isoformat() if modified else None,
        "has_images": image_count > 0,
        "has_tables": has_tables,
        "image_count": image_count,
    }

    return _ParsedDocx(
        atoms=atoms,
        gaps=gaps,
        author=author,
        created=created,
        modified=modified,
        source_fields=source_fields,
    )


def _table_atom(table: Any) -> Table:
    """Convert a python-docx table into a canonical ``Table`` atom.

    The first row is treated as headers when the table has more than one row;
    a single-row table is emitted as one header-less data row so no content is
    invented or lost.
    """
    grid: list[list[str]] = []
    for row in table.rows:
        grid.append([cell.text for cell in row.cells])

    if not grid:
        return Table(headers=None, rows=[])
    if len(grid) == 1:
        return Table(headers=None, rows=grid)

    headers = grid[0]
    width = len(headers)
    # Normalise every data row to the header width so the Table validator
    # (rows must match headers) never rejects a ragged grid.
    rows: list[list[Any]] = []
    for raw in grid[1:]:
        if len(raw) < width:
            raw = raw + [""] * (width - len(raw))
        elif len(raw) > width:
            raw = raw[:width]
        rows.append(raw)
    return Table(headers=headers, rows=rows)
