"""Google Drive/Sheets/Docs/Slides connector example for OmniFetcher."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

from omni_fetcher import OmniFetcher
from omni_fetcher.schemas.google import (
    GoogleDriveFile,
    GoogleDriveFolder,
    GoogleSheetsSpreadsheet,
    GoogleDocsDocument,
    GoogleSlidesPresentation,
)
from omni_fetcher.schemas.atomics import TextDocument, SpreadsheetDocument, SheetData, TextFormat


async def main():
    print("=" * 60)
    print("Google Drive/Sheets/Docs/Slides Connector Examples")
    print("=" * 60)

    service_account_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if service_account_env:
        fetcher = OmniFetcher(
            auth={
                "google_drive": {
                    "type": "google_service_account",
                    "google_service_account_env": "GOOGLE_SERVICE_ACCOUNT_JSON",
                }
            }
        )
        print("Using GOOGLE_SERVICE_ACCOUNT_JSON for authentication")
        print("\n2. Fetch Examples (with real credentials)")
        print("-" * 40)
        try:
            doc = await fetcher.fetch("https://docs.google.com/document/d/EXAMPLE_DOC_ID")
            print(f"Fetched document: {doc.title}")
        except Exception as e:
            print(f"Note: Fetch failed (expected without real credentials): {e}")
    else:
        print("No GOOGLE_SERVICE_ACCOUNT_JSON found - showing schema examples only")
        print("To test with real credentials, set the environment variable:")
        print('  export GOOGLE_SERVICE_ACCOUNT_JSON=\'{"type": "service_account", ...}\'')
        print()

    print("\n3. Schema Examples (without API calls)")
    print("-" * 40)

    drive_file = GoogleDriveFile(
        file_id="1abc123DEF456",
        name="document.pdf",
        mime_type="application/pdf",
        size=1024000,
        created_time=datetime.now(),
        modified_time=datetime.now(),
        parents=["root"],
        web_view_link="https://drive.google.com/file/d/1abc123DEF456",
    )
    print(f"GoogleDriveFile tags: {drive_file.tags}")
    print(f"  Name: {drive_file.name}")
    print(f"  MIME type: {drive_file.mime_type}")
    print(f"  Size: {drive_file.size} bytes")

    drive_folder = GoogleDriveFolder(
        folder_id="folder123",
        name="My Documents",
        created_time=datetime.now(),
        modified_time=datetime.now(),
        web_view_link="https://drive.google.com/drive/folders/folder123",
        files=[drive_file],
        file_count=1,
        folder_count=0,
    )
    print(f"\nGoogleDriveFolder tags: {drive_folder.tags}")
    print(f"  Name: {drive_folder.name}")
    print(f"  Files: {drive_folder.file_count}")

    sheet_data = SheetData(
        name="Sheet1",
        headers=["Name", "Age", "City"],
        rows=[["Alice", "30", "NYC"], ["Bob", "25", "LA"]],
        row_count=2,
        col_count=3,
    )
    spreadsheet_doc = SpreadsheetDocument(
        source_uri="https://docs.google.com/spreadsheets/d/abc123",
        sheets=[sheet_data],
        format="csv",
        sheet_count=1,
    )

    spreadsheet = GoogleSheetsSpreadsheet(
        spreadsheet_id="abc123DEF456",
        spreadsheet_title="Employee Data",
        sheets=["Sheet1"],
        spreadsheet_url="https://docs.google.com/spreadsheets/d/abc123",
        created_at=datetime.now(),
        modified_at=datetime.now(),
        data=spreadsheet_doc,
    )
    print(f"\nGoogleSheetsSpreadsheet tags: {spreadsheet.tags}")
    print(f"  Title: {spreadsheet.spreadsheet_title}")
    print(f"  Sheets: {spreadsheet.sheets}")
    if spreadsheet.data:
        print(f"  Sheet rows: {spreadsheet.data.sheets[0].row_count}")

    doc_text = TextDocument(
        source_uri="https://docs.google.com/document/d/abc123",
        content="# My Document\n\nThis is the content.",
        format=TextFormat.MARKDOWN,
        language="markdown",
    )
    doc = GoogleDocsDocument(
        document_id="abc123DEF456",
        title="My Document",
        document_url="https://docs.google.com/document/d/abc123",
        created_at=datetime.now(),
        modified_at=datetime.now(),
        text=doc_text,
    )
    print(f"\nGoogleDocsDocument tags: {doc.tags}")
    print(f"  Title: {doc.title}")
    print(f"  Content preview: {doc.text.content[:50]}...")

    slide1 = TextDocument(
        source_uri="slide_1",
        content="Welcome to the presentation",
        format=TextFormat.PLAIN,
    )
    slide2 = TextDocument(
        source_uri="slide_2",
        content="Key points:\n- Point 1\n- Point 2\n- Point 3",
        format=TextFormat.PLAIN,
    )
    slides = GoogleSlidesPresentation(
        presentation_id="abc123DEF456",
        title="My Presentation",
        presentation_url="https://docs.google.com/presentation/d/abc123",
        created_at=datetime.now(),
        modified_at=datetime.now(),
        slide_count=2,
        slides=[slide1, slide2],
    )
    print(f"\nGoogleSlidesPresentation tags: {slides.tags}")
    print(f"  Title: {slides.title}")
    print(f"  Slide count: {slides.slide_count}")
    print(f"  First slide: {slides.slides[0].content[:30]}...")

    print("\n4. URI Patterns Supported")
    print("-" * 40)
    print("  - https://drive.google.com/file/d/FILE_ID")
    print("  - https://drive.google.com/uc?id=FILE_ID")
    print("  - https://docs.google.com/document/d/FILE_ID")
    print("  - https://docs.google.com/spreadsheets/d/FILE_ID")
    print("  - https://docs.google.com/presentation/d/FILE_ID")

    print("\n5. Fetch Options")
    print("-" * 40)
    print("  For folders:")
    print("    - recursive=True/False (default: False)")
    print("    - max_files (default: 100)")
    print("  For spreadsheets:")
    print("    - sheet_name (default: 'Sheet1')")
    print("    - export_as_csv (default: True)")

    print("\n" + "=" * 60)
    print("Google Drive Connector supports:")
    print("  - Google Drive files and folders")
    print("  - Google Sheets (exported as CSV/JSON)")
    print("  - Google Docs (markdown text)")
    print("  - Google Slides (text per slide)")
    print("  - Service account authentication")
    print("  - Recursive folder fetching")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
