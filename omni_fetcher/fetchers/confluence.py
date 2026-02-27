"""Confluence fetcher for OmniFetcher."""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

from omni_fetcher.core.exceptions import FetchError, SourceNotFoundError
from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.atomics import TextDocument, TextFormat
from omni_fetcher.schemas.confluence import (
    ConfluencePage,
    ConfluenceSpace,
    ConfluenceAttachment,
    ConfluenceUser,
)

try:
    from atlassian import Confluence as AtlassianConfluence

    ATLASSIAN_AVAILABLE = True
except ImportError:
    ATLASSIAN_AVAILABLE = False


CONFLUENCE_CLOUD_URL = "https://api.atlassian.com"


def extract_confluence_id(uri: str) -> Optional[str]:
    """Extract Confluence page/space ID from URI."""
    patterns = [
        r"/spaces/([A-Z0-9]+)/pages/(\d+)",
        r"/pages/(\d+)",
        r"/display/([A-Z0-9]+)/",
        r"/display/([A-Z0-9]+)/([^\s/]+)",
        r"pageId=(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, uri)
        if match:
            return match.group(1) if match.lastindex and match.lastindex >= 1 else match.group(0)

    return None


def extract_space_key(uri: str) -> Optional[str]:
    """Extract Confluence space key from URI."""
    patterns = [
        r"/spaces/([A-Z0-9]+)",
        r"/display/([A-Z0-9]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, uri)
        if match:
            return match.group(1)

    return None


def parse_confluence_uri(uri: str) -> dict[str, Any]:
    """Parse Confluence URI to determine resource type."""
    parsed = urlparse(uri)
    path = parsed.path

    if not path or path == "/" or path.strip("/") == "":
        return {"type": "root"}

    if path.strip("/") == "wiki":
        return {"type": "root"}

    page_match = re.search(r"/pages/(\d+)", path)
    if page_match:
        return {"type": "page", "page_id": page_match.group(1)}

    space_match = re.search(r"/spaces/(~[a-fA-F0-9]+)", path)
    if space_match:
        return {"type": "space", "space_key": space_match.group(1)}

    space_match = re.search(r"/spaces/([A-Z0-9]+)", path)
    if space_match:
        return {"type": "space", "space_key": space_match.group(1)}

    space_match2 = re.search(r"/display/([A-Z0-9]+)/?", path)
    if space_match2:
        return {"type": "space", "space_key": space_match2.group(1)}

    return {"type": "unknown"}


@source(
    name="confluence",
    uri_patterns=["confluence.com", "atlassian.net", "confluence://"],
    priority=15,
    description="Fetch from Confluence — pages, spaces, and attachments",
    auth={"type": "bearer", "token_env": "CONFLUENCE_TOKEN"},
)
class ConfluenceFetcher(BaseFetcher):
    """Fetcher for Confluence API - pages, spaces, and attachments."""

    name = "confluence"
    priority = 15

    def __init__(self, timeout: float = 30.0):
        super().__init__()
        self.timeout = timeout
        self._client: Optional[AtlassianConfluence] = None

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if URI is a Confluence URL."""
        if not uri:
            return False
        lower_uri = uri.lower()
        return (
            "confluence" in lower_uri
            or "atlassian.net" in lower_uri
            or lower_uri.startswith("confluence://")
        )

    def _get_client(self, base_url: Optional[str] = None) -> AtlassianConfluence:
        """Get or create Confluence client."""
        if not ATLASSIAN_AVAILABLE:
            raise ImportError(
                "atlassian-python-api is not installed. Install with: pip install atlassian-python-api"
            )

        auth_headers = self.get_auth_headers()
        token = auth_headers.get("Authorization", "").replace("Bearer ", "")

        if not token:
            token = os.environ.get("CONFLUENCE_TOKEN", "")

        if not token:
            raise FetchError(
                "confluence:// or https://company.atlassian.net/wiki/...",
                "Confluence requires authentication. Set CONFLUENCE_TOKEN environment variable.",
            )

        username = os.environ.get("CONFLUENCE_USER", "")

        url = base_url or os.environ.get("CONFLUENCE_URL", CONFLUENCE_CLOUD_URL)

        if username:
            return AtlassianConfluence(
                url=url,
                username=username,
                password=token,
                timeout=self.timeout,
            )

        return AtlassianConfluence(
            url=url,
            token=token,
            timeout=self.timeout,
        )

    def _html_to_markdown(self, html: str) -> str:
        """Convert Confluence HTML to markdown using beautifulsoup4."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        for pre in soup.find_all("pre"):
            code = pre.find("code")
            if code:
                class_attr = code.get("class") or []
                lang = ""
                if isinstance(class_attr, list):
                    for c in class_attr:
                        if isinstance(c, str) and c.startswith("language-"):
                            lang = c.replace("language-", "")
                            break
                pre.replace_with(BeautifulSoup(f"```{lang}\n{code.get_text()}\n```", "html.parser"))

        for h1 in soup.find_all("h1"):
            h1.replace_with(BeautifulSoup(f"# {h1.get_text()}", "html.parser"))

        for h2 in soup.find_all("h2"):
            h2.replace_with(BeautifulSoup(f"## {h2.get_text()}", "html.parser"))

        for h3 in soup.find_all("h3"):
            h3.replace_with(BeautifulSoup(f"### {h3.get_text()}", "html.parser"))

        for ul in soup.find_all("ul"):
            items = ul.find_all("li")
            if items:
                md_items = "\n".join(f"- {item.get_text()}" for item in items)
                ul.replace_with(BeautifulSoup(md_items, "html.parser"))

        for ol in soup.find_all("ol"):
            items = ol.find_all("li")
            if items:
                md_items = "\n".join(f"{i + 1}. {item.get_text()}" for i, item in enumerate(items))
                ol.replace_with(BeautifulSoup(md_items, "html.parser"))

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if rows:
                md_table = "\n".join(
                    "| " + " | ".join(cell.get_text() for cell in row.find_all(["th", "td"])) + " |"
                    for row in rows
                )
                table.replace_with(BeautifulSoup(md_table, "html.parser"))

        return soup.get_text()

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch from Confluence based on URI type."""
        if not ATLASSIAN_AVAILABLE:
            raise ImportError(
                "atlassian-python-api is not installed. Install with: pip install atlassian-python-api"
            )

        route = parse_confluence_uri(uri)

        if route["type"] == "unknown":
            raise SourceNotFoundError(f"Invalid Confluence URI: {uri}")

        base_url = kwargs.get("base_url")

        client = self._get_client(base_url)

        try:
            if route["type"] == "space":
                return await self._fetch_space(client, route["space_key"], uri, **kwargs)
            elif route["type"] == "page":
                return await self._fetch_page(client, route["page_id"], uri, **kwargs)
            elif route["type"] == "root":
                return await self._fetch_root(client, uri, **kwargs)
        except Exception as e:
            if "404" in str(e) or "Not Found" in str(e):
                raise SourceNotFoundError(f"Confluence resource not found: {uri}")
            raise FetchError(uri, str(e))

    async def _fetch_page(
        self, client: AtlassianConfluence, page_id: str, uri: str, **kwargs: Any
    ) -> ConfluencePage:
        """Fetch a Confluence page."""
        import asyncio

        get_content = kwargs.get("get_content", True)

        page_data = await asyncio.to_thread(client.get_page_by_id, page_id, expand="body.storage")

        if not page_data:
            raise FetchError(uri, "Failed to retrieve Confluence page")

        title = page_data.get("title", "Untitled")
        space_key = page_data.get("space", {}).get("key") if page_data.get("space") else None

        icon = None
        if page_data.get("icon"):
            icon = page_data.get("icon", {}).get("emoji")

        parent_id = None
        if page_data.get("ancestors"):
            parent_id = page_data["ancestors"][0].get("id")

        version = page_data.get("version", {}).get("number", 1) if page_data.get("version") else 1

        created_at = None
        if page_data.get("createdAt"):
            created_at = datetime.fromisoformat(page_data["createdAt"].replace("Z", "+00:00"))

        updated_at = None
        if page_data.get("version", {}).get("createdAt"):
            updated_at = datetime.fromisoformat(
                page_data["version"]["createdAt"].replace("Z", "+00:00")
            )

        created_by = None
        if page_data.get("version", {}).get("by"):
            user_data = page_data["version"]["by"]
            created_by = ConfluenceUser(
                user_id=user_data.get("accountId", ""),
                display_name=user_data.get("displayName", ""),
                username=user_data.get("publicName"),
                email=user_data.get("email"),
                profile_picture=user_data.get("profilePicture", {}).get("path")
                if isinstance(user_data.get("profilePicture"), dict)
                else None,
            )

        content_html = ""
        content_text = ""
        if get_content and page_data.get("body", {}).get("storage"):
            content_html = page_data["body"]["storage"].get("value", "")
            content_text = self._html_to_markdown(content_html)

        content = None
        if content_text:
            content = TextDocument(
                source_uri=uri,
                content=content_text,
                format=TextFormat.MARKDOWN,
                language="markdown",
            )

        page_url = page_data.get("_links", {}).get("webui")
        if page_url:
            page_url = f"https://{self._get_domain_from_client(uri)}{page_url}"

        return ConfluencePage(
            page_id=str(page_id),
            title=title,
            space_key=space_key,
            url=page_url,
            icon=icon,
            parent_id=parent_id,
            version=version,
            created_at=created_at,
            updated_at=updated_at,
            created_by=created_by,
            last_updated_by=created_by,
            content=content,
        )

    async def _fetch_space(
        self, client: AtlassianConfluence, space_key: str, uri: str, **kwargs: Any
    ) -> ConfluenceSpace:
        """Fetch a Confluence space with its pages."""
        import asyncio

        get_pages = kwargs.get("get_pages", True)
        get_attachments = kwargs.get("get_attachments", False)

        space_data = await asyncio.to_thread(client.get_space, space_key, expand="description")

        if not space_data:
            raise FetchError(uri, f"Failed to retrieve Confluence space: {space_key}")

        name = space_data.get("name", space_key)
        description = (
            space_data.get("description", {}).get("plain", {}).get("value")
            if isinstance(space_data.get("description"), dict)
            else None
        )
        space_type = space_data.get("type", "global")
        status = space_data.get("status", "current")

        homepage_id = None
        if space_data.get("homepage"):
            homepage_id = str(space_data.get("homepage", {}).get("id"))

        created_at = None
        if space_data.get("createdAt"):
            created_at = datetime.fromisoformat(space_data["createdAt"].replace("Z", "+00:00"))

        updated_at = None
        if space_data.get("createdAt"):
            updated_at = datetime.fromisoformat(space_data["createdAt"].replace("Z", "+00:00"))

        icon = None
        if space_data.get("icon"):
            icon = space_data.get("icon", {}).get("emoji")

        space_url = space_data.get("_links", {}).get("webui")
        if space_url:
            space_url = f"https://{self._get_domain_from_client(uri)}{space_url}"

        pages = []
        page_count = 0
        if get_pages:
            try:
                cql = f"space={space_key} AND type=page"
                search_results = await asyncio.to_thread(
                    client.get, "rest/api/content", params={"cql": cql, "limit": 25}
                )
                results = search_results.get("results", []) if search_results else []
                page_count = search_results.get("size", 0) if search_results else 0

                for result in results[:10]:
                    try:
                        page = await self._fetch_page(client, result["id"], uri, get_content=False)
                        pages.append(page)
                    except Exception:
                        pass
            except Exception:
                pass

        attachments = []
        attachment_count = 0
        if get_attachments:
            try:
                attachments_data = await asyncio.to_thread(client.get_attachments, space_key)
                if attachments_data:
                    for att in attachments_data.get("results", [])[:10]:
                        attachment = ConfluenceAttachment(
                            attachment_id=att.get("id", ""),
                            filename=att.get("filename", ""),
                            media_type=att.get("mediaType", "application/octet-stream"),
                            size=att.get("fileSize", 0),
                            web_url=att.get("_links", {}).get("webui"),
                        )
                        attachments.append(attachment)
                    attachment_count = attachments_data.get("size", 0)
            except Exception:
                pass

        return ConfluenceSpace(
            space_key=space_key,
            name=name,
            description=description,
            url=space_url,
            icon=icon,
            type=space_type,
            status=status,
            homepage_id=homepage_id,
            created_at=created_at,
            updated_at=updated_at,
            pages=pages,
            attachments=attachments,
            source_uri=uri,
            page_count=page_count,
            attachment_count=attachment_count,
        )

    async def _fetch_root(
        self, client: AtlassianConfluence, uri: str, **kwargs: Any
    ) -> ConfluenceSpace:
        """Fetch Confluence home/root - returns user's personal space or first accessible space."""
        import asyncio

        get_pages = kwargs.get("get_pages", True)

        try:
            user = await asyncio.to_thread(client.get_myself)
            account_id = user.get("accountId", "")
        except Exception:
            account_id = ""

        if account_id:
            personal_space_key = f"~{account_id}"
            try:
                return await self._fetch_space(client, personal_space_key, uri, get_pages=get_pages)
            except Exception:
                pass

        try:
            spaces_data = await asyncio.to_thread(
                client.get, "rest/api/space", params={"limit": 10}
            )
            results = spaces_data.get("results", []) if spaces_data else []

            if results:
                first_space_key = results[0].get("key")
                if first_space_key:
                    return await self._fetch_space(
                        client, first_space_key, uri, get_pages=get_pages
                    )
        except Exception as e:
            raise FetchError(uri, f"Could not fetch spaces: {e}")

        raise FetchError(uri, "Could not fetch Confluence home - no accessible spaces")

    def _get_domain_from_client(self, uri: str) -> str:
        """Extract domain from URI for building URLs."""
        parsed = urlparse(uri)
        if parsed.netloc and "atlassian.net" in parsed.netloc:
            return parsed.netloc
        return "company.atlassian.net/wiki"
