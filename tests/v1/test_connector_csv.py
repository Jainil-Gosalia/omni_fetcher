"""External-behaviour tests for the v1 CSV connector.

These exercise only the public surface (``stream()`` / inherited ``fetch()``)
over temp CSV files -- no network. They assert the canonical contract:

- a well-formed CSV yields a ``Success`` whose single node carries one
  ``Table`` atom (headers + rows), with CSV descriptive fields living in the
  namespaced ``source_extra`` metadata (never inline on the atom);
- a CSV with rows that do not match the header width yields a ``Partial``
  whose ``gaps`` flag every skipped row -- malformed rows are never silently
  dropped into a clean success;
- a missing file yields ``error(NOT_FOUND)``.
"""

from __future__ import annotations

from pathlib import Path

from omni_fetcher.v1.atoms import AtomKind, Table
from omni_fetcher.v1.connectors.csv import (
    CSV_KIND,
    SOURCE_NAMESPACE,
    CSVConnector,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success


def _write_csv(tmp_path: Path, name: str, text: str) -> Path:
    """Write CSV text to a temp file and return its path."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


async def test_well_formed_csv_is_success_with_table(tmp_path: Path) -> None:
    """A clean CSV yields a Success with one Table atom (headers + rows)."""
    path = _write_csv(
        tmp_path,
        "people.csv",
        "name,city\nAlice,Paris\nBob,Berlin\n",
    )
    result = await CSVConnector().fetch(str(path))

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == CSV_KIND

    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    table = atoms[0]
    assert isinstance(table, Table)
    assert table.kind is AtomKind.TABLE
    assert table.headers == ["name", "city"]
    assert table.rows == [["Alice", "Paris"], ["Bob", "Berlin"]]


async def test_csv_descriptive_fields_in_source_extra(tmp_path: Path) -> None:
    """CSV descriptive fields live namespaced in source_extra, not on atom."""
    path = _write_csv(
        tmp_path,
        "people.csv",
        "name,city\nAlice,Paris\nBob,Berlin\n",
    )
    result = await CSVConnector().fetch(str(path))

    assert isinstance(result, Success)
    extra = result.tree.metadata.source_extra
    assert SOURCE_NAMESPACE in extra
    csv_extra = extra[SOURCE_NAMESPACE]
    assert csv_extra["delimiter"] == ","
    assert csv_extra["has_header"] is True
    assert csv_extra["row_count"] == 2
    assert csv_extra["col_count"] == 2
    assert csv_extra["skipped_rows"] == 0
    assert csv_extra["source_path"].endswith("people.csv")

    # Content-only atom: no descriptive fields leak onto the Table.
    table = next(result.tree.iter_atoms())
    assert set(table.model_dump().keys()) == {"kind", "headers", "rows"}


async def test_semicolon_delimiter_detected(tmp_path: Path) -> None:
    """A non-comma delimiter is detected and reported in source_extra."""
    path = _write_csv(
        tmp_path,
        "data.csv",
        "name;city\nAlice;Paris\n",
    )
    result = await CSVConnector().fetch(str(path))

    assert isinstance(result, Success)
    assert result.tree.metadata.source_extra[SOURCE_NAMESPACE]["delimiter"] == ";"
    table = next(result.tree.iter_atoms())
    assert table.headers == ["name", "city"]
    assert table.rows == [["Alice", "Paris"]]


async def test_malformed_rows_yield_partial_with_gaps(
    tmp_path: Path,
) -> None:
    """Rows not matching the header width are skipped and flagged as gaps."""
    path = _write_csv(
        tmp_path,
        "ragged.csv",
        "name,city\nAlice,Paris\nBob\nCarol,Rome,extra\nDave,Oslo\n",
    )
    result = await CSVConnector().fetch(str(path))

    assert isinstance(result, Partial)

    # Only the well-formed rows survive into the clean Table.
    table = next(result.tree.iter_atoms())
    assert isinstance(table, Table)
    assert table.headers == ["name", "city"]
    assert table.rows == [["Alice", "Paris"], ["Dave", "Oslo"]]

    # Both malformed rows are reported as typed gaps -- never dropped silently.
    assert len(result.gaps) == 2
    assert all(g.kind is ErrorKind.PARSE_ERROR for g in result.gaps)
    assert all(g.locator and "row=" in g.locator for g in result.gaps)

    # The skip count is also surfaced in descriptive metadata.
    csv_extra = result.tree.metadata.source_extra[SOURCE_NAMESPACE]
    assert csv_extra["skipped_rows"] == 2
    assert csv_extra["row_count"] == 2


async def test_missing_file_is_not_found(tmp_path: Path) -> None:
    """A missing CSV file yields error(NOT_FOUND), not a raised exception."""
    missing = tmp_path / "does_not_exist.csv"
    result = await CSVConnector().fetch(str(missing))

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND


async def test_stream_yields_single_result(tmp_path: Path) -> None:
    """The bounded CSV stream() yields exactly one Result."""
    path = _write_csv(tmp_path, "one.csv", "a,b\n1,2\n")

    items = []
    async for item in CSVConnector().stream(str(path)):
        items.append(item)

    assert len(items) == 1


async def test_no_header_generates_column_names(tmp_path: Path) -> None:
    """A header-less CSV gets generated column_ names and keeps all rows."""
    path = _write_csv(tmp_path, "nums.csv", "1,2,3\n4,5,6\n")
    result = await CSVConnector().fetch(str(path))

    assert isinstance(result, Success)
    table = next(result.tree.iter_atoms())
    assert table.headers == ["column_0", "column_1", "column_2"]
    assert table.rows == [["1", "2", "3"], ["4", "5", "6"]]
    csv_extra = result.tree.metadata.source_extra[SOURCE_NAMESPACE]
    assert csv_extra["has_header"] is False
