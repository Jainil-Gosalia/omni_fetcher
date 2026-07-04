"""CSV connector for the OmniFetcher v1 canonical contract.

Maps a CSV file onto the canonical contract: one ``CompositionNode`` carrying
a single canonical ``Table`` atom (headers + rows), wrapped in a ``Result``.

The connector implements only ``stream()`` (``fetch()`` is inherited from
``BaseFetcher``). It is deterministic and read-only: it reads a local CSV
file, reuses the v0.11 delimiter/header heuristics, and emits content as a
``Table`` atom while keeping every descriptive field (delimiter, row/column
counts, source path, header detection) in the namespaced ``source_extra``
metadata channel -- never inline on the atom.

Failure handling follows the contract's "never silently drop" rule:

- a missing file is an ``error(NOT_FOUND)``;
- a file that cannot be read/decoded is an ``error(PARSE_ERROR)``;
- when a header row is detected and some data rows do not match the header
  width, the malformed rows are skipped from the clean ``Table`` and reported
  as typed ``gap``s inside a ``partial`` result -- they are never quietly
  dropped into a ``success``.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from omni_fetcher.v1.atoms import Table
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.result import (
    Result,
    error,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# Source namespace for CSV descriptive metadata in ``source_extra``.
SOURCE_NAMESPACE = "csv"

# Advisory semantic ``kind`` for a CSV node (a single-sheet spreadsheet).
CSV_KIND = "spreadsheet"

# Candidate delimiters, in detection precedence order.
_CANDIDATE_DELIMITERS = (",", ";", "\t", "|")


class CSVConnector(BaseFetcher):
    """
    Canonical v1 connector for CSV files
    ===============================================
    Reads a local CSV file and emits the canonical contract: a single
    ``CompositionNode`` (advisory ``kind`` ``"spreadsheet"``) carrying one
    ``Table`` atom of headers and rows, wrapped in a ``Result``. Descriptive
    fields live in the namespaced ``source_extra`` metadata channel; the atom
    carries content only.
    ===============================================
    NOTE:
        1. Implements only ``stream()``; ``fetch()`` is inherited and collects
           the bounded one-item stream into a single ``Result``.
        2. Deterministic and read-only. Delimiter and header detection reuse
           the v0.11 heuristics.
        3. Malformed rows (width mismatching a detected header) are skipped
           from the clean ``Table`` and reported as typed gaps in a
           ``partial`` result -- never silently dropped.

    Methods
    -------
        stream:
        can_handle:
    """

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical result for a CSV file

        Reads the CSV at ``uri`` (a local path or ``file://`` URI), parses it
        deterministically, and yields exactly one ``Result``: a ``success``
        with a single ``Table``-bearing node, a ``partial`` when malformed
        rows were skipped, or an ``error`` when the file is missing or cannot
        be read/parsed.

        NOTE:
            1. ``auth`` and ``zoom`` are accepted for protocol conformance; a
               local CSV needs no credential and has a single natural
               granularity (one table).

        Parameters
        ----------
            uri:
                The CSV source URI -- a local filesystem path or ``file://``
                URI.
            auth:
                Unused; accepted for protocol conformance.
            zoom:
                Unused; accepted for protocol conformance.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result`` for the
                bounded CSV source.
        """
        path = self._uri_to_path(uri)

        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            yield error(
                ErrorKind.NOT_FOUND,
                message=f"CSV file not found: {path}",
                locator=uri,
            )
            return
        except (OSError, UnicodeError) as exc:
            yield error(
                ErrorKind.PARSE_ERROR,
                message=f"could not read CSV: {type(exc).__name__}: {exc}",
                locator=uri,
            )
            return

        yield self._build_result(uri, str(path), content)

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
                ``True`` if ``uri`` ends with the ``.csv`` extension.
        """
        return uri.lower().endswith(".csv")

    @staticmethod
    def _uri_to_path(uri: str) -> Path:
        """Resolve a local path or ``file://`` URI to a ``Path``."""
        path = uri
        if path.startswith("file://"):
            path = path[len("file://") :]
            # A ``file:///abs`` URI leaves a leading slash before a Windows
            # drive letter; drop it so ``C:/...`` resolves correctly.
            if len(path) > 2 and path.startswith("/") and path[2] == ":":
                path = path[1:]
        return Path(path)

    def _build_result(
        self,
        uri: str,
        source_path: str,
        content: str,
    ) -> Result:
        """Parse CSV content into a canonical ``Result``."""
        delimiter = self._detect_delimiter(content)
        try:
            all_rows = list(csv.reader(io.StringIO(content), delimiter=delimiter))
        except csv.Error as exc:
            return error(
                ErrorKind.PARSE_ERROR,
                message=f"malformed CSV: {exc}",
                locator=uri,
            )

        if not all_rows:
            return self._empty_result(uri, source_path, delimiter)

        has_header = self._has_header(all_rows[0])
        if has_header:
            headers = all_rows[0]
            data_rows = all_rows[1:]
        else:
            headers = [f"column_{i}" for i in range(len(all_rows[0]))]
            data_rows = all_rows

        width = len(headers)
        good_rows: list[list[Any]] = []
        gaps = []
        for index, row in enumerate(data_rows):
            if len(row) == width:
                good_rows.append(list(row))
            else:
                gaps.append(
                    gap(
                        ErrorKind.PARSE_ERROR,
                        locator=f"{uri}#row={index}",
                        detail=(f"row has {len(row)} cells; expected {width} to match headers"),
                    )
                )

        table = Table(headers=headers, rows=good_rows)
        node = build_node(
            kind=CSV_KIND,
            atoms=[table],
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "delimiter": delimiter,
                "has_header": has_header,
                "row_count": len(good_rows),
                "col_count": width,
                "skipped_rows": len(gaps),
                "source_path": source_path,
            },
        )

        if gaps:
            return partial(node, gaps)
        return success(node)

    def _empty_result(
        self,
        uri: str,
        source_path: str,
        delimiter: str,
    ) -> Result:
        """Build a success for an empty CSV (a node with an empty table)."""
        table = Table(headers=None, rows=[])
        node = build_node(
            kind=CSV_KIND,
            atoms=[table],
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields={
                "delimiter": delimiter,
                "has_header": False,
                "row_count": 0,
                "col_count": 0,
                "skipped_rows": 0,
                "source_path": source_path,
            },
        )
        return success(node)

    @staticmethod
    def _detect_delimiter(content: str) -> str:
        """Detect the delimiter from the first line (v0.11 heuristic)."""
        first_line = content.split("\n", 1)[0]
        counts = {delim: first_line.count(delim) for delim in _CANDIDATE_DELIMITERS}
        return max(counts, key=lambda d: counts[d])

    @staticmethod
    def _has_header(first_row: list[str]) -> bool:
        """Detect whether the first row is a header (v0.11 heuristic)."""
        if not first_row:
            return False
        for value in first_row:
            if any(char.isdigit() for char in value):
                return False
        return True
