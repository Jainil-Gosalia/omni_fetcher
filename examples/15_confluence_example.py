"""Confluence connector example for OmniFetcher."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

from omni_fetcher import OmniFetcher
from omni_fetcher.schemas.confluence import (
    ConfluencePage,
    ConfluenceSpace,
    ConfluenceAttachment,
    ConfluenceUser,
    ConfluenceComment,
)
from omni_fetcher.schemas.atomics import TextDocument, TextFormat


async def main():
    print("=" * 60)
    print("Confluence Connector Examples")
    print("=" * 60)

    confluence_token = os.environ.get("CONFLUENCE_TOKEN")
    if confluence_token:
        fetcher = OmniFetcher(
            auth={
                "confluence": {
                    "type": "bearer",
                    "token_env": "CONFLUENCE_TOKEN",
                }
            }
        )
        print("Using CONFLUENCE_TOKEN for authentication")
        print("\n2. Fetch Examples (with real credentials)")
        print("-" * 40)
        try:
            page = await fetcher.fetch(
                "https://company.atlassian.net/wiki/spaces/SPACE/pages/123456"
            )
            print(f"Fetched page: {page.title}")
            if page.content:
                print(f"Content preview: {page.content.content[:100]}...")
        except Exception as e:
            print(f"Note: Fetch failed (expected without valid page ID): {e}")
    else:
        print("No CONFLUENCE_TOKEN found - showing schema examples only")
        print("To test with real credentials, set the environment variable:")
        print("  export CONFLUENCE_TOKEN='your-api-token'")
        print()

    print("\n3. Schema Examples (without API calls)")
    print("-" * 40)

    page = ConfluencePage(
        page_id="123456",
        title="My Confluence Page",
        space_key="TEAM",
        url="https://company.atlassian.net/wiki/spaces/TEAM/pages/123456",
        icon="📄",
        created_time=datetime.now(),
        updated_at=datetime.now(),
    )
    print(f"ConfluencePage tags: {page.tags}")
    print(f"  Title: {page.title}")
    print(f"  Page ID: {page.page_id}")
    print(f"  Space: {page.space_key}")

    page_with_content = ConfluencePage(
        page_id="123456",
        title="Page With Content",
        space_key="TEAM",
        content=TextDocument(
            source_uri="https://company.atlassian.net/wiki/spaces/TEAM/pages/123456",
            content="# Heading\n\nThis is some **bold** text.",
            format=TextFormat.MARKDOWN,
            language="markdown",
        ),
    )
    print("\nConfluencePage with content:")
    if page_with_content.content:
        print(f"  Content: {page_with_content.content.content[:50]}...")

    user = ConfluenceUser(
        user_id="user123",
        display_name="John Doe",
        username="jdoe",
        email="john@example.com",
    )
    print(f"\nConfluenceUser: {user.display_name} ({user.email})")

    attachment = ConfluenceAttachment(
        attachment_id="att123",
        filename="document.pdf",
        media_type="application/pdf",
        size=1024000,
        url="https://company.atlassian.net/wiki/download/attachments/123456/document.pdf",
    )
    print(f"\nConfluenceAttachment: {attachment.filename}")
    print(f"  Size: {attachment.size} bytes")
    print(f"  Tags: {attachment.tags}")

    space = ConfluenceSpace(
        space_key="TEAM",
        name="Team Space",
        description="A team workspace",
        url="https://company.atlassian.net/wiki/spaces/TEAM",
    )
    print(f"\nConfluenceSpace tags: {space.tags}")
    print(f"  Name: {space.name}")
    print(f"  Space Key: {space.space_key}")
    print(f"  Description: {space.description}")

    space_with_pages = ConfluenceSpace(
        space_key="TEAM",
        name="Team Space with Pages",
        pages=[
            ConfluencePage(
                page_id="111",
                title="Page 1",
                space_key="TEAM",
            ),
            ConfluencePage(
                page_id="222",
                title="Page 2",
                space_key="TEAM",
            ),
        ],
        page_count=2,
    )
    print("\nConfluenceSpace with pages:")
    print(f"  Page count: {space_with_pages.page_count}")
    print(f"  Pages: {[p.title for p in space_with_pages.pages]}")
    print(f"  Merged tags: {space_with_pages.tags}")

    comment = ConfluenceComment(
        comment_id="comment123",
        page_id="123456",
        content=TextDocument(
            source_uri="",
            content="This is a comment",
            format=TextFormat.PLAIN,
        ),
    )
    print(f"\nConfluenceComment: {comment.comment_id}")

    print("\n4. URI Patterns Supported")
    print("-" * 40)
    print("  - https://company.atlassian.net/wiki/spaces/SPACE/pages/123456")
    print("  - https://confluence.company.com/pages/viewpage.action?pageId=123456")
    print("  - https://company.atlassian.net/wiki/spaces/SPACE (space listing)")
    print("  - https://company.atlassian.net/wiki/display/SPACE (space display)")
    print("  - confluence://page-id")

    print("\n5. Self-hosted Confluence")
    print("-" * 40)
    print("For self-hosted (on-premise) Confluence, pass base_url:")
    print("""
    fetcher = OmniFetcher(
        auth={
            "confluence": {
                "type": "bearer",
                "token_env": "CONFLUENCE_TOKEN",
            }
        }
    )
    result = await fetcher.fetch(
        "https://confluence.mycompany.com/pages/123456",
        base_url="https://confluence.mycompany.com"
    )
    """)

    print("\n6. Fetch Options")
    print("-" * 40)
    print("  For pages:")
    print("    - get_content=True/False (default: True)")
    print("      - Fetches page content as HTML and converts to markdown")
    print("  For spaces:")
    print("    - get_pages=True/False (default: True)")
    print("      - Fetches recent pages in the space")
    print("    - get_attachments=True/False (default: False)")
    print("      - Fetches attachments in the space")

    print("\n" + "=" * 60)
    print("Confluence Connector supports:")
    print("  - Confluence pages with HTML content")
    print("  - Confluence spaces as containers with pages")
    print("  - Confluence attachments")
    print("  - HTML to markdown conversion")
    print("  - Bearer token authentication")
    print("  - Self-hosted Confluence via base_url")
    print("  - Cloud Confluence (atlassian.net)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
