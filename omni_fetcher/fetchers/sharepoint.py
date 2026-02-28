"""SharePoint fetcher for OmniFetcher."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse, unquote

import httpx

from omni_fetcher.core.exceptions import FetchError, SourceNotFoundError
from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.atomics import TextDocument, TextFormat
from omni_fetcher.schemas.sharepoint import SharePointFile, SharePointLibrary, SharePointSite


GRAPH_BASE = "https://graph.microsoft.com/v1.0"


@dataclass
class SharePointRoute:
    """Parsed SharePoint URI route."""

    type: str
    hostname: str
    site_name: str
    library_name: Optional[str] = None
    file_name: Optional[str] = None


def parse_sharepoint_uri(uri: str) -> SharePointRoute:
    """Parse a SharePoint URI into route components.

    Supports both sharepoint:// protocol and https://*.sharepoint.com URLs.

    Args:
        uri: SharePoint URI or URL

    Returns:
        SharePointRoute with parsed components

    Raises:
        SourceNotFoundError: If URI format is invalid
    """
    uri = uri.strip()

    if uri.startswith("sharepoint://"):
        return _parse_sharepoint_protocol(uri)

    if "sharepoint.com" in uri.lower():
        return _parse_sharepoint_url(uri)

    raise SourceNotFoundError(f"Invalid SharePoint URI: {uri}")


def _parse_sharepoint_protocol(uri: str) -> SharePointRoute:
    """Parse sharepoint:// protocol URI."""
    uri = uri.replace("sharepoint://", "")

    if uri.startswith("sites/"):
        uri = uri[6:]

    if "/" not in uri:
        return SharePointRoute(
            type="site",
            hostname="",
            site_name=uri,
        )

    parts = uri.split("/", 1)
    site_name = parts[0]
    remainder = parts[1] if len(parts) > 1 else ""

    if "/" in remainder:
        library_name, file_name = remainder.split("/", 1)
        return SharePointRoute(
            type="file",
            hostname="",
            site_name=site_name,
            library_name=library_name,
            file_name=unquote(file_name),
        )

    return SharePointRoute(
        type="library",
        hostname="",
        site_name=site_name,
        library_name=remainder,
    )


def _parse_sharepoint_url(uri: str) -> SharePointRoute:
    """Parse https://*.sharepoint.com URL."""
    parsed = urlparse(uri)
    path = parsed.path.strip("/")
    hostname = parsed.netloc.lower()

    path_parts = path.split("/") if path else []

    if not path_parts or path_parts[0] != "sites":
        if len(path_parts) >= 1:
            site_name = path_parts[0]
            if len(path_parts) >= 2:
                library_name = path_parts[1]
                if len(path_parts) >= 3:
                    file_name = "/".join(path_parts[2:])
                    return SharePointRoute(
                        type="file",
                        hostname=hostname,
                        site_name=site_name,
                        library_name=library_name,
                        file_name=unquote(file_name),
                    )
                return SharePointRoute(
                    type="library",
                    hostname=hostname,
                    site_name=site_name,
                    library_name=library_name,
                )
            return SharePointRoute(
                type="site",
                hostname=hostname,
                site_name=site_name,
            )

    if len(path_parts) >= 2:
        site_name = path_parts[1]

        shared_docs_match = re.search(r"Shared%20Documents|Shared Documents", path)
        if shared_docs_match:
            library_name = "Shared Documents"
            remaining = path.split("Shared Documents", 1)
            if len(remaining) > 1 and remaining[1].strip("/"):
                file_name = unquote(remaining[1].strip("/"))
                return SharePointRoute(
                    type="file",
                    hostname=hostname,
                    site_name=site_name,
                    library_name=library_name,
                    file_name=file_name,
                )
            return SharePointRoute(
                type="library",
                hostname=hostname,
                site_name=site_name,
                library_name=library_name,
            )

        if len(path_parts) >= 3:
            library_name = path_parts[2]
            if len(path_parts) >= 4:
                file_name = "/".join(path_parts[3:])
                return SharePointRoute(
                    type="file",
                    hostname=hostname,
                    site_name=site_name,
                    library_name=library_name,
                    file_name=unquote(file_name),
                )
            return SharePointRoute(
                type="library",
                hostname=hostname,
                site_name=site_name,
                library_name=library_name,
            )

        return SharePointRoute(
            type="site",
            hostname=hostname,
            site_name=site_name,
        )

    raise SourceNotFoundError(f"Could not parse SharePoint URL: {uri}")


@source(
    name="sharepoint",
    uri_patterns=[
        "sharepoint.com",
        "sharepoint://",
    ],
    priority=15,
    description="Fetch from SharePoint - sites, libraries, and files",
    auth={"type": "azure_client_credentials"},
)
class SharePointFetcher(BaseFetcher):
    """Fetcher for Microsoft SharePoint via Graph API."""

    name = "sharepoint"
    priority = 15

    def __init__(self, timeout: float = 60.0):
        super().__init__()
        self.timeout = timeout
        self._access_token: Optional[str] = None

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if URI is a SharePoint URL."""
        if not uri:
            return False
        lower_uri = uri.lower()
        if "sharepoint.com" not in lower_uri and not lower_uri.startswith("sharepoint://"):
            return False
        return "-my.sharepoint.com" not in lower_uri

    async def _get_token(self) -> str:
        """Get OAuth2 access token using client credentials flow."""
        if self._access_token:
            return self._access_token

        client_id = os.environ.get("AZURE_CLIENT_ID")
        client_secret = os.environ.get("AZURE_CLIENT_SECRET")
        tenant_id = os.environ.get("AZURE_TENANT_ID")

        if not client_id or not client_secret or not tenant_id:
            raise FetchError(
                "",
                "Azure client credentials not configured. Set AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, and AZURE_TENANT_ID.",
            )

        token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                token_url,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )

        if response.status_code != 200:
            raise FetchError("", f"Failed to get OAuth token: {response.text}")

        token_data = response.json()
        access_token: str = token_data.get("access_token", "")
        if not access_token:
            raise FetchError("", "No access token in response")
        self._access_token = access_token
        return self._access_token

    async def _graph(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Make a GET request to the Graph API."""
        token = await self._get_token()
        url = f"{GRAPH_BASE}{endpoint}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )

        if response.status_code == 404:
            raise SourceNotFoundError(f"Resource not found: {endpoint}")
        if response.status_code != 200:
            raise FetchError(endpoint, f"Graph API error: {response.status_code} - {response.text}")

        return response.json()

    async def _graph_download(self, endpoint: str) -> bytes:
        """Download file content from Graph API."""
        token = await self._get_token()
        url = f"{GRAPH_BASE}{endpoint}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
            )

        if response.status_code == 404:
            raise SourceNotFoundError(f"File not found: {endpoint}")
        if response.status_code != 200:
            raise FetchError(endpoint, f"Graph API error: {response.status_code}")

        return response.content

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch from SharePoint based on URI type."""
        route = parse_sharepoint_uri(uri)

        if route.type == "site":
            return await self._fetch_site(route, **kwargs)
        elif route.type == "library":
            return await self._fetch_library(route, **kwargs)
        elif route.type == "file":
            return await self._fetch_file(route, uri, **kwargs)

        raise SourceNotFoundError(f"Unknown SharePoint route type: {route.type}")

    async def _fetch_site(self, route: SharePointRoute, **kwargs: Any) -> SharePointSite:
        """Fetch a SharePoint site and its libraries."""
        include_items = kwargs.get("include_items", False)

        if route.site_name:
            site_data = await self._get_site_by_name(route.site_name, route.hostname)
        else:
            raise SourceNotFoundError("Site name not specified")

        site_id = site_data.get("id", "")
        site_name = site_data.get("name", route.site_name)
        display_name = site_data.get("displayName", site_name)
        description = site_data.get("description")
        web_url = site_data.get("webUrl", "")

        site_description_doc = None
        if description:
            site_description_doc = TextDocument(
                source_uri=route.site_name,
                content=description,
                format=TextFormat.PLAIN,
            )

        items = []
        if include_items:
            libraries = await self._get_libraries(site_id)
            for lib in libraries:
                items.append(lib)

        return SharePointSite(
            site_id=site_id,
            name=site_name,
            display_name=display_name,
            description=site_description_doc,
            hostname=route.hostname,
            items=items,
            url=web_url,
        )

    async def _get_site_by_name(self, site_name: str, hostname: str) -> dict:
        """Get site by name (URL path)."""
        if hostname:
            filter_query = f"siteCollection/hostname eq '{hostname}' and name eq '{site_name}'"
        else:
            filter_query = f"name eq '{site_name}'"

        result = await self._graph(
            "/sites",
            params={
                "$filter": filter_query,
                "$top": 1,
            },
        )

        sites = result.get("value", [])
        if not sites:
            raise SourceNotFoundError(f"Site not found: {site_name}")

        return sites[0]

    async def _get_libraries(self, site_id: str) -> list[SharePointLibrary]:
        """Get all document libraries for a site."""
        result = await self._graph(f"/sites/{site_id}/drives")

        libraries = []
        for drive in result.get("value", []):
            lib = SharePointLibrary(
                library_id=drive.get("id", ""),
                name=drive.get("name", ""),
                site=drive.get("parentReference", {}).get("driveType", ""),
                description=drive.get("description"),
                item_count=drive.get("quota", {}).get("used", 0),
                url=drive.get("webUrl", ""),
            )
            libraries.append(lib)

        return libraries

    async def _fetch_library(self, route: SharePointRoute, **kwargs: Any) -> SharePointLibrary:
        """Fetch a SharePoint library and its files."""
        include_items = kwargs.get("include_items", True)
        max_items = kwargs.get("max_items", 100)

        site_data = await self._get_site_by_name(route.site_name, route.hostname)
        site_id = site_data.get("id", "")

        drives_result = await self._graph(f"/sites/{site_id}/drives")

        drive_id = None
        drive_name = route.library_name or "Documents"
        for drive in drives_result.get("value", []):
            if drive.get("name", "").lower() == drive_name.lower():
                drive_id = drive.get("id")
                break

        if not drive_id:
            for drive in drives_result.get("value", []):
                if drive.get("name", "").lower() in [drive_name.lower(), "documents"]:
                    drive_id = drive.get("id")
                    break

        if not drive_id:
            raise SourceNotFoundError(f"Library not found: {drive_name}")

        drive_data = drives_result["value"][0]
        for d in drives_result.get("value", []):
            if d.get("id") == drive_id:
                drive_data = d
                break

        items = []
        if include_items:
            items = await self._get_library_items(drive_id, max_items)

        return SharePointLibrary(
            library_id=drive_id,
            name=drive_data.get("name", drive_name),
            site=route.site_name,
            description=drive_data.get("description"),
            items=items,
            item_count=len(items),
            url=drive_data.get("webUrl", ""),
        )

    async def _get_library_items(self, drive_id: str, max_items: int) -> list[SharePointFile]:
        """Get files in a library."""
        result = await self._graph(
            f"/drives/{drive_id}/root/children",
            params={"$top": max_items},
        )

        files = []
        for item in result.get("value", []):
            if "file" in item:
                file_info = await self._build_sharepoint_file(item, drive_id)
                files.append(file_info)

        return files

    async def _fetch_file(self, route: SharePointRoute, uri: str, **kwargs: Any) -> SharePointFile:
        """Fetch a SharePoint file."""
        include_content = kwargs.get("include_content", False)

        site_data = await self._get_site_by_name(route.site_name, route.hostname)
        site_id = site_data.get("id", "")

        drives_result = await self._graph(f"/sites/{site_id}/drives")

        drive_id = None
        library_name = route.library_name or "Documents"
        for drive in drives_result.get("value", []):
            if drive.get("name", "").lower() == library_name.lower():
                drive_id = drive.get("id")
                break

        if not drive_id:
            for drive in drives_result.get("value", []):
                if drive.get("name", "").lower() in [library_name.lower(), "documents"]:
                    drive_id = drive.get("id")
                    break

        if not drive_id:
            raise SourceNotFoundError(f"Library not found: {library_name}")

        file_path = route.file_name
        if not file_path:
            raise SourceNotFoundError("File path not specified")

        item_result = await self._graph(f"/drives/{drive_id}/root:/{file_path}")

        return await self._build_sharepoint_file(item_result, drive_id, include_content)

    async def _build_sharepoint_file(
        self, item: dict, drive_id: str, include_content: bool = False
    ) -> SharePointFile:
        """Build a SharePointFile from Graph API item."""
        file_id = item.get("id", "")
        name = item.get("name", "")
        path = item.get("parentReference", {}).get("path", "")
        if path:
            path = path.split(":")[-1] if ":" in path else path
            if name:
                path = f"{path}/{name}"

        file_props = item.get("file", {})
        mime_type = file_props.get("mimeType", "application/octet-stream")

        size_bytes = item.get("size")
        created_at = datetime.fromisoformat(item.get("createdDateTime", "").replace("Z", "+00:00"))
        modified_at = datetime.fromisoformat(
            item.get("lastModifiedDateTime", "").replace("Z", "+00:00")
        )
        web_url = item.get("webUrl", "")

        created_by = item.get("createdBy", {}).get("user", {})
        author = created_by.get("displayName") or created_by.get("email")

        last_modified_by = item.get("lastModifiedBy", {}).get("user", {})
        last_modifier = last_modified_by.get("displayName") or last_modified_by.get("email")

        content = None
        if include_content and item.get("file"):
            try:
                file_content = await self._graph_download(
                    f"/drives/{drive_id}/items/{file_id}/content"
                )
                if mime_type.startswith("text/") or mime_type in [
                    "application/json",
                    "application/xml",
                ]:
                    try:
                        content = TextDocument(
                            source_uri=web_url,
                            content=file_content.decode("utf-8"),
                            format=TextFormat.PLAIN,
                        )
                    except UnicodeDecodeError:
                        content = file_content  # type: ignore[assignment]
                else:
                    content = file_content  # type: ignore[assignment]
            except Exception:
                pass

        parent_ref = item.get("parentReference", {})
        site_name = ""
        if "driveId" in parent_ref:
            drive_id = parent_ref.get("driveId", "")
        site_name = parent_ref.get("name", "")

        library_name = ""
        if path:
            parts = path.strip("/").split("/")
            if len(parts) >= 2:
                library_name = parts[0]

        return SharePointFile(
            file_id=file_id,
            name=name,
            path=path or name,
            site=site_name,
            library=library_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            content=content,
            author=author,
            last_modified_by=last_modifier,
            created_at=created_at,
            modified_at=modified_at,
            url=web_url,
        )
