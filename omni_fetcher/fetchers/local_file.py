"""Local file fetcher for OmniFetcher."""

from __future__ import annotations

import os
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata, MediaType, DataCategory
from omni_fetcher.schemas.atomics import (
    VideoDocument,
    ImageDocument,
    TextDocument,
    TextFormat,
)
from omni_fetcher.schemas.documents import PDFDocument
from omni_fetcher.schemas.structured import JSONData, YAMLData


@source(
    name="local_file",
    uri_patterns=["file:///*", "file:///.*", "/.*", "^[A-Za-z]:\\.*"],
    mime_types=["*/*"],
    priority=10,
    description="Fetch files from local filesystem",
)
class LocalFileFetcher(BaseFetcher):
    """Fetcher for local files."""

    name = "local_file"
    priority = 10

    def __init__(self):
        super().__init__()
        mimetypes.init()

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this is a local file URI."""
        return uri.startswith("file://") or os.path.isabs(uri)

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch a local file.

        Args:
            uri: File path (absolute or file:// URI)

        Returns:
            Appropriate Pydantic model based on file type
        """
        # Convert file:// URI to path
        if uri.startswith("file://"):
            # Handle both Unix and Windows file:// URIs
            path = uri[7:]  # Remove file://
            if path.startswith("/") and not os.name == "nt":
                pass  # Unix absolute path
            elif os.name == "nt" and len(path) > 1 and path[1] == ":":
                pass  # Windows absolute path (e.g., C:)
            else:
                # Could be file://relative or file://localhost/path
                path = uri.replace("file://localhost/", "").replace("file:///", "")
        else:
            path = uri

        # Get absolute path
        path_obj = Path(path).resolve()

        if not path_obj.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not path_obj.is_file():
            raise ValueError(f"Not a file: {path}")

        # Get file stats
        stat = path_obj.stat()
        file_size = stat.st_size
        mime_type = self._guess_mime_type(path)

        # Create metadata
        metadata = FetchMetadata(
            source_uri=uri,
            fetched_at=datetime.now(),
            source_name=self.name,
            mime_type=mime_type,
            file_size=file_size,
            last_modified=datetime.fromtimestamp(stat.st_mtime),
        )

        # Return appropriate model based on file type
        return await self._create_result(path_obj, metadata, mime_type, uri, stat)

    def _guess_mime_type(self, path: str) -> Optional[str]:
        """Guess MIME type from file extension."""
        mime_type, _ = mimetypes.guess_type(path)
        return mime_type

    async def _create_result(
        self, path: Path, metadata: FetchMetadata, mime_type: Optional[str], uri: str, stat: Any
    ) -> Any:
        """Create appropriate result model based on file type."""
        content = None
        file_size = stat.st_size

        def build_tags(base_tags: list[str]) -> list[str]:
            tags = list(base_tags)
            if file_size > 50 * 1024 * 1024:
                tags.append("large_file")
            return tags

        if mime_type:
            # Text files
            if mime_type.startswith("text/"):
                try:
                    content = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    content = path.read_text(encoding="latin-1")

                if mime_type == "text/markdown":
                    tags = build_tags(["local", "file", "text"])
                    return TextDocument(
                        source_uri=metadata.source_uri,
                        content=content,
                        format=TextFormat.MARKDOWN,
                        encoding="utf-8",
                        tags=tags,
                    )
                elif mime_type == "text/html":
                    from omni_fetcher.schemas.documents import WebPageDocument

                    tags = build_tags(["local", "file", "text"])
                    return WebPageDocument(
                        metadata=metadata,
                        text=TextDocument(
                            source_uri=metadata.source_uri,
                            content=content,
                            format=TextFormat.HTML,
                            tags=tags,
                        ),
                        title=path.stem,
                    )
                else:
                    tags = build_tags(["local", "file", "text"])
                    return TextDocument(
                        source_uri=metadata.source_uri,
                        content=content,
                        format=TextFormat.PLAIN,
                        encoding="utf-8",
                        tags=tags,
                    )

            # JSON
            elif mime_type == "application/json":
                import json

                try:
                    data = json.loads(path.read_text())
                except json.JSONDecodeError:
                    data = {}

                from omni_fetcher.schemas.structured import JSONData

                tags = build_tags(["local", "file", "json"])
                return JSONData(
                    metadata=metadata,
                    data=data,
                    root_keys=list(data.keys()) if isinstance(data, dict) else None,
                    is_array=isinstance(data, list),
                    tags=tags,
                )

            # YAML
            elif mime_type in ["application/x-yaml", "text/yaml"]:
                try:
                    import yaml

                    data = yaml.safe_load(path.read_text())
                except ImportError:
                    data = {}
                except Exception:
                    data = {}

                from omni_fetcher.schemas.structured import YAMLData

                tags = build_tags(["local", "file", "yaml"])
                return YAMLData(
                    metadata=metadata,
                    data=data,
                    root_keys=list(data.keys()) if isinstance(data, dict) else None,
                    tags=tags,
                )

            # PDF
            elif mime_type == "application/pdf":
                from omni_fetcher.schemas.documents import PDFDocument

                tags = build_tags(["local", "file", "pdf", "document"])
                return PDFDocument(
                    metadata=metadata,
                    text=TextDocument(
                        source_uri=metadata.source_uri,
                        content="",
                        format=TextFormat.PLAIN,
                        tags=tags,
                    ),
                )

            # Video
            elif mime_type.startswith("video/"):
                video_format = mime_type.split("/")[-1] if mime_type else "unknown"
                tags = build_tags(["local", "file", "video"])
                return VideoDocument(
                    source_uri=uri,
                    duration_seconds=0.0,
                    format=video_format,
                    file_name=path.name,
                    file_size_bytes=stat.st_size,
                    tags=tags,
                )

            # Image
            elif mime_type.startswith("image/"):
                image_format = mime_type.split("/")[-1] if mime_type else "unknown"
                tags = build_tags(["local", "file", "image"])
                return ImageDocument(
                    source_uri=uri,
                    format=image_format,
                    file_name=path.name,
                    file_size_bytes=stat.st_size,
                    tags=tags,
                )

        # Default to text document
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="latin-1")

        tags = build_tags(["local", "file", "text"])
        return TextDocument(
            source_uri=metadata.source_uri,
            content=content,
            format=TextFormat.PLAIN,
            encoding="utf-8",
            tags=tags,
        )
