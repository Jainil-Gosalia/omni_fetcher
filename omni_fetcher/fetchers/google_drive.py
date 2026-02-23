"""Google Drive, Sheets, Docs, and Slides fetcher for OmniFetcher."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs

import httpx
import jwt

from omni_fetcher.core.exceptions import FetchError, SourceNotFoundError
from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.atomics import TextDocument, SpreadsheetDocument, SheetData, TextFormat
from omni_fetcher.schemas.google import (
    GoogleDriveFile,
    GoogleDriveFolder,
    GoogleSheetsSpreadsheet,
    GoogleDocsDocument,
    GoogleSlidesPresentation,
)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DOCS_API_BASE = "https://docs.googleapis.com/v1"
SHEETS_API_BASE = "https://sheets.googleapis.com/v4"
SLIDES_API_BASE = "https://slides.googleapis.com/v1"

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/presentations.readonly",
]


@dataclass
class GoogleDriveRoute:
    """Parsed Google Drive URI route."""

    type: str
    file_id: Optional[str] = None
    folder_id: Optional[str] = None


def parse_file_id(uri: str) -> Optional[str]:
    """Extract file ID from various Google Drive URI formats."""
    if not uri:
        return None

    uri = uri.strip()

    patterns = [
        r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/uc\?id=([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)",
        r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)",
        r"spreadsheets/d/([a-zA-Z0-9_-]+)",
        r"presentation/d/([a-zA-Z0-9_-]+)",
        r"^[a-zA-Z0-9_-]{20,}$",
    ]

    for pattern in patterns:
        match = re.search(pattern, uri)
        if match:
            return match.group(1)

    parsed = urlparse(uri)
    if "drive.google.com" in uri or "docs.google.com" in uri:
        params = parse_qs(parsed.query)
        if "id" in params:
            return params["id"][0]

    return None


@source(
    name="google_drive",
    uri_patterns=[
        "drive.google.com",
        "docs.google.com",
        "spreadsheets.google.com",
        "slides.google.com",
    ],
    priority=15,
    description="Fetch from Google Drive — files, folders, Docs, Sheets, Slides",
    auth={"type": "google_service_account"},
)
class GoogleDriveFetcher(BaseFetcher):
    """Fetcher for Google Drive API - files, folders, Docs, Sheets, Slides."""

    name = "google_drive"
    priority = 15

    def __init__(self, timeout: float = 60.0):
        super().__init__()
        self.timeout = timeout
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[float] = None

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if URI is a Google Drive URL."""
        if not uri:
            return False
        lower_uri = uri.lower()
        return any(
            domain in lower_uri
            for domain in [
                "drive.google.com",
                "docs.google.com",
                "spreadsheets.google.com",
                "slides.google.com",
            ]
        )

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch from Google Drive based on URI type."""
        file_id = parse_file_id(uri)
        if not file_id:
            raise SourceNotFoundError(f"Could not parse Google Drive file ID from: {uri}")

        mime_type = await self._get_file_mime_type(file_id)

        if not mime_type:
            raise SourceNotFoundError(f"File not found: {file_id}")

        if "folder" in mime_type.lower():
            return await self._fetch_folder(file_id, uri, **kwargs)

        if "spreadsheet" in mime_type.lower():
            return await self._fetch_spreadsheet(file_id, uri, **kwargs)

        if "document" in mime_type.lower():
            return await self._fetch_document(file_id, uri, **kwargs)

        if "presentation" in mime_type.lower():
            return await self._fetch_presentation(file_id, uri, **kwargs)

        return await self._fetch_file(file_id, uri)

    async def _ensure_auth_token(self) -> str:
        """Ensure we have a valid access token."""
        if self._access_token and self._token_expiry:
            import time

            if time.time() < self._token_expiry - 60:
                return self._access_token

        service_account_json = self._get_service_account_json()
        if not service_account_json:
            raise FetchError(
                "",
                "No Google service account credentials. Set google_service_account_env or provide JSON.",
            )

        self._access_token = await self._get_access_token_from_service_account(service_account_json)
        return self._access_token

    def _get_service_account_json(self) -> Optional[str]:
        """Get service account JSON from auth config or kwargs."""
        if self._auth_config:
            return self._auth_config.get_google_service_account_json()
        return None

    async def _get_access_token_from_service_account(self, service_account_json: str) -> str:
        """Get access token using service account JSON."""
        try:
            creds = json.loads(service_account_json)
        except json.JSONDecodeError:
            raise FetchError("", "Invalid service account JSON")

        if "private_key" not in creds or "client_email" not in creds:
            raise FetchError("", "Service account JSON missing required fields")

        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": creds["client_email"],
                "sub": creds.get("sub", creds["client_email"]),
                "aud": "https://oauth2.googleapis.com/token",
                "iat": now,
                "exp": now + 3600,
            },
            creds["private_key"],
            algorithm="RS256",
            headers={"kid": creds.get("private_key_id", "")},
        )

        token_url = "https://oauth2.googleapis.com/token"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )

        if response.status_code != 200:
            raise FetchError("", f"Failed to get access token: {response.text}")

        token_data = response.json()
        self._token_expiry = time.time() + token_data.get("expires_in", 3600)
        return token_data["access_token"]

    def _get_headers(self) -> dict[str, str]:
        """Get headers including auth."""
        headers = {
            "Content-Type": "application/json",
        }
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    async def _get_file_mime_type(self, file_id: str) -> Optional[str]:
        """Get file MIME type."""
        token = await self._ensure_auth_token()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{DRIVE_API_BASE}/files/{file_id}",
                params={"fields": "mimeType"},
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code == 404:
                return None
            if response.status_code != 200:
                return None

            data = response.json()
            return data.get("mimeType")

    async def _fetch_file(self, file_id: str, uri: str) -> GoogleDriveFile:
        """Fetch file metadata from Google Drive."""
        token = await self._ensure_auth_token()

        fields = [
            "id",
            "name",
            "mimeType",
            "size",
            "createdTime",
            "modifiedTime",
            "parents",
            "webViewLink",
            "webContentLink",
            "iconLink",
            "thumbnailLink",
        ]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{DRIVE_API_BASE}/files/{file_id}",
                params={"fields": ",".join(fields)},
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code == 404:
                raise SourceNotFoundError(f"File not found: {file_id}")
            if response.status_code != 200:
                raise FetchError(uri, f"Google Drive API error: {response.status_code}")

            data = response.json()

        return GoogleDriveFile(
            file_id=data.get("id", file_id),
            name=data.get("name", ""),
            mime_type=data.get("mimeType", ""),
            size=int(data["size"]) if data.get("size") else None,
            created_time=datetime.fromisoformat(data["createdTime"].replace("Z", "+00:00"))
            if data.get("createdTime")
            else None,
            modified_time=datetime.fromisoformat(data["modifiedTime"].replace("Z", "+00:00"))
            if data.get("modifiedTime")
            else None,
            parents=data.get("parents", []),
            web_view_link=data.get("webViewLink"),
            web_content_link=data.get("webContentLink"),
            icon_link=data.get("iconLink"),
            thumbnail_link=data.get("thumbnailLink"),
        )

    async def _fetch_folder(self, folder_id: str, uri: str, **kwargs: Any) -> GoogleDriveFolder:
        """Fetch folder contents from Google Drive."""
        token = await self._ensure_auth_token()
        recursive = kwargs.get("recursive", False)
        max_files = kwargs.get("max_files", 100)

        folder_data = await self._get_folder_metadata(folder_id, token)
        files = await self._list_folder_files(folder_id, token, max_files)
        subfolders = []

        if recursive:
            subfolders = await self._list_subfolders(folder_id, token, max_files // 10)

        return GoogleDriveFolder(
            folder_id=folder_data["id"],
            name=folder_data["name"],
            created_time=datetime.fromisoformat(folder_data["createdTime"].replace("Z", "+00:00"))
            if folder_data.get("createdTime")
            else None,
            modified_time=datetime.fromisoformat(folder_data["modifiedTime"].replace("Z", "+00:00"))
            if folder_data.get("modifiedTime")
            else None,
            parents=folder_data.get("parents", []),
            web_view_link=folder_data.get("webViewLink"),
            files=files,
            subfolders=subfolders,
            file_count=len(files),
            folder_count=len(subfolders),
        )

    async def _get_folder_metadata(self, folder_id: str, token: str) -> dict:
        """Get folder metadata."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{DRIVE_API_BASE}/files/{folder_id}",
                params={"fields": "id,name,createdTime,modifiedTime,parents,webViewLink"},
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code != 200:
                raise FetchError("", f"Failed to get folder: {response.text}")

            return response.json()

    async def _list_folder_files(
        self, folder_id: str, token: str, max_files: int
    ) -> list[GoogleDriveFile]:
        """List files in a folder."""
        files = []
        page_token = None

        while len(files) < max_files:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType,size,createdTime,modifiedTime,parents,webViewLink)",
                "pageSize": min(100, max_files - len(files)),
            }
            if page_token:
                params["pageToken"] = page_token

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{DRIVE_API_BASE}/files",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )

                if response.status_code != 200:
                    break

                data = response.json()
                for f in data.get("files", []):
                    files.append(
                        GoogleDriveFile(
                            file_id=f["id"],
                            name=f["name"],
                            mime_type=f["mimeType"],
                            size=int(f["size"]) if f.get("size") else None,
                            created_time=datetime.fromisoformat(
                                f["createdTime"].replace("Z", "+00:00")
                            )
                            if f.get("createdTime")
                            else None,
                            modified_time=datetime.fromisoformat(
                                f["modifiedTime"].replace("Z", "+00:00")
                            )
                            if f.get("modifiedTime")
                            else None,
                            parents=f.get("parents", []),
                            web_view_link=f.get("webViewLink"),
                        )
                    )

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        return files

    async def _list_subfolders(
        self, folder_id: str, token: str, max_folders: int
    ) -> list[GoogleDriveFolder]:
        """List subfolders in a folder."""
        folders = []

        params = {
            "q": f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            "fields": "nextPageToken,files(id,name,createdTime,modifiedTime,parents,webViewLink)",
            "pageSize": min(100, max_folders),
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{DRIVE_API_BASE}/files",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code != 200:
                return []

            data = response.json()
            for f in data.get("files", []):
                folders.append(
                    GoogleDriveFolder(
                        folder_id=f["id"],
                        name=f["name"],
                        created_time=datetime.fromisoformat(f["createdTime"].replace("Z", "+00:00"))
                        if f.get("createdTime")
                        else None,
                        modified_time=datetime.fromisoformat(
                            f["modifiedTime"].replace("Z", "+00:00")
                        )
                        if f.get("modifiedTime")
                        else None,
                        parents=f.get("parents", []),
                        web_view_link=f.get("webViewLink"),
                    )
                )

        return folders

    async def _fetch_spreadsheet(
        self, spreadsheet_id: str, uri: str, **kwargs: Any
    ) -> GoogleSheetsSpreadsheet:
        """Fetch Google Sheets spreadsheet."""
        token = await self._ensure_auth_token()

        sheet_name = kwargs.get("sheet_name", "Sheet1")
        as_csv = kwargs.get("export_as_csv", True)

        spreadsheet_data = await self._get_spreadsheet_metadata(spreadsheet_id, token)

        if as_csv:
            csv_content = await self._export_spreadsheet_csv(spreadsheet_id, sheet_name, token)
            headers = []
            rows = []
            if csv_content:
                lines = csv_content.strip().split("\n")
                if lines:
                    headers = lines[0].split(",")
                    for line in lines[1:]:
                        rows.append(line.split(","))
            sheet_data = SheetData(
                name=sheet_name,
                headers=headers,
                rows=rows,
                row_count=len(rows),
                col_count=len(headers),
            )
            spreadsheet_doc = SpreadsheetDocument(
                source_uri=uri,
                sheets=[sheet_data],
                format="csv",
                sheet_count=1,
            )
        else:
            values = await self._get_spreadsheet_values(spreadsheet_id, sheet_name, token)
            rows = len(values)
            cols = len(values[0]) if values else 0
            sheet_data = SheetData(
                name=sheet_name,
                headers=values[0] if values else [],
                rows=values[1:] if values else [],
                row_count=rows - 1 if rows > 0 else 0,
                col_count=cols,
            )
            spreadsheet_doc = SpreadsheetDocument(
                source_uri=uri,
                sheets=[sheet_data],
                format="json",
                sheet_count=1,
            )

        return GoogleSheetsSpreadsheet(
            spreadsheet_id=spreadsheet_id,
            spreadsheet_title=spreadsheet_data.get("title", ""),
            sheets=spreadsheet_data.get("sheets", []),
            spreadsheet_url=f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}",
            created_at=datetime.fromisoformat(
                spreadsheet_data["createdTime"].replace("Z", "+00:00")
            )
            if spreadsheet_data.get("createdTime")
            else None,
            modified_at=datetime.fromisoformat(
                spreadsheet_data["modifiedTime"].replace("Z", "+00:00")
            )
            if spreadsheet_data.get("modifiedTime")
            else None,
            data=spreadsheet_doc,
        )

    async def _get_spreadsheet_metadata(self, spreadsheet_id: str, token: str) -> dict:
        """Get spreadsheet metadata."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}",
                params={"fields": "title,createdTime,modifiedTime,sheets(properties(title))"},
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code != 200:
                raise FetchError("", f"Failed to get spreadsheet: {response.text}")

            data = response.json()
            return {
                "title": data.get("title", ""),
                "createdTime": data.get("createdTime"),
                "modifiedTime": data.get("modifiedTime"),
                "sheets": [
                    s.get("properties", {}).get("title", "Sheet1") for s in data.get("sheets", [])
                ],
            }

    async def _export_spreadsheet_csv(
        self, spreadsheet_id: str, sheet_name: str, token: str
    ) -> str:
        """Export spreadsheet as CSV."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{sheet_name}",
                params={"valueRenderOption": "UNFORMATTED_VALUE"},
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code != 200:
                raise FetchError("", f"Failed to get sheet values: {response.text}")

            data = response.json()
            rows = data.get("values", [])

        if not rows:
            return ""

        csv_lines = []
        for row in rows:
            escaped = [f'"{field}"' if "," in str(field) else str(field) for field in row]
            csv_lines.append(",".join(escaped))

        return "\n".join(csv_lines)

    async def _get_spreadsheet_values(
        self, spreadsheet_id: str, sheet_name: str, token: str
    ) -> list[list[str]]:
        """Get spreadsheet values as nested list."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{SHEETS_API_BASE}/spreadsheets/{spreadsheet_id}/values/{sheet_name}",
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code != 200:
                return []

            data = response.json()
            return data.get("values", [])

    async def _fetch_document(
        self, document_id: str, uri: str, **kwargs: Any
    ) -> GoogleDocsDocument:
        """Fetch Google Docs document."""
        token = await self._ensure_auth_token()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{DOCS_API_BASE}/documents/{document_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code == 404:
                raise SourceNotFoundError(f"Document not found: {document_id}")
            if response.status_code != 200:
                raise FetchError(uri, f"Google Docs API error: {response.status_code}")

            data = response.json()

        text_content = self._extract_text_from_document(data)

        title = data.get("title", "")
        created_time = data.get("createdTime")
        modified_time = data.get("lastModifiedTime")

        return GoogleDocsDocument(
            document_id=document_id,
            title=title,
            document_url=f"https://docs.google.com/document/d/{document_id}",
            created_at=datetime.fromisoformat(created_time.replace("Z", "+00:00"))
            if created_time
            else None,
            modified_at=datetime.fromisoformat(modified_time.replace("Z", "+00:00"))
            if modified_time
            else None,
            text=TextDocument(
                source_uri=uri,
                content=text_content,
                format=TextFormat.MARKDOWN,
                language="markdown",
            ),
        )

    def _extract_text_from_document(self, doc_data: dict) -> str:
        """Extract plain text from Google Docs document structure."""
        text_parts = []

        for element in doc_data.get("body", {}).get("content", []):
            if "paragraph" in element:
                para = element["paragraph"]
                for text_elem in para.get("elements", []):
                    if "textRun" in text_elem:
                        text_parts.append(text_elem["textRun"].get("content", ""))

            elif "table" in element:
                table = element["table"]
                for row in table.get("tableRows", []):
                    row_text = []
                    for cell in row.get("tableCells", []):
                        cell_text = []
                        for cell_elem in cell.get("content", []):
                            if "paragraph" in cell_elem:
                                for text_elem in cell_elem["paragraph"].get("elements", []):
                                    if "textRun" in text_elem:
                                        cell_text.append(text_elem["textRun"].get("content", ""))
                        row_text.append("".join(cell_text).strip())
                    if any(row_text):
                        text_parts.append(" | ".join(row_text))

        return "".join(text_parts)

    async def _fetch_presentation(
        self, presentation_id: str, uri: str, **kwargs: Any
    ) -> GoogleSlidesPresentation:
        """Fetch Google Slides presentation."""
        token = await self._ensure_auth_token()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{SLIDES_API_BASE}/presentations/{presentation_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code == 404:
                raise SourceNotFoundError(f"Presentation not found: {presentation_id}")
            if response.status_code != 200:
                raise FetchError(uri, f"Google Slides API error: {response.status_code}")

            data = response.json()

        slides = self._extract_slides_from_presentation(data)

        title = data.get("title", "")
        created_time = data.get("createdTime")
        modified_time = data.get("lastModifiedTime")

        return GoogleSlidesPresentation(
            presentation_id=presentation_id,
            title=title,
            presentation_url=f"https://docs.google.com/presentation/d/{presentation_id}",
            created_at=datetime.fromisoformat(created_time.replace("Z", "+00:00"))
            if created_time
            else None,
            modified_at=datetime.fromisoformat(modified_time.replace("Z", "+00:00"))
            if modified_time
            else None,
            slide_count=len(slides),
            slides=slides,
        )

    def _extract_slides_from_presentation(self, pres_data: dict) -> list[TextDocument]:
        """Extract text from slides."""
        slides_text = []

        for idx, slide in enumerate(pres_data.get("slides", []), start=1):
            slide_text_parts = []

            for element in slide.get("pageElements", []):
                if "shape" in element:
                    shape = element["shape"]
                    if shape.get("shapeType") == "TEXT_BOX":
                        for text_elem in shape.get("text", {}).get("textElements", []):
                            if "textRun" in text_elem:
                                slide_text_parts.append(text_elem["textRun"].get("content", ""))

            full_text = "".join(slide_text_parts)

            slides_text.append(
                TextDocument(
                    source_uri=f"slide_{idx}",
                    content=full_text.strip(),
                    format=TextFormat.PLAIN,
                )
            )

        return slides_text
