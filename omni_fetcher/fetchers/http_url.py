"""HTTP URL fetcher for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata
from omni_fetcher.schemas.documents import WebPageDocument
from omni_fetcher.schemas.atomics import TextDocument, ImageDocument, TextFormat

try:
    import trafilatura
except ImportError:
    trafilatura = None


@source(
    name="http_url",
    uri_patterns=["http://*", "https://*"],
    mime_types=["text/*", "application/octet-stream"],
    priority=50,
    description="Fetch content from HTTP/HTTPS URLs",
)
class HTTPURLFetcher(BaseFetcher):
    """Fetcher for HTTP/HTTPS URLs."""

    name = "http_url"
    priority = 50

    def __init__(self, timeout: float = 30.0):
        super().__init__()
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this is an HTTP/HTTPS URI."""
        return uri.startswith("http://") or uri.startswith("https://")

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch content from HTTP URL."""
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(uri)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "application/octet-stream")
            mime_type = content_type.split(";")[0].strip()
            fetch_duration_ms = response.elapsed.total_seconds() * 1000

            metadata = FetchMetadata(
                source_uri=str(response.url),
                fetched_at=datetime.now(),
                source_name=self.name,
                mime_type=mime_type,
                file_size=len(response.content),
                fetch_duration_ms=fetch_duration_ms,
                headers=dict(response.headers),
                status_code=response.status_code,
                etag=response.headers.get("etag"),
                last_modified=self._parse_date(response.headers.get("last-modified")),
            )

            return await self._create_result(response, metadata, mime_type)

    async def _create_result(
        self, response: httpx.Response, metadata: FetchMetadata, mime_type: str
    ) -> Any:
        """Create appropriate result model based on content type."""
        if mime_type.startswith("text/"):
            content = response.text

            if mime_type == "text/html":
                return self._create_webpage_document(response, metadata, content)
            elif mime_type == "text/markdown":
                return TextDocument(
                    source_uri=metadata.source_uri,
                    content=content,
                    format=TextFormat.MARKDOWN,
                    tags=["web", "text"],
                )
            else:
                return TextDocument(
                    source_uri=metadata.source_uri,
                    content=content,
                    format=TextFormat.PLAIN,
                    tags=["web", "text"],
                )

        elif mime_type.startswith("image/"):
            return ImageDocument(
                source_uri=metadata.source_uri,
                width=None,
                height=None,
                format=mime_type.split("/")[-1].upper(),
                tags=["web", "image"],
            )

        elif mime_type == "application/json":
            from omni_fetcher.schemas.structured import JSONData

            try:
                data = response.json()
            except Exception:
                data = {}

            return JSONData(
                metadata=metadata,
                data=data,
                root_keys=list(data.keys()) if isinstance(data, dict) else None,
                is_array=isinstance(data, list),
                tags=["web", "json", "api"],
            )

        else:
            return TextDocument(
                source_uri=metadata.source_uri,
                content=response.text[:10000],
                format=TextFormat.PLAIN,
                tags=["web", "text"],
            )

    def _create_webpage_document(
        self, response: httpx.Response, metadata: FetchMetadata, html: str
    ) -> WebPageDocument:
        """Create WebPageDocument from HTML content."""
        url = str(response.url)
        title = self._extract_title(html)
        author = self._extract_author(html)
        published_at = self._extract_published_date(html)
        language = self._extract_language(html)
        images = self._extract_images(html, url)

        text_content, text_format = self._extract_text(html)

        body = TextDocument(
            source_uri=url,
            content=text_content,
            format=text_format,
        )

        tags = ["web", "webpage"]
        if images:
            tags.append("has_images")

        return WebPageDocument(
            url=url,
            title=title,
            body=body,
            images=images,
            author=author,
            published_at=published_at,
            language=language,
            status_code=response.status_code,
            tags=tags,
        )

    def _extract_text(self, html: str) -> tuple[str, TextFormat]:
        """Extract clean text from HTML using trafilatura or fallback."""
        if trafilatura is not None:
            try:
                result = trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=True,
                    output_format="markdown",
                )
                if result:
                    return result, TextFormat.MARKDOWN
            except Exception:
                pass

        return self._fallback_extract_text(html)

    def _fallback_extract_text(self, html: str) -> tuple[str, TextFormat]:
        """Fallback text extraction using BeautifulSoup."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            for script in soup(["script", "style"]):
                script.decompose()

            text = soup.get_text(separator="\n")

            lines = [line.strip() for line in text.split("\n")]
            clean_lines = [line for line in lines if line]
            clean_text = "\n".join(clean_lines)

            return clean_text, TextFormat.PLAIN
        except Exception:
            return "", TextFormat.PLAIN

    def _extract_title(self, html: str) -> Optional[str]:
        """Extract title from HTML."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                return og_title["content"].strip()

            title_tag = soup.find("title")
            if title_tag:
                return title_tag.get_text().strip()

            h1 = soup.find("h1")
            if h1:
                return h1.get_text().strip()
        except Exception:
            pass
        return None

    def _extract_author(self, html: str) -> Optional[str]:
        """Extract author from HTML meta tags."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            author_meta = soup.find("meta", attrs={"name": "author"})
            if author_meta and author_meta.get("content"):
                return author_meta["content"].strip()

            og_author = soup.find("meta", property="article:author")
            if og_author and og_author.get("content"):
                return og_author["content"].strip()

            author_tag = soup.find("meta", attrs={"name": "twitter:creator"})
            if author_tag and author_tag.get("content"):
                return author_tag["content"].strip()
        except Exception:
            pass
        return None

    def _extract_published_date(self, html: str) -> Optional[datetime]:
        """Extract published date from schema.org or meta tags."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            schema = soup.find("script", type="application/ld+json")
            if schema:
                import json

                try:
                    data = json.loads(schema.string)
                    if isinstance(data, dict):
                        if data.get("datePublished"):
                            return self._parse_iso_date(data["datePublished"])
                        if data.get("dateCreated"):
                            return self._parse_iso_date(data["dateCreated"])
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get("datePublished"):
                                return self._parse_iso_date(item["datePublished"])
                except Exception:
                    pass

            article_published = soup.find("meta", property="article:published_time")
            if article_published and article_published.get("content"):
                return self._parse_iso_date(article_published["content"])
        except Exception:
            pass
        return None

    def _parse_iso_date(self, date_str: str) -> Optional[datetime]:
        """Parse ISO date string to datetime."""
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None

    def _extract_language(self, html: str) -> Optional[str]:
        """Extract language from HTML."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            html_tag = soup.find("html")
            if html_tag and html_tag.get("lang"):
                return html_tag["lang"].strip()

            meta = soup.find("meta", attrs={"http-equiv": "content-language"})
            if meta and meta.get("content"):
                return meta["content"].strip()
        except Exception:
            pass
        return None

    def _extract_images(self, html: str, base_url: str) -> list[ImageDocument]:
        """Extract images from main content area."""
        images = []
        try:
            soup = BeautifulSoup(html, "html.parser")

            main_content = soup.find("main") or soup.find("article") or soup.find("body")

            if main_content:
                img_tags = main_content.find_all("img")

                for img in img_tags:
                    src = img.get("src") or img.get("data-src")
                    if not src:
                        continue

                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        from urllib.parse import urljoin

                        src = urljoin(base_url, src)

                    if src.startswith("http://") or src.startswith("https://"):
                        images.append(
                            ImageDocument(
                                source_uri=src,
                                width=None,
                                height=None,
                                format="UNKNOWN",
                            )
                        )

        except Exception:
            pass
        return images

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse HTTP date string to datetime."""
        if not date_str:
            return None
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            return None
