"""Notion connector example for OmniFetcher."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

from omni_fetcher import OmniFetcher
from omni_fetcher.schemas.notion import (
    NotionPage,
    NotionDatabase,
    NotionBlock,
    NotionRichText,
    NotionUser,
    NotionProperty,
)
from omni_fetcher.schemas.atomics import TextDocument, SpreadsheetDocument, SheetData, TextFormat


async def main():
    print("=" * 60)
    print("Notion Connector Examples")
    print("=" * 60)

    notion_token = os.environ.get("NOTION_TOKEN")
    if notion_token:
        fetcher = OmniFetcher(
            auth={
                "notion": {
                    "type": "bearer",
                    "token_env": "NOTION_TOKEN",
                }
            }
        )
        print("Using NOTION_TOKEN for authentication")
        print("\n2. Fetch Examples (with real credentials)")
        print("-" * 40)
        try:
            page = await fetcher.fetch("https://notion.so/page-id")
            print(f"Fetched page: {page.title}")
            if page.content:
                print(f"Content preview: {page.content.content[:100]}...")
        except Exception as e:
            print(f"Note: Fetch failed (expected without valid page ID): {e}")
    else:
        print("No NOTION_TOKEN found - showing schema examples only")
        print("To test with real credentials, set the environment variable:")
        print("  export NOTION_TOKEN='secret_...'")
        print()

    print("\n3. Schema Examples (without API calls)")
    print("-" * 40)

    page = NotionPage(
        page_id="abc123def456",
        title="My Notion Page",
        url="https://notion.so/abc123def456",
        icon="📄",
        created_time=datetime.now(),
        last_edited_time=datetime.now(),
    )
    print(f"NotionPage tags: {page.tags}")
    print(f"  Title: {page.title}")
    print(f"  Page ID: {page.page_id}")
    print(f"  Icon type: {type(page.icon).__name__ if page.icon else 'None'}")

    page_with_content = NotionPage(
        page_id="abc123def456",
        title="Page With Content",
        url="https://notion.so/abc123def456",
        content=TextDocument(
            source_uri="https://notion.so/abc123def456",
            content="# Heading\n\nThis is some **bold** text.",
            format=TextFormat.MARKDOWN,
            language="markdown",
        ),
    )
    print(f"\nNotionPage with content:")
    if page_with_content.content:
        print(f"  Content: {page_with_content.content.content[:50]}...")

    user = NotionUser(
        id="user123",
        name="John Doe",
        avatar_url="https://example.com/avatar.png",
        type="person",
    )
    print(f"\nNotionUser: {user.name} ({user.type})")

    prop = NotionProperty(
        id="prop123",
        name="Status",
        type="select",
        value={"id": "status1", "name": "In Progress"},
        raw={},
    )
    print(f"NotionProperty: {prop.name} = {prop.value}")

    rich_text = NotionRichText(
        plain_text="Hello world",
        markdown="**Hello world**",
        annotations_bold=True,
    )
    print(f"NotionRichText: {rich_text.markdown}")

    block = NotionBlock(
        id="block123",
        type="paragraph",
        content={"rich_text": [{"plain_text": "Block content", "annotations": {}}]},
        has_children=False,
    )
    print(f"NotionBlock: {block.type}")

    database = NotionDatabase(
        database_id="db123abc",
        title="My Database",
        url="https://notion.so/db123abc",
        icon="📊",
    )
    print(f"\nNotionDatabase tags: {database.tags}")
    print(f"  Title: {database.title}")
    print(f"  Database ID: {database.database_id}")

    sheet_data = SheetData(
        name="Notion Database",
        headers=["Name", "Status", "Tags"],
        rows=[["Task 1", "Done", "work"], ["Task 2", "In Progress", "home"]],
        row_count=2,
        col_count=3,
    )
    spreadsheet_doc = SpreadsheetDocument(
        source_uri="https://notion.so/db123abc",
        sheets=[sheet_data],
        format="notion",
        sheet_count=1,
    )

    database_with_data = NotionDatabase(
        database_id="db123abc",
        title="My Database with Data",
        url="https://notion.so/db123abc",
        schema={
            "Name": {"type": "title"},
            "Status": {"type": "select"},
            "Tags": {"type": "multi_select"},
        },
        data=spreadsheet_doc,
    )
    print(f"\nNotionDatabase with data:")
    print(f"  Schema keys: {list(database_with_data.properties_schema.keys())}")
    if database_with_data.data:
        print(f"  Sheet rows: {database_with_data.data.sheets[0].row_count}")

    print("\n4. URI Patterns Supported")
    print("-" * 40)
    print("  - https://notion.so/page-id")
    print("  - https://notion.so/workspace/page-name-32charid")
    print("  - notion://page-id")

    print("\n5. Fetch Options")
    print("-" * 40)
    print("  For pages:")
    print("    - recursive=True/False (default: False)")
    print("      - Fetches block children recursively")
    print("  For databases:")
    print("    - Database items are fetched as NotionPage objects")
    print("    - Data is also available as SpreadsheetDocument")

    print("\n6. Block Types Supported (18 types)")
    print("-" * 40)
    print("  - paragraph, heading_1, heading_2, heading_3")
    print("  - bulleted_list_item, numbered_list_item")
    print("  - to_do, toggle")
    print("  - code, quote, divider")
    print("  - callout, image, video, embed")
    print("  - bookmark, link_preview")
    print("  - table, table_row")

    print("\n" + "=" * 60)
    print("Notion Connector supports:")
    print("  - Notion pages with block content")
    print("  - Notion databases as spreadsheet data")
    print("  - Block to markdown conversion")
    print("  - Rich text with annotations (bold, italic, etc.)")
    print("  - Bearer token authentication")
    print("  - Recursive block fetching")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
