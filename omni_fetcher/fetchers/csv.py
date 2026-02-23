"""CSV fetcher for OmniFetcher."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, List

import httpx

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.atomics import SpreadsheetDocument, SheetData


@source(
    name="csv",
    uri_patterns=["*.csv"],
    mime_types=["text/csv", "application/csv"],
    priority=20,
    description="Fetch and parse CSV files",
)
class CSVFetcher(BaseFetcher):
    """Fetcher for CSV files."""

    name = "csv"
    priority = 20

    def __init__(self, timeout: float = 30.0):
        super().__init__()
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this is a CSV URL."""
        return uri.lower().endswith(".csv")

    async def fetch(self, uri: str, **kwargs: Any) -> SpreadsheetDocument:
        """Fetch and parse a CSV file."""
        if uri.startswith("file://") or not uri.startswith("http"):
            content = await self._fetch_local(uri)
        else:
            content = await self._fetch_remote(uri)

        # Parse CSV
        parsed = self._parse_csv(content)

        sheet_data = SheetData(
            name="Sheet1",
            headers=parsed["headers"],
            rows=parsed["rows"],
            row_count=parsed["row_count"],
            col_count=len(parsed["headers"]) if parsed["headers"] else 0,
        )

        tags = ["csv", "spreadsheet"]
        return SpreadsheetDocument(
            source_uri=uri,
            fetched_at=datetime.now(),
            sheets=[sheet_data],
            format="csv",
            sheet_count=1,
            tags=tags,
        )

    async def _fetch_local(self, uri: str) -> str:
        """Fetch local CSV file."""
        from pathlib import Path

        path = uri.replace("file://", "")
        if path.startswith("/"):
            path = path[1:]

        return Path(path).read_text(encoding="utf-8")

    async def _fetch_remote(self, uri: str) -> str:
        """Fetch remote CSV file."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(uri)
            response.raise_for_status()
            return response.text

    def _parse_csv(self, content: str) -> dict[str, Any]:
        """Parse CSV content."""
        # Detect delimiter
        delimiter = self._detect_delimiter(content)

        reader = csv.reader(io.StringIO(content), delimiter=delimiter)

        rows = list(reader)

        if not rows:
            return {
                "headers": [],
                "row_count": 0,
                "delimiter": delimiter,
                "has_header": False,
                "rows": [],
            }

        # Detect if first row is header
        has_header = self._has_header(rows[0])

        if has_header:
            headers = rows[0]
            data_rows = rows[1:]
        else:
            # Generate column names
            headers = [f"column_{i}" for i in range(len(rows[0]))]
            data_rows = rows

        # Get sample rows (first 5)
        sample_rows = data_rows[:5]

        return {
            "headers": headers,
            "row_count": len(data_rows),
            "delimiter": delimiter,
            "has_header": has_header,
            "rows": data_rows,
        }

    def _detect_delimiter(self, content: str) -> str:
        """Detect CSV delimiter."""
        # Common delimiters to check
        delimiters = [",", ";", "\t", "|"]

        first_line = content.split("\n")[0]

        counts = {}
        for delim in delimiters:
            counts[delim] = first_line.count(delim)

        # Return most common delimiter
        return max(counts, key=counts.get)

    def _has_header(self, first_row: List[str]) -> bool:
        """Detect if first row is a header."""
        if not first_row:
            return False

        # Check if values look like headers (short strings, no numbers)
        for value in first_row:
            if any(c.isdigit() for c in value):
                return False

        return True
