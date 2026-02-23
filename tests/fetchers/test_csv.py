"""Tests for CSV fetcher."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime

from omni_fetcher.fetchers.csv import CSVFetcher
from omni_fetcher.schemas.atomics import SpreadsheetDocument, SheetData


class TestCSVFetcher:
    """Basic creation tests."""

    def test_creation(self):
        """Can create CSVFetcher."""
        fetcher = CSVFetcher()
        assert fetcher.name == "csv"
        assert fetcher.priority == 20

    def test_can_handle_csv(self):
        """CSVFetcher identifies .csv files."""
        assert CSVFetcher.can_handle("data.csv")
        assert CSVFetcher.can_handle("https://example.com/data.csv")
        assert CSVFetcher.can_handle("DATA.CSV")

    def test_cannot_handle_non_csv(self):
        """CSVFetcher rejects non-CSV files."""
        assert not CSVFetcher.can_handle("data.txt")
        assert not CSVFetcher.can_handle("data.xlsx")
        assert not CSVFetcher.can_handle("data.json")

    def test_priority(self):
        """CSVFetcher has correct priority."""
        assert CSVFetcher.priority == 20

    def test_creation_with_timeout(self):
        """Can create CSVFetcher with custom timeout."""
        fetcher = CSVFetcher(timeout=60.0)
        assert fetcher.timeout == 60.0


class TestCSVParser:
    """Tests for CSV parsing."""

    def test_parse_csv_with_headers(self):
        """Parses CSV with headers correctly."""
        fetcher = CSVFetcher()
        csv_content = "name,age,city\nJohn,30,NYC\nJane,25,LA"
        result = fetcher._parse_csv(csv_content)

        assert result["headers"] == ["name", "age", "city"]
        assert result["has_header"] is True
        assert result["row_count"] == 2
        assert result["rows"][0] == ["John", "30", "NYC"]

    def test_parse_csv_without_headers(self):
        """Parses CSV without headers correctly."""
        fetcher = CSVFetcher()
        csv_content = "John,30,NYC\nJane,25,LA"
        result = fetcher._parse_csv(csv_content)

        assert result["has_header"] is False
        assert result["headers"] == ["column_0", "column_1", "column_2"]
        assert result["row_count"] == 2

    def test_parse_csv_empty(self):
        """Handles empty CSV."""
        fetcher = CSVFetcher()
        result = fetcher._parse_csv("")

        assert result["row_count"] == 0
        assert result["headers"] == []
        assert result["rows"] == []

    def test_parse_csv_with_special_characters(self):
        """Handles CSV with special characters."""
        fetcher = CSVFetcher()
        csv_content = '"Smith, John",30,"New York, NY"'
        result = fetcher._parse_csv(csv_content)

        assert result["row_count"] == 1

    def test_parse_csv_with_missing_values(self):
        """Handles CSV with missing values."""
        fetcher = CSVFetcher()
        csv_content = "name,age,city\nJohn,,NYC\n,25,"
        result = fetcher._parse_csv(csv_content)

        assert result["row_count"] == 2
        assert result["rows"][0][1] == ""

    def test_detect_delimiter_comma(self):
        """Detects comma delimiter."""
        fetcher = CSVFetcher()
        content = "a,b,c\n1,2,3"
        assert fetcher._detect_delimiter(content) == ","

    def test_detect_delimiter_semicolon(self):
        """Detects semicolon delimiter."""
        fetcher = CSVFetcher()
        content = "a;b;c\n1;2;3"
        assert fetcher._detect_delimiter(content) == ";"

    def test_detect_delimiter_tab(self):
        """Detects tab delimiter."""
        fetcher = CSVFetcher()
        content = "a\tb\tc\n1\t2\t3"
        assert fetcher._detect_delimiter(content) == "\t"

    def test_has_header_with_numbers(self):
        """Detects non-header when row has numbers."""
        fetcher = CSVFetcher()
        row = ["John", "30", "NYC"]
        assert fetcher._has_header(row) is False

    def test_has_header_without_numbers(self):
        """Detects header when row has no numbers."""
        fetcher = CSVFetcher()
        row = ["name", "age", "city"]
        assert fetcher._has_header(row) is True

    def test_has_header_empty(self):
        """Empty row returns False."""
        fetcher = CSVFetcher()
        assert fetcher._has_header([]) is False


class TestCSVFetcherFetch:
    """Tests for fetch method."""

    @pytest.mark.asyncio
    async def test_fetch_local_csv(self):
        """Fetches local CSV file."""
        fetcher = CSVFetcher()

        with patch("pathlib.Path.read_text", return_value="name,age\nJohn,30"):
            result = await fetcher.fetch("data.csv")

        assert isinstance(result, SpreadsheetDocument)
        assert result.sheet_count == 1
        assert result.format == "csv"

    @pytest.mark.asyncio
    async def test_fetch_local_csv_with_file_scheme(self):
        """Fetches local CSV with file:// scheme."""
        fetcher = CSVFetcher()

        with patch("pathlib.Path.read_text", return_value="name,age\nJohn,30"):
            result = await fetcher.fetch("file:///data.csv")

        assert isinstance(result, SpreadsheetDocument)

    @pytest.mark.asyncio
    async def test_fetch_remote_csv(self):
        """Fetches remote CSV file."""
        fetcher = CSVFetcher()

        mock_response = MagicMock()
        mock_response.text = "name,age\nJohn,30"
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )

            result = await fetcher.fetch("https://example.com/data.csv")

        assert isinstance(result, SpreadsheetDocument)
        assert result.format == "csv"

    @pytest.mark.asyncio
    async def test_fetch_csv_with_headers(self):
        """CSV with headers returns correct SheetData."""
        fetcher = CSVFetcher()

        with patch("pathlib.Path.read_text", return_value="Name,Age,City\nJohn,30,NYC\nJane,25,LA"):
            result = await fetcher.fetch("data.csv")

        sheet = result.sheets[0]
        assert sheet.headers == ["Name", "Age", "City"]
        assert sheet.row_count == 2

    @pytest.mark.asyncio
    async def test_fetch_csv_without_headers(self):
        """CSV without headers generates column names."""
        fetcher = CSVFetcher()

        with patch("pathlib.Path.read_text", return_value="John,30,NYC\nJane,25,LA"):
            result = await fetcher.fetch("data.csv")

        sheet = result.sheets[0]
        assert sheet.headers == ["column_0", "column_1", "column_2"]

    @pytest.mark.asyncio
    async def test_fetch_empty_csv(self):
        """Empty CSV returns empty SpreadsheetDocument."""
        fetcher = CSVFetcher()

        with patch("pathlib.Path.read_text", return_value=""):
            result = await fetcher.fetch("empty.csv")

        sheet = result.sheets[0]
        assert sheet.row_count == 0
