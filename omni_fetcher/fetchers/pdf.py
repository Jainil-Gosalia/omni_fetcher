"""PDF fetcher for OmniFetcher."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Optional

import httpx
from pypdf import PdfReader

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata
from omni_fetcher.schemas.documents import PDFDocument
from omni_fetcher.schemas.atomics import (
    TextDocument,
    TextFormat,
    ImageDocument,
)


@source(
    name="pdf",
    uri_patterns=["*.pdf"],
    mime_types=["application/pdf"],
    priority=20,
    description="Fetch and parse PDF documents",
)
class PDFFetcher(BaseFetcher):
    """Fetcher for PDF documents."""

    name = "pdf"
    priority = 20

    def __init__(self, timeout: float = 60.0):
        super().__init__()
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this is a PDF URL."""
        return uri.lower().endswith(".pdf")

    async def fetch(self, uri: str, **kwargs: Any) -> PDFDocument:
        """Fetch and parse a PDF document."""
        # Determine if local or remote
        if uri.startswith("file://"):
            pdf_data = await self._fetch_local(uri)
        else:
            pdf_data = await self._fetch_remote(uri)

        metadata = FetchMetadata(
            source_uri=uri,
            fetched_at=datetime.now(),
            source_name=self.name,
            mime_type="application/pdf",
            file_size=len(pdf_data),
        )

        # Parse PDF
        pdf_info = await self._parse_pdf(pdf_data)

        return PDFDocument(
            metadata=metadata,
            title=pdf_info.get("title"),
            text=TextDocument(
                source_uri=uri,
                content=pdf_info.get("text", ""),
                format=TextFormat.PLAIN,
            ),
            images=[],
            author=pdf_info.get("author"),
            page_count=pdf_info.get("page_count"),
            subject=pdf_info.get("subject"),
            creator=pdf_info.get("creator"),
            producer=pdf_info.get("producer"),
            creation_date=pdf_info.get("creation_date"),
            modification_date=pdf_info.get("modification_date"),
            language=pdf_info.get("language"),
        )

    async def _fetch_local(self, uri: str) -> bytes:
        """Fetch local PDF file."""
        from pathlib import Path

        path = uri.replace("file://", "")
        if path.startswith("/"):
            path = path[1:]

        return Path(path).read_bytes()

    async def _fetch_remote(self, uri: str) -> bytes:
        """Fetch remote PDF file."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(uri)
            response.raise_for_status()
            return response.content

    async def _parse_pdf(self, pdf_data: bytes) -> dict[str, Any]:
        """Parse PDF and extract information."""

        def _parse():
            reader = PdfReader(io.BytesIO(pdf_data))

            info = {
                "text": "",
                "page_count": len(reader.pages),
            }

            # Extract text from all pages
            for page in reader.pages:
                info["text"] += page.extract_text() + "\n"

            # Extract metadata
            if reader.metadata:
                info["title"] = reader.metadata.get("/Title", "")
                info["author"] = reader.metadata.get("/Author", "")
                info["subject"] = reader.metadata.get("/Subject", "")
                info["creator"] = reader.metadata.get("/Creator", "")
                info["producer"] = reader.metadata.get("/Producer", "")
                info["language"] = reader.metadata.get("/Lang", "")

                # Handle dates
                creation_date = reader.metadata.get("/CreationDate", "")
                if creation_date:
                    info["creation_date"] = self._parse_pdf_date(creation_date)

                mod_date = reader.metadata.get("/ModDate", "")
                if mod_date:
                    info["modification_date"] = self._parse_pdf_date(mod_date)

            return info

        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _parse)

    def _parse_pdf_date(self, date_str: str) -> Optional[datetime]:
        """Parse PDF date string."""
        # PDF dates are typically: D:YYYYMMDDHHmmSSOHH'mm
        if date_str.startswith("D:"):
            date_str = date_str[2:]

        try:
            # Simple parsing for YYYYMMDD format
            if len(date_str) >= 8:
                return datetime(
                    int(date_str[0:4]),
                    int(date_str[4:6]),
                    int(date_str[6:8]),
                )
        except Exception:
            pass

        return None
