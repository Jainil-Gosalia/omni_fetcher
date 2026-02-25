"""Notion fetcher for OmniFetcher."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from omni_fetcher.core.exceptions import FetchError, SourceNotFoundError
from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.atomics import TextDocument, TextFormat, SheetData, SpreadsheetDocument
from omni_fetcher.schemas.notion import (
    NotionPage,
    NotionDatabase,
    NotionBlock,
    NotionUser,
    NotionProperty,
    NotionSearchResult,
)


NOTION_API_BASE = "https://api.notion.com/v1"


@dataclass
class NotionRoute:
    """Parsed Notion URI route."""

    type: str
    page_id: Optional[str] = None
    database_id: Optional[str] = None


def extract_notion_id(uri: str) -> Optional[str]:
    """Extract Notion page/database ID from URI."""
    patterns = [
        r"notion\.so/([a-zA-Z0-9-]{32,})",
        r"notion\.so/([a-zA-Z0-9-]{8,})-[a-zA-Z0-9-]+",
        r"^([a-zA-Z0-9-]{32,})$",
        r"^([a-zA-Z0-9-]{8})-[a-zA-Z0-9-]+-[a-zA-Z0-9-]+-[a-zA-Z0-9-]+-[a-zA-Z0-9-]+$",
    ]

    for pattern in patterns:
        match = re.search(pattern, uri)
        if match:
            return match.group(1).replace("-", "")[:32]

    return None


def parse_notion_uri(uri: str) -> NotionRoute:
    """Parse Notion URI to determine route type."""
    lower_uri = uri.lower()

    if lower_uri.startswith("notion://"):
        page_id = extract_notion_id(uri)
        if page_id:
            return NotionRoute(type="page", page_id=page_id)

    if "notion.so" in lower_uri:
        parsed = urlparse(uri)
        path = parsed.path.strip("/")

        if "?" in path:
            path = path.split("?")[0]

        parts = path.split("-")

        if len(parts) >= 2:
            potential_id = "-".join(parts[:5])
            if len(potential_id.replace("-", "")) >= 32:
                return NotionRoute(type="page", page_id=potential_id.replace("-", "")[:32])

        if len(parts) == 1 and len(parts[0]) >= 32:
            return NotionRoute(type="page", page_id=parts[0][:32])

    page_id = extract_notion_id(uri)
    if page_id:
        return NotionRoute(type="page", page_id=page_id)

    return NotionRoute(type="unknown", page_id=None)


@source(
    name="notion",
    uri_patterns=["notion.so", "notion://"],
    priority=15,
    description="Fetch from Notion — pages and databases",
    auth={"type": "bearer", "token_env": "NOTION_TOKEN"},
)
class NotionFetcher(BaseFetcher):
    """Fetcher for Notion API - pages and databases."""

    name = "notion"
    priority = 15
    BLOCK_TYPE_MAP: dict[str, str] = {}

    def __init__(self, timeout: float = 30.0):
        super().__init__()
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if URI is a Notion URL."""
        if not uri:
            return False
        lower_uri = uri.lower()
        return "notion.so" in lower_uri or lower_uri.startswith("notion://")

    def _get_auth_token(self) -> str:
        """Get authentication token from headers or environment."""
        auth_headers = self.get_auth_headers()
        token = auth_headers.get("Authorization", "").replace("Bearer ", "")

        if not token:
            token = os.environ.get("NOTION_TOKEN", "")

        if not token:
            raise FetchError(
                "notion:// or https://notion.so/...",
                "Notion requires authentication. Set NOTION_TOKEN environment variable.",
            )

        return token

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create httpx client for Notion API."""
        if self._client is None:
            token = self._get_auth_token()
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {token}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                base_url=NOTION_API_BASE,
                timeout=self.timeout,
            )
        return self._client

    async def _close_client(self) -> None:
        """Close the httpx client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch from Notion based on URI type."""
        route = parse_notion_uri(uri)

        if not route.page_id and not route.database_id:
            raise SourceNotFoundError(f"Invalid Notion URI: {uri}")

        try:
            client = self._get_client()
            if route.database_id:
                return await self._fetch_database(client, route.database_id, uri, **kwargs)
            elif route.page_id:
                return await self._fetch_page(client, route.page_id, uri, **kwargs)
            else:
                raise SourceNotFoundError(f"Invalid Notion URI: {uri}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise SourceNotFoundError(f"Notion page or database not found: {uri}")
            raise FetchError(uri, str(e))
        except Exception as e:
            raise FetchError(uri, str(e))
        finally:
            await self._close_client()

    async def _fetch_page(
        self, client: httpx.AsyncClient, page_id: str, uri: str, **kwargs: Any
    ) -> NotionPage:
        """Fetch a Notion page."""
        recursive = kwargs.get("recursive", False)

        response = await client.get(f"/pages/{page_id}")
        response.raise_for_status()
        page_data = response.json()

        title = self._extract_title(page_data)

        icon = None
        if page_data.get("icon"):
            if page_data["icon"].get("emoji"):
                icon = page_data["icon"]["emoji"]
            elif page_data["icon"].get("external"):
                icon = page_data["icon"]["external"].get("url")
            elif page_data["icon"].get("file"):
                icon = page_data["icon"]["file"].get("url")

        cover = None
        if page_data.get("cover"):
            if page_data["cover"].get("external"):
                cover = page_data["cover"]["external"].get("url")
            elif page_data["cover"].get("file"):
                cover = page_data["cover"]["file"].get("url")

        created_time = None
        if page_data.get("created_time"):
            created_time = datetime.fromisoformat(page_data["created_time"].replace("Z", "+00:00"))

        last_edited_time = None
        if page_data.get("last_edited_time"):
            last_edited_time = datetime.fromisoformat(
                page_data["last_edited_time"].replace("Z", "+00:00")
            )

        created_by = None
        if page_data.get("created_by"):
            created_by = NotionUser(
                id=page_data["created_by"].get("id", ""),
                name=page_data["created_by"].get("name"),
                avatar_url=page_data["created_by"].get("avatar_url"),
                type=page_data["created_by"].get("type", "person"),
            )

        last_edited_by = None
        if page_data.get("last_edited_by"):
            last_edited_by = NotionUser(
                id=page_data["last_edited_by"].get("id", ""),
                name=page_data["last_edited_by"].get("name"),
                avatar_url=page_data["last_edited_by"].get("avatar_url"),
                type=page_data["last_edited_by"].get("type", "person"),
            )

        properties = self._parse_properties(page_data.get("properties", {}))

        blocks = []
        content_text = ""

        try:
            blocks = await self._fetch_block_children(client, page_id)

            if recursive:
                blocks = await self._fetch_all_block_children(client, blocks)

            content_text = self._blocks_to_markdown(blocks)
        except Exception:
            pass

        content = None
        if content_text:
            content = TextDocument(
                source_uri=uri,
                content=content_text,
                format=TextFormat.MARKDOWN,
                language="markdown",
            )

        page_url = f"https://notion.so/{page_id.replace('-', '')}"

        return NotionPage(
            page_id=page_id,
            title=title,
            url=page_url,
            icon=icon,
            cover=cover,
            created_time=created_time,
            last_edited_time=last_edited_time,
            created_by=created_by,
            last_edited_by=last_edited_by,
            properties=properties,
            content=content,
            blocks=self._parse_blocks(blocks),
        )

    async def _fetch_database(
        self, client: httpx.AsyncClient, database_id: str, uri: str, **kwargs: Any
    ) -> NotionDatabase:
        """Fetch a Notion database."""
        response = await client.get(f"/databases/{database_id}")
        response.raise_for_status()
        database_data = response.json()

        title = self._extract_title(database_data)

        icon = None
        if database_data.get("icon"):
            if database_data["icon"].get("emoji"):
                icon = database_data["icon"]["emoji"]
            elif database_data["icon"].get("external"):
                icon = database_data["icon"]["external"].get("url")
            elif database_data["icon"].get("file"):
                icon = database_data["icon"]["file"].get("url")

        cover = None
        if database_data.get("cover"):
            if database_data["cover"].get("external"):
                cover = database_data["cover"]["external"].get("url")
            elif database_data["cover"].get("file"):
                cover = database_data["cover"]["file"].get("url")

        created_time = None
        if database_data.get("created_time"):
            created_time = datetime.fromisoformat(
                database_data["created_time"].replace("Z", "+00:00")
            )

        last_edited_time = None
        if database_data.get("last_edited_time"):
            last_edited_time = datetime.fromisoformat(
                database_data["last_edited_time"].replace("Z", "+00:00")
            )

        schema = database_data.get("properties", {})

        items = []
        try:
            response = await client.post(
                f"/databases/{database_id}/query",
                json={"page_size": 100},
            )
            response.raise_for_status()
            pages_data = response.json()
            pages = pages_data.get("results", [])

            for page in pages:
                page_id = page.get("id", "")
                try:
                    item = await self._fetch_page(client, page_id, uri, recursive=False)
                    items.append(item)
                except Exception:
                    pass
        except Exception:
            pass

        spreadsheet_data = None
        if items and schema:
            spreadsheet_data = self._database_to_spreadsheet(schema, items)

        database_url = f"https://notion.so/{database_id.replace('-', '')}"

        return NotionDatabase(
            database_id=database_id,
            title=title,
            url=database_url,
            icon=icon,
            cover=cover,
            created_time=created_time,
            last_edited_time=last_edited_time,
            properties_schema=schema,
            items=items,
            data=spreadsheet_data,
        )

    async def _fetch_block_children(self, client: httpx.AsyncClient, block_id: str) -> list[dict]:
        """Fetch children for a block."""
        response = await client.get(f"/blocks/{block_id}/children")
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])

    async def _fetch_all_block_children(
        self, client: httpx.AsyncClient, blocks: list[dict]
    ) -> list[dict]:
        """Recursively fetch children for blocks that have them."""
        all_blocks = []

        for block in blocks:
            all_blocks.append(block)

            if block.get("has_children"):
                try:
                    children = await self._fetch_block_children(client, block["id"])

                    if children:
                        children_with_grandchildren = await self._fetch_all_block_children(
                            client, children
                        )
                        block["children"] = children_with_grandchildren
                except Exception:
                    pass

        return all_blocks

    def _extract_title(self, data: dict[str, Any]) -> str:
        """Extract title from Notion page or database."""
        properties = data.get("properties", {})

        for prop_name, prop_value in properties.items():
            prop_type = prop_value.get("type")
            if prop_type == "title":
                title_texts = prop_value.get("title", [])
                if title_texts:
                    return self._rich_text_to_plain(title_texts[0])

        for prop_name, prop_value in properties.items():
            prop_type = prop_value.get("type")
            if prop_type == "name":
                name_texts = prop_value.get("name", [])
                if name_texts:
                    return self._rich_text_to_plain(name_texts[0])

        return data.get("title", {}).get("title", [{}])[0].get("plain_text", "Untitled")

    def _parse_properties(self, properties: dict[str, Any]) -> dict[str, NotionProperty]:
        """Parse Notion page properties."""
        result = {}

        for prop_name, prop_value in properties.items():
            prop_type = prop_value.get("type", "unknown")

            value = prop_value.get(prop_type)

            result[prop_name] = NotionProperty(
                id=prop_value.get("id", ""),
                name=prop_name,
                type=prop_type,
                value=value,
                raw=prop_value,
            )

        return result

    def _parse_blocks(self, blocks: list[dict]) -> list[NotionBlock]:
        """Parse Notion blocks."""
        result = []

        for block in blocks:
            block_type = block.get("type", "unknown")
            content = block.get(block_type, {})

            children = []
            if block.get("has_children") and block.get("children"):
                children = self._parse_blocks(block.get("children", []))

            result.append(
                NotionBlock(
                    id=block.get("id", ""),
                    type=block_type,
                    content=content,
                    has_children=block.get("has_children", False),
                    children=children,
                    raw=block,
                )
            )

        return result

    def _blocks_to_markdown(self, blocks: list[dict]) -> str:
        """Convert Notion blocks to markdown."""
        lines = []

        for block in blocks:
            md = self._block_to_markdown(block)
            if md:
                lines.append(md)

        return "\n\n".join(lines)

    def _block_to_markdown(self, block: dict[str, Any]) -> str:
        """Convert a single Notion block to markdown."""
        block_type = block.get("type", "")

        if block_type == "paragraph":
            text = block.get("paragraph", {}).get("rich_text", [])
            return self._rich_text_array_to_markdown(text)

        if block_type == "heading_1":
            text = block.get("heading_1", {}).get("rich_text", [])
            return f"# {self._rich_text_array_to_markdown(text)}"

        if block_type == "heading_2":
            text = block.get("heading_2", {}).get("rich_text", [])
            return f"## {self._rich_text_array_to_markdown(text)}"

        if block_type == "heading_3":
            text = block.get("heading_3", {}).get("rich_text", [])
            return f"### {self._rich_text_array_to_markdown(text)}"

        if block_type == "bulleted_list_item":
            text = block.get("bulleted_list_item", {}).get("rich_text", [])
            return f"- {self._rich_text_array_to_markdown(text)}"

        if block_type == "numbered_list_item":
            text = block.get("numbered_list_item", {}).get("rich_text", [])
            return f"1. {self._rich_text_array_to_markdown(text)}"

        if block_type == "to_do":
            text = block.get("to_do", {}).get("rich_text", [])
            checked = block.get("to_do", {}).get("checked", False)
            checkbox = "[x]" if checked else "[ ]"
            return f"{checkbox} {self._rich_text_array_to_markdown(text)}"

        if block_type == "toggle":
            text = block.get("toggle", {}).get("rich_text", [])
            return f"<details>\n<summary>{self._rich_text_array_to_markdown(text)}</summary>\n</details>"

        if block_type == "code":
            text = block.get("code", {}).get("rich_text", [])
            language = block.get("code", {}).get("language", "")
            code_text = self._rich_text_array_to_markdown(text)
            return f"```{language}\n{code_text}\n```"

        if block_type == "quote":
            text = block.get("quote", {}).get("rich_text", [])
            return f"> {self._rich_text_array_to_markdown(text)}"

        if block_type == "divider":
            return "---"

        if block_type == "callout":
            icon = block.get("callout", {}).get("icon", {})
            emoji = icon.get("emoji", "💡")
            text = block.get("callout", {}).get("rich_text", [])
            return f"{emoji} {self._rich_text_array_to_markdown(text)}"

        if block_type == "image":
            image_data = block.get("image", {})
            image_type = image_data.get("type")
            url = None
            if image_type == "external":
                url = image_data.get("external", {}).get("url")
            elif image_type == "file":
                url = image_data.get("file", {}).get("url")
            caption = self._rich_text_array_to_markdown(image_data.get("caption", []))
            if caption:
                return f"![{caption}]({url})"
            return f"![image]({url})"

        if block_type == "video":
            video_data = block.get("video", {})
            video_type = video_data.get("type")
            url = None
            if video_type == "external":
                url = video_data.get("external", {}).get("url")
            elif video_type == "file":
                url = video_data.get("file", {}).get("url")
            return f"[Video]({url})"

        if block_type == "embed":
            url = block.get("embed", {}).get("url", "")
            return f"[Embed]({url})"

        if block_type == "bookmark":
            url = block.get("bookmark", {}).get("url", "")
            caption = self._rich_text_array_to_markdown(
                block.get("bookmark", {}).get("caption", [])
            )
            if caption:
                return f"[{caption}]({url})"
            return f"[Bookmark]({url})"

        if block_type == "link_preview":
            url = block.get("link_preview", {}).get("url", "")
            return f"[Link Preview]({url})"

        if block_type == "table":
            return "[Table]"

        if block_type == "table_row":
            cells = block.get("table_row", {}).get("cells", [])
            row_text = " | ".join(self._rich_text_array_to_markdown(cell) for cell in cells)
            return f"| {row_text} |"

        return ""

    def _rich_text_array_to_markdown(self, rich_text: list[dict]) -> str:
        """Convert rich text array to markdown."""
        if not rich_text:
            return ""

        result = []
        for text in rich_text:
            result.append(self._rich_text_to_markdown(text))

        return "".join(result)

    def _rich_text_to_markdown(self, text: dict[str, Any]) -> str:
        """Convert single rich text element to markdown."""
        plain_text = text.get("plain_text", "")
        annotations = text.get("annotations", {})
        href = text.get("href")

        if href:
            return f"[{plain_text}]({href})"

        if annotations.get("bold"):
            plain_text = f"**{plain_text}**"
        if annotations.get("italic"):
            plain_text = f"*{plain_text}*"
        if annotations.get("strikethrough"):
            plain_text = f"~~{plain_text}~~"
        if annotations.get("underline"):
            plain_text = f"__{plain_text}__"
        if annotations.get("code"):
            plain_text = f"`{plain_text}`"

        return plain_text

    def _rich_text_to_plain(self, text: dict[str, Any]) -> str:
        """Convert rich text to plain text."""
        return text.get("plain_text", "")

    def _database_to_spreadsheet(
        self, schema: dict[str, Any], items: list[NotionPage]
    ) -> SpreadsheetDocument:
        """Convert Notion database to SpreadsheetDocument."""
        headers = list(schema.keys())

        rows = []
        for item in items:
            row = []
            for prop_name in headers:
                prop = item.properties.get(prop_name)
                if prop:
                    row.append(str(prop.value))
                else:
                    row.append("")
            rows.append(row)

        sheet = SheetData(
            name="Notion Database",
            headers=headers,
            rows=rows,
            row_count=len(rows),
            col_count=len(headers),
        )

        return SpreadsheetDocument(
            source_uri="",
            sheets=[sheet],
            format="notion",
            sheet_count=1,
        )

    async def search(
        self,
        query: str | None = None,
        object_type: str | None = None,
        limit: int = 100,
    ) -> list[NotionSearchResult]:
        """Search all accessible pages and databases in Notion.

        Args:
            query: Optional text search query
            object_type: Filter by "page", "database", or None for both
            limit: Maximum number of results to return (default 100)

        Returns:
            List of NotionSearchResult objects

        Raises:
            FetchError: If authentication fails or API call fails
        """
        client = self._get_client()

        request_json: dict[str, Any] = {
            "query": query or "",
            "page_size": min(limit, 100),
        }

        if object_type in ("page", "database"):
            request_json["filter"] = {"value": object_type, "property": "object"}

        try:
            response = await client.post("/search", json=request_json)
            response.raise_for_status()
            results = response.json()
        except httpx.HTTPStatusError as e:
            raise FetchError("notion://search", str(e)) from e
        except Exception as e:
            raise FetchError("notion://search", str(e)) from e

        search_results: list[NotionSearchResult] = []

        for item in results.get("results", []):
            obj_type = item.get("object")
            if obj_type not in ("page", "database"):
                continue

            title = ""
            if obj_type == "page":
                title = self._extract_title(item)
            elif obj_type == "database":
                title_db = item.get("title", [])
                if title_db:
                    title = title_db[0].get("plain_text", "")

            icon = None
            if item.get("icon"):
                if item["icon"].get("emoji"):
                    icon = item["icon"]["emoji"]
                elif item["icon"].get("external"):
                    icon = item["icon"]["external"].get("url")
                elif item["icon"].get("file"):
                    icon = item["icon"]["file"].get("url")

            obj_id = item.get("id", "")
            url = f"https://notion.so/{obj_id.replace('-', '')}" if obj_id else None

            search_results.append(
                NotionSearchResult(
                    id=obj_id,
                    title=title or "Untitled",
                    object_type=obj_type,
                    url=url,
                    icon=icon,
                )
            )

        return search_results
