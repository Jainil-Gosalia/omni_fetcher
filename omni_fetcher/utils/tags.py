"""Tag utilities for OmniFetcher."""

from __future__ import annotations

from typing import Any


def merge_tags(*tag_sources: Any) -> list[str]:
    """Merge multiple tag sources and deduplicate.

    Args:
        *tag_sources: Variables to merge tags from. Can be:
            - list[str]: Direct tag list
            - object with tags attribute
            - None or empty

    Returns:
        Sorted, deduplicated list of tags
    """
    all_tags: set[str] = set()

    for source in tag_sources:
        if source is None:
            continue
        if isinstance(source, list):
            all_tags.update(source)
        elif hasattr(source, "tags") and source.tags:
            all_tags.update(source.tags)

    return sorted(all_tags)


def build_file_tags(
    base_tags: list[str],
    file_size: int,
    threshold: int = 50 * 1024 * 1024,
) -> list[str]:
    """Build tags with large_file check.

    Args:
        base_tags: Base tags to start with
        file_size: File size in bytes
        threshold: Size threshold for large_file tag (default: 50MB)

    Returns:
        List of tags including large_file if threshold exceeded
    """
    tags = list(base_tags)
    if file_size > threshold:
        tags.append("large_file")
    return tags


def apply_content_tags(
    tags: list[str],
    has_transcript: bool = False,
    has_images: bool = False,
    has_tables: bool = False,
    is_scanned: bool = False,
) -> list[str]:
    """Apply content-derived tags.

    Args:
        tags: Base tags list
        has_transcript: Whether transcript is present
        has_images: Whether images are present
        has_tables: Whether tables are present
        is_scanned: Whether document is scanned (no text layer)

    Returns:
        Updated tags list
    """
    result = list(tags)
    if has_transcript:
        result.append("has_transcript")
    if has_images:
        result.append("has_images")
    if has_tables:
        result.append("has_tables")
    if is_scanned:
        result.append("scanned")
    return result
