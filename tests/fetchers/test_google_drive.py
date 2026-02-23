"""Tests for Google Drive fetcher."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from omni_fetcher.fetchers.google_drive import (
    GoogleDriveFetcher,
    parse_file_id,
)
from omni_fetcher.schemas.google import (
    GoogleDriveFile,
    GoogleDriveFolder,
    GoogleSheetsSpreadsheet,
    GoogleDocsDocument,
    GoogleSlidesPresentation,
)
from omni_fetcher.schemas.atomics import TextDocument, TextFormat


class TestParseFileId:
    """Tests for parse_file_id function."""

    def test_parse_file_id_from_file_url(self):
        """Parse file ID from drive.google.com/file/d/ URL."""
        uri = "https://drive.google.com/file/d/1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p/view"
        assert parse_file_id(uri) == "1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"

    def test_parse_file_id_from_uc_url(self):
        """Parse file ID from drive.google.com/uc URL."""
        uri = "https://drive.google.com/uc?id=1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"
        assert parse_file_id(uri) == "1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"

    def test_parse_file_id_from_docs_url(self):
        """Parse file ID from docs.google.com/document/d/ URL."""
        uri = "https://docs.google.com/document/d/1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p/edit"
        assert parse_file_id(uri) == "1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"

    def test_parse_file_id_from_spreadsheet_url(self):
        """Parse file ID from spreadsheets/d/ URL."""
        uri = "https://docs.google.com/spreadsheets/d/1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p/edit"
        assert parse_file_id(uri) == "1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"

    def test_parse_file_id_from_presentation_url(self):
        """Parse file ID from presentation/d/ URL."""
        uri = "https://docs.google.com/presentation/d/1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p/edit"
        assert parse_file_id(uri) == "1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"

    def test_parse_file_id_with_query_param(self):
        """Parse file ID from URL with query parameter."""
        uri = "https://drive.google.com/file?usp=sharing&id=1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"
        assert parse_file_id(uri) == "1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p"

    def test_parse_file_id_empty_string(self):
        """Empty string returns None."""
        assert parse_file_id("") is None

    def test_parse_file_id_invalid_url(self):
        """Invalid URL returns None."""
        assert parse_file_id("https://example.com/file.pdf") is None


class TestGoogleDriveFetcherCreation:
    """Tests for GoogleDriveFetcher creation."""

    def test_creation(self):
        """Can create GoogleDriveFetcher."""
        fetcher = GoogleDriveFetcher()
        assert fetcher.name == "google_drive"
        assert fetcher.priority == 15

    def test_creation_with_timeout(self):
        """Can create GoogleDriveFetcher with custom timeout."""
        fetcher = GoogleDriveFetcher(timeout=120.0)
        assert fetcher.timeout == 120.0


class TestGoogleDriveFetcherCanHandle:
    """Tests for can_handle method."""

    def test_can_handle_drive_file_url(self):
        """Handles drive.google.com/file/d/ URLs."""
        assert GoogleDriveFetcher.can_handle("https://drive.google.com/file/d/FILE_ID")
        assert GoogleDriveFetcher.can_handle("http://drive.google.com/file/d/FILE_ID")

    def test_can_handle_drive_folder_url(self):
        """Handles drive.google.com/drive/folders/ URLs."""
        assert GoogleDriveFetcher.can_handle("https://drive.google.com/drive/folders/FOLDER_ID")

    def test_can_handle_docs_url(self):
        """Handles docs.google.com URLs."""
        assert GoogleDriveFetcher.can_handle("https://docs.google.com/document/d/ID")
        assert GoogleDriveFetcher.can_handle("https://docs.google.com/spreadsheets/d/ID")
        assert GoogleDriveFetcher.can_handle("https://docs.google.com/presentation/d/ID")

    def test_cannot_handle_non_google(self):
        """Rejects non-Google URLs."""
        assert not GoogleDriveFetcher.can_handle("https://example.com/file.pdf")
        assert not GoogleDriveFetcher.can_handle("https://dropbox.com/file")
        assert not GoogleDriveFetcher.can_handle("/local/file.txt")

    def test_can_handle_empty_string(self):
        """Empty string returns False."""
        assert GoogleDriveFetcher.can_handle("") is False

    def test_can_handle_none(self):
        """None returns False."""
        assert GoogleDriveFetcher.can_handle(None) is False


class TestGoogleDriveFetcherFile:
    """Tests for file fetching."""

    @pytest.mark.asyncio
    async def test_fetch_file_success(self):
        """Test fetching a file successfully."""
        fetcher = GoogleDriveFetcher()

        with patch.object(fetcher, "_ensure_auth_token", return_value="fake_token"):
            with patch.object(fetcher, "_get_file_mime_type", new_callable=AsyncMock) as mock_mime:
                mock_mime.return_value = "application/pdf"
                with patch.object(fetcher, "_fetch_file", new_callable=AsyncMock) as mock_fetch:
                    mock_fetch.return_value = GoogleDriveFile(
                        file_id="file123",
                        name="test.pdf",
                        mime_type="application/pdf",
                        size=1024,
                    )

                    result = await fetcher.fetch("https://drive.google.com/file/d/file123")

        assert isinstance(result, GoogleDriveFile)
        assert result.file_id == "file123"
        assert result.name == "test.pdf"

    @pytest.mark.asyncio
    async def test_fetch_folder(self):
        """Test fetching a folder."""
        fetcher = GoogleDriveFetcher()

        with patch.object(fetcher, "_ensure_auth_token", return_value="fake_token"):
            with patch.object(fetcher, "_get_file_mime_type", new_callable=AsyncMock) as mock_mime:
                mock_mime.return_value = "application/vnd.google-apps.folder"
                with patch.object(fetcher, "_fetch_folder", new_callable=AsyncMock) as mock_fetch:
                    mock_fetch.return_value = GoogleDriveFolder(
                        folder_id="folder123",
                        name="Test Folder",
                        created_time=None,
                        modified_time=None,
                        parents=[],
                        web_view_link=None,
                        files=[],
                        subfolders=[],
                    )

                    result = await fetcher.fetch("https://drive.google.com/drive/folders/folder123")

        assert isinstance(result, GoogleDriveFolder)
        assert result.folder_id == "folder123"
        assert result.name == "Test Folder"

    @pytest.mark.asyncio
    async def test_fetch_folder_recursive(self):
        """Test fetching a folder recursively."""
        fetcher = GoogleDriveFetcher()

        subfolder = GoogleDriveFolder(
            folder_id="subfolder1",
            name="Subfolder",
            created_time=None,
            modified_time=None,
            parents=[],
            web_view_link=None,
            files=[],
            subfolders=[],
        )

        with patch.object(fetcher, "_ensure_auth_token", return_value="fake_token"):
            with patch.object(fetcher, "_get_file_mime_type", new_callable=AsyncMock) as mock_mime:
                mock_mime.return_value = "application/vnd.google-apps.folder"
                with patch.object(fetcher, "_fetch_folder", new_callable=AsyncMock) as mock_fetch:
                    mock_fetch.return_value = GoogleDriveFolder(
                        folder_id="folder123",
                        name="Test Folder",
                        created_time=None,
                        modified_time=None,
                        parents=[],
                        web_view_link=None,
                        files=[],
                        subfolders=[subfolder],
                    )

                    result = await fetcher.fetch(
                        "https://drive.google.com/drive/folders/folder123",
                        recursive=True,
                    )

        assert isinstance(result, GoogleDriveFolder)
        assert len(result.subfolders) == 1
        assert result.subfolders[0].folder_id == "subfolder1"

    @pytest.mark.asyncio
    async def test_fetch_spreadsheet(self):
        """Test fetching a Google Sheet."""
        fetcher = GoogleDriveFetcher()

        with patch.object(fetcher, "_ensure_auth_token", return_value="fake_token"):
            with patch.object(fetcher, "_get_file_mime_type", new_callable=AsyncMock) as mock_mime:
                mock_mime.return_value = "application/vnd.google-apps.spreadsheet"
                with patch.object(
                    fetcher, "_fetch_spreadsheet", new_callable=AsyncMock
                ) as mock_fetch:
                    mock_fetch.return_value = GoogleSheetsSpreadsheet(
                        spreadsheet_id="spreadsheet123",
                        spreadsheet_title="Test Sheet",
                        spreadsheet_url="https://docs.google.com/spreadsheets/d/spreadsheet123",
                        sheets=[],
                        created_at=None,
                        modified_at=None,
                        data=None,
                    )

                    result = await fetcher.fetch(
                        "https://docs.google.com/spreadsheets/d/spreadsheet123"
                    )

        assert isinstance(result, GoogleSheetsSpreadsheet)
        assert result.spreadsheet_id == "spreadsheet123"
        assert result.spreadsheet_title == "Test Sheet"

    @pytest.mark.asyncio
    async def test_fetch_document(self):
        """Test fetching a Google Doc."""
        fetcher = GoogleDriveFetcher()

        with patch.object(fetcher, "_ensure_auth_token", return_value="fake_token"):
            with patch.object(fetcher, "_get_file_mime_type", new_callable=AsyncMock) as mock_mime:
                mock_mime.return_value = "application/vnd.google-apps.document"
                with patch.object(fetcher, "_fetch_document", new_callable=AsyncMock) as mock_fetch:
                    mock_fetch.return_value = GoogleDocsDocument(
                        document_id="doc123",
                        title="Test Doc",
                        document_url="https://docs.google.com/document/d/doc123",
                        created_at=None,
                        modified_at=None,
                        text=TextDocument(
                            source_uri="https://docs.google.com/document/d/doc123",
                            content="Test content",
                            format=TextFormat.MARKDOWN,
                        ),
                    )

                    result = await fetcher.fetch("https://docs.google.com/document/d/doc123")

        assert isinstance(result, GoogleDocsDocument)
        assert result.document_id == "doc123"
        assert result.title == "Test Doc"

    @pytest.mark.asyncio
    async def test_fetch_presentation(self):
        """Test fetching a Google Slides presentation."""
        fetcher = GoogleDriveFetcher()

        with patch.object(fetcher, "_ensure_auth_token", return_value="fake_token"):
            with patch.object(fetcher, "_get_file_mime_type", new_callable=AsyncMock) as mock_mime:
                mock_mime.return_value = "application/vnd.google-apps.presentation"
                with patch.object(
                    fetcher, "_fetch_presentation", new_callable=AsyncMock
                ) as mock_fetch:
                    mock_fetch.return_value = GoogleSlidesPresentation(
                        presentation_id="pres123",
                        title="Test Presentation",
                        presentation_url="https://docs.google.com/presentation/d/pres123",
                        created_at=None,
                        modified_at=None,
                        slide_count=1,
                        slides=[],
                    )

                    result = await fetcher.fetch("https://docs.google.com/presentation/d/pres123")

        assert isinstance(result, GoogleSlidesPresentation)
        assert result.presentation_id == "pres123"
        assert result.title == "Test Presentation"
        assert result.slide_count == 1

    @pytest.mark.asyncio
    async def test_fetch_no_file_id_raises(self):
        """Fetching without valid file ID raises error."""
        from omni_fetcher.core.exceptions import SourceNotFoundError

        fetcher = GoogleDriveFetcher()

        with pytest.raises(SourceNotFoundError):
            await fetcher.fetch("https://example.com/file.pdf")

    @pytest.mark.asyncio
    async def test_fetch_file_not_found(self):
        """Fetching non-existent file raises error."""
        from omni_fetcher.core.exceptions import SourceNotFoundError

        fetcher = GoogleDriveFetcher()

        with patch.object(fetcher, "_ensure_auth_token", return_value="fake_token"):
            with patch.object(fetcher, "_get_file_mime_type", new_callable=AsyncMock) as mock_mime:
                mock_mime.return_value = None

                with pytest.raises(SourceNotFoundError):
                    await fetcher.fetch("https://drive.google.com/file/d/nonexistent")


class TestGoogleDriveFetcherAuth:
    """Tests for authentication flow."""

    @pytest.mark.asyncio
    async def test_ensure_auth_token_cached(self):
        """Auth token is cached and reused."""
        fetcher = GoogleDriveFetcher()
        fetcher._access_token = "cached_token"
        fetcher._token_expiry = 9999999999  # Far future

        token = await fetcher._ensure_auth_token()
        assert token == "cached_token"

    @pytest.mark.asyncio
    async def test_ensure_auth_token_no_credentials_raises(self):
        """No credentials raises FetchError."""
        from omni_fetcher.core.exceptions import FetchError

        fetcher = GoogleDriveFetcher()
        fetcher._access_token = None

        with pytest.raises(FetchError):
            await fetcher._ensure_auth_token()

    @pytest.mark.asyncio
    async def test_ensure_auth_token_invalid_json_raises(self):
        """Invalid service account JSON raises FetchError."""
        from omni_fetcher.core.exceptions import FetchError

        fetcher = GoogleDriveFetcher()
        fetcher._access_token = None
        fetcher._auth_config = MagicMock()
        fetcher._auth_config.get_google_service_account_json = MagicMock(
            return_value="invalid json"
        )

        with pytest.raises(FetchError):
            await fetcher._ensure_auth_token()

    @pytest.mark.asyncio
    async def test_ensure_auth_token_missing_fields_raises(self):
        """Missing required fields in service account JSON raises FetchError."""
        from omni_fetcher.core.exceptions import FetchError

        fetcher = GoogleDriveFetcher()
        fetcher._access_token = None
        fetcher._auth_config = MagicMock()
        fetcher._auth_config.get_google_service_account_json = MagicMock(
            return_value='{"client_email": "test@example.com"}'
        )

        with pytest.raises(FetchError):
            await fetcher._ensure_auth_token()
