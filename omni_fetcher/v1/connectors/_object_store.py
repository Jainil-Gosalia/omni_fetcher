"""Shared spec for the object-storage connector family (v1.12).

The bounded "read one object, map its bytes onto a canonical file node" shape is
identical across object stores (AWS S3, Google Cloud Storage, Azure Blob); what
differs per store is the URI shape, the credential model, the download call, and
the SDK's error taxonomy. This module holds the part that is genuinely shared --
and *only* that part, mirroring the ``_sql_query`` spec's discipline:

- content-type -> atom-kind decisions (:func:`is_text`, :func:`is_tabular`,
  :func:`text_format_for`, :func:`binary_atom_for`),
- delimited-text -> :class:`Table` parsing (:func:`parse_table`),
- the assembly of a fetched object's ``(bytes, content_type, key)`` into one
  ``kind="file"`` node (:func:`build_file_node`), with the same
  textual / binary / gap policy AWS S3 established in v0.11: text-like content
  becomes a ``Text`` atom, CSV/TSV a ``Table`` atom, recognised media an
  ``Image`` / ``Audio`` / ``Video`` atom, and an unrepresentable binary a
  ``partial`` carrying an ``UNSUPPORTED`` gap rather than a silent empty success.

Each connector owns its URI parsing, credentials, download, error mapping, and
the descriptive ``source_fields`` it files under its own namespace. This is a
spec, not a base class doing the work -- the object stores' auth models and
error taxonomies differ too much to share a connection layer.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Mapping, Optional

from omni_fetcher.v1.atoms import (
    Audio,
    Image,
    Table,
    Text,
    TextFormat,
    Video,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.result import (
    Result,
    from_exception,
    gap,
    partial,
    success,
)

# Advisory semantic ``kind`` for every node an object-storage connector emits.
FILE_KIND = "file"

# MIME types parsed into a ``Table`` atom rather than a ``Text`` atom.
CSV_MIME = "text/csv"
TSV_MIMES = frozenset({"text/tab-separated-values", "text/tsv"})

# Map a text-ish MIME type onto the canonical ``TextFormat`` for its content.
TEXT_FORMATS: dict[str, TextFormat] = {
    "text/markdown": TextFormat.MARKDOWN,
    "text/html": TextFormat.HTML,
    "text/x-rst": TextFormat.RST,
    "application/json": TextFormat.PLAIN,
    "application/xml": TextFormat.PLAIN,
}


def _base_mime(content_type: Optional[str]) -> str:
    """Return the lower-cased media type without parameters (``; charset=...``)."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def text_format_for(content_type: Optional[str]) -> TextFormat:
    """
    Pick the canonical ``TextFormat`` for a text-like object

    Parameters
    ----------
        content_type:
            The object's declared content type, or ``None``.

    Return
    ------
        text_format:
            The mapped ``TextFormat``; an unmapped ``text/*`` subtype is
            ``CODE``, everything else ``PLAIN``.
    """
    base = _base_mime(content_type)
    if base in TEXT_FORMATS:
        return TEXT_FORMATS[base]
    if base.startswith("text/") and base != "text/plain":
        # An unmapped ``text/*`` subtype is source code as far as the
        # canonical vocabulary is concerned.
        return TextFormat.CODE
    return TextFormat.PLAIN


def is_text(content_type: Optional[str]) -> bool:
    """
    Report whether an object should be decoded into a ``Text`` atom

    Parameters
    ----------
        content_type:
            The object's declared content type, or ``None``.

    Return
    ------
        textual:
            ``True`` for a text-like or unknown/generic-binary content type.
    """
    base = _base_mime(content_type)
    if not base or base == "application/octet-stream":
        # Unknown / generic binary: treat as text and let decode decide.
        return True
    if base.startswith("text/"):
        return True
    return base in {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
    }


def is_tabular(content_type: Optional[str], key: str) -> bool:
    """
    Report whether an object should be parsed into a ``Table`` atom

    Parameters
    ----------
        content_type:
            The object's declared content type, or ``None``.
        key:
            The object key/name, used as an extension fallback.

    Return
    ------
        tabular:
            ``True`` for a CSV/TSV content type or ``.csv``/``.tsv`` key.
    """
    base = _base_mime(content_type)
    if base == CSV_MIME or base in TSV_MIMES:
        return True
    return key.lower().endswith((".csv", ".tsv"))


def binary_atom_for(content_type: Optional[str], data: bytes) -> Optional[Image | Audio | Video]:
    """
    Build an image/audio/video atom for a recognised binary content type

    Parameters
    ----------
        content_type:
            The object's declared content type, or ``None``.
        data:
            The object's raw bytes, carried inline on the atom.

    Return
    ------
        atom:
            An ``Image`` / ``Audio`` / ``Video`` atom, or ``None`` for a
            content type that is not a recognised media type.
    """
    base = _base_mime(content_type)
    subtype = base.split("/", 1)[1] if "/" in base else base
    if base.startswith("image/"):
        return Image(format=subtype, data=data)
    if base.startswith("audio/"):
        return Audio(format=subtype, data=data)
    if base.startswith("video/"):
        return Video(format=subtype, data=data)
    return None


def parse_table(text: str, content_type: Optional[str], key: str) -> Table:
    """
    Parse delimited text into a canonical ``Table`` atom

    The first row is treated as headers when every subsequent row matches its
    width; otherwise the grid is emitted header-less so the ``Table``
    width-invariant is never violated.

    Parameters
    ----------
        text:
            The decoded object text.
        content_type:
            The object's declared content type, used to pick the delimiter.
        key:
            The object key/name, used as a ``.tsv`` delimiter fallback.

    Return
    ------
        table:
            The parsed ``Table`` atom.
    """
    base = _base_mime(content_type)
    is_tsv = key.lower().endswith(".tsv") or base in TSV_MIMES
    delimiter = "\t" if is_tsv else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    grid = [list(row) for row in reader]
    if not grid:
        return Table(headers=None, rows=[])
    headers = grid[0]
    body = grid[1:]
    if body and all(len(row) == len(headers) for row in body):
        return Table(headers=headers, rows=body)
    return Table(headers=None, rows=grid)


def build_file_node(
    *,
    uri: str,
    namespace: str,
    key: str,
    data: bytes,
    content_type: Optional[str],
    source_fields: Mapping[str, Any],
    updated: Optional[datetime],
) -> Result:
    """
    Fold a fetched object's bytes onto one canonical ``file`` ``Result``

    Applies the shared object-storage mapping policy: a text-like or CSV/TSV
    object decodes into a ``Text`` / ``Table`` atom; a recognised media object
    becomes an ``Image`` / ``Audio`` / ``Video`` atom carrying the bytes inline;
    an unrepresentable binary yields a ``partial`` (empty ``Text`` atom + an
    ``UNSUPPORTED`` gap). Descriptive fields land in ``source_extra[namespace]``.
    The node is left un-stamped; the connector's ``stream()`` stamps sequence and
    timestamp.

    Parameters
    ----------
        uri:
            The source URI (recorded as ``source_url`` and gap locator).
        namespace:
            The ``source_extra`` namespace for this store (``"s3"`` / ``"gcs"`` /
            ``"azure"``).
        key:
            The object key/name, used for atom-kind decisions.
        data:
            The object's raw bytes.
        content_type:
            The object's declared content type, or ``None``.
        source_fields:
            The connector's descriptive fields for ``source_extra[namespace]``.
        updated:
            The object's last-modified time, or ``None``.

    Return
    ------
        result:
            A ``Success`` (text / table / media) or ``Partial`` (unrepresentable
            binary), or an ``Error(PARSE_ERROR)`` when text bytes will not decode.
    """
    fields = dict(source_fields)

    if is_tabular(content_type, key) or is_text(content_type):
        return _build_textual_node(uri, namespace, content_type, key, data, fields, updated)

    atom = binary_atom_for(content_type, data)
    if atom is not None:
        node = build_node(
            kind=FILE_KIND,
            atoms=[atom],
            source_url=uri,
            updated=updated,
            source_namespace=namespace,
            source_fields=fields,
        )
        return success(node)

    # Recognised object, but no canonical media representation: be explicit
    # about the gap rather than emit a silent empty success.
    node = build_node(
        kind=FILE_KIND,
        atoms=[Text(content="", format=TextFormat.OPAQUE)],
        source_url=uri,
        updated=updated,
        source_namespace=namespace,
        source_fields=fields,
    )
    return partial(
        node,
        [
            gap(
                kind=ErrorKind.UNSUPPORTED,
                locator=uri,
                detail=f"binary content not represented ({content_type})",
            )
        ],
    )


def _build_textual_node(
    uri: str,
    namespace: str,
    content_type: Optional[str],
    key: str,
    data: bytes,
    source_fields: dict[str, Any],
    updated: Optional[datetime],
) -> Result:
    """Decode object bytes and assemble a ``Text`` / ``Table`` file node."""
    try:
        text = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
            encoding = "latin-1"
        except UnicodeError as exc:
            return from_exception(exc, kind=ErrorKind.PARSE_ERROR, locator=uri)

    if is_tabular(content_type, key):
        try:
            atom: Text | Table = parse_table(text, content_type, key)
        except (csv.Error, ValueError) as exc:
            return from_exception(exc, kind=ErrorKind.PARSE_ERROR, locator=uri)
    else:
        atom = Text(
            content=text,
            format=text_format_for(content_type),
            encoding=encoding,
        )

    node = build_node(
        kind=FILE_KIND,
        atoms=[atom],
        source_url=uri,
        updated=updated,
        source_namespace=namespace,
        source_fields=source_fields,
    )
    return success(node)
