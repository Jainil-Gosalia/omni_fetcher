"""Shared spec for the knowledge-base & wiki connector family (v1.14).

The "read a note, keep its Markdown body and surface its graph" shape is the
same across personal-knowledge tools (Obsidian, Logseq, plain Markdown/Org) and
wikis (MediaWiki); what differs per source is where notes live (a local vault
folder, a wiki API) and the surface syntax (Markdown vs. Org vs. wikitext/HTML).
This module holds the genuinely shared part -- and only that:

- :func:`parse_frontmatter` -- split leading YAML frontmatter (``---`` fences)
  from the body.
- :func:`extract_wikilinks` -- pull ``[[target]]`` / ``[[target|alias]]`` /
  ``[[target#heading]]`` references (the knowledge graph's edges).
- :func:`extract_hashtags` / :func:`merge_tags` -- gather ``#tag`` body tags and
  frontmatter ``tags:`` into one ordered, de-duplicated list.
- :func:`build_note_node` -- fold a note into one ``kind="note"`` node carrying a
  single ``Text`` atom (the body, at the given ``TextFormat``), with title,
  frontmatter, wikilinks, and tags in ``source_extra[namespace]``.
- :func:`build_vault_collection` -- fold a set of note nodes into one
  ``kind="collection"`` node (a vault / graph / space).

Each connector owns its addressing, its file walk or API call, and its
surface->Markdown conversion. This is a spec, not a base class doing the work.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import yaml

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Result, gap, partial, success

# Advisory semantic ``kind`` values for the nodes this family emits.
NOTE_KIND = "note"
COLLECTION_KIND = "collection"

# ``[[target]]`` / ``[[target|alias]]`` / ``[[target#heading]]``: capture the
# target only (before any ``|`` alias or ``#`` heading anchor).
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")

# ``#tag`` with Obsidian/Logseq tag characters (letters, digits, _, -, /), not a
# bare number (``#123`` is not a tag) and not mid-word (must follow start/space).
_HASHTAG = re.compile(r"(?:^|\s)#([A-Za-z_][\w/-]*)")

# A fenced YAML frontmatter block at the very top of a note.
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """
    Split leading YAML frontmatter from the note body

    A ``---``-fenced YAML block at the very top is parsed into a mapping and
    removed from the returned body. Malformed or non-mapping frontmatter is
    treated as absent (the fence stays part of the body) rather than raising --
    a note is never rejected for a bad header.

    Parameters
    ----------
        text:
            The raw note text.

    Return
    ------
        frontmatter, body:
            The parsed frontmatter mapping (empty when absent) and the body
            with the frontmatter block stripped.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        loaded = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, text
    if not isinstance(loaded, dict):
        return {}, text
    body = text[match.end() :]
    return loaded, body


def extract_wikilinks(text: str) -> list[str]:
    """
    Extract ``[[wikilink]]`` targets from a note body, in order, de-duplicated

    Parameters
    ----------
        text:
            The note body.

    Return
    ------
        targets:
            The link targets (before any ``|`` alias or ``#`` heading), each
            once, in first-seen order.
    """
    seen: dict[str, None] = {}
    for match in _WIKILINK.finditer(text):
        target = match.group(1).strip()
        if target:
            seen.setdefault(target, None)
    return list(seen)


def extract_hashtags(text: str) -> list[str]:
    """
    Extract ``#tag`` body tags from a note, in order, de-duplicated

    Parameters
    ----------
        text:
            The note body.

    Return
    ------
        tags:
            The hashtag names (without the ``#``), each once, in first-seen
            order.
    """
    seen: dict[str, None] = {}
    for match in _HASHTAG.finditer(text):
        seen.setdefault(match.group(1), None)
    return list(seen)


def _frontmatter_tags(frontmatter: Mapping[str, Any]) -> list[str]:
    """Pull tags from a frontmatter ``tags:`` value (a list or a CSV string)."""
    raw = frontmatter.get("tags")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [part.strip() for part in raw.replace(",", " ").split() if part.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def merge_tags(frontmatter: Mapping[str, Any], body: str) -> list[str]:
    """
    Merge frontmatter ``tags:`` and body ``#tag``s into one ordered set

    Frontmatter tags come first (they are the note's declared taxonomy), then
    body hashtags, each tag once.

    Parameters
    ----------
        frontmatter:
            The parsed frontmatter mapping.
        body:
            The note body (searched for ``#tag``).

    Return
    ------
        tags:
            The merged, de-duplicated tag list.
    """
    seen: dict[str, None] = {}
    for tag in _frontmatter_tags(frontmatter):
        seen.setdefault(tag, None)
    for tag in extract_hashtags(body):
        seen.setdefault(tag, None)
    return list(seen)


def build_note_node(
    *,
    uri: str,
    namespace: str,
    title: str,
    body: str,
    text_format: TextFormat = TextFormat.MARKDOWN,
    frontmatter: Optional[Mapping[str, Any]] = None,
    wikilinks: Optional[Sequence[str]] = None,
    tags: Optional[Sequence[str]] = None,
    extra_fields: Optional[Mapping[str, Any]] = None,
    updated: Optional[datetime] = None,
) -> CompositionNode:
    """
    Fold one note into a canonical ``kind="note"`` node

    The body becomes one ``Text`` atom at ``text_format``; the title,
    frontmatter, wikilinks, and tags are descriptive facts under
    ``source_extra[namespace]`` -- never inline on the atom.

    Parameters
    ----------
        uri:
            The note's source URI (``source_url``).
        namespace:
            The ``source_extra`` namespace (``"obsidian"`` / ``"logseq"`` / ...).
        title:
            The note title (basename or wiki page title).
        body:
            The note body (frontmatter already stripped).
        text_format:
            The body's canonical ``TextFormat`` (Markdown by default).
        frontmatter:
            The parsed frontmatter mapping, if any.
        wikilinks:
            The outbound ``[[link]]`` targets.
        tags:
            The note's tags.
        extra_fields:
            Extra descriptive fields merged into ``source_extra[namespace]``.
        updated:
            The note's modification time, if known.

    Return
    ------
        node:
            The canonical note node (un-stamped; the connector stamps it).
    """
    fields: dict[str, Any] = {
        "title": title,
        "frontmatter": dict(frontmatter) if frontmatter else {},
        "wikilinks": list(wikilinks or []),
        "tags": list(tags or []),
    }
    if extra_fields:
        fields.update(extra_fields)
    atom = Text(content=body, format=text_format, encoding="utf-8")
    return build_node(
        kind=NOTE_KIND,
        atoms=[atom],
        source_url=uri,
        updated=updated,
        source_namespace=namespace,
        source_fields=fields,
    )


def note_node_from_file(
    path: Path,
    *,
    uri: str,
    namespace: str,
    text_format: TextFormat = TextFormat.MARKDOWN,
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> CompositionNode:
    """
    Read a local note file and fold it into a canonical note node

    Reads ``path`` as UTF-8, splits frontmatter, extracts wikilinks and tags,
    and builds the ``kind="note"`` node. The title is the frontmatter ``title``
    if present, else the file stem. The file's mtime becomes ``updated``.
    Raises ``OSError`` / ``UnicodeDecodeError`` to the caller (mapped to a typed
    error at the connector boundary).

    Parameters
    ----------
        path:
            The note file path.
        uri:
            The note's source URI (``source_url``).
        namespace:
            The ``source_extra`` namespace.
        text_format:
            The body's canonical ``TextFormat`` (Markdown by default).
        extra_fields:
            Extra descriptive fields for ``source_extra[namespace]``.

    Return
    ------
        node:
            The canonical note node.
    """
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(raw)
    title = str(frontmatter.get("title") or path.stem)
    updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    fields = {"path": str(path)}
    if extra_fields:
        fields.update(dict(extra_fields))
    return build_note_node(
        uri=uri,
        namespace=namespace,
        title=title,
        body=body,
        text_format=text_format,
        frontmatter=frontmatter,
        wikilinks=extract_wikilinks(body),
        tags=merge_tags(frontmatter, body),
        extra_fields=fields,
        updated=updated,
    )


def build_vault_collection(
    *,
    uri: str,
    namespace: str,
    notes: Iterable[CompositionNode],
    extra_fields: Optional[Mapping[str, Any]] = None,
    truncated: bool = False,
    note_cap: Optional[int] = None,
) -> Result:
    """
    Fold note nodes into one ``kind="collection"`` vault/graph result

    Builds a container node whose children are the note nodes, with the note
    count in ``source_extra[namespace]``. When ``truncated`` (more notes existed
    than the cap allowed), the result is a ``Partial`` carrying an honest
    ``UNSUPPORTED`` gap naming the cap, never a silent drop.

    Parameters
    ----------
        uri:
            The vault/graph source URI.
        namespace:
            The ``source_extra`` namespace.
        notes:
            The note child nodes, already built.
        extra_fields:
            Extra descriptive fields for ``source_extra[namespace]``.
        truncated:
            Whether the note set was capped.
        note_cap:
            The applied cap (named in the truncation gap).

    Return
    ------
        result:
            A ``Success`` (within cap) or ``Partial`` (truncated) collection.
    """
    children = list(notes)
    fields: dict[str, Any] = {"note_count": len(children)}
    if extra_fields:
        fields.update(extra_fields)
    node = build_node(
        kind=COLLECTION_KIND,
        children=children,
        source_url=uri,
        source_namespace=namespace,
        source_fields=fields,
    )
    if truncated:
        return partial(
            node,
            [
                gap(
                    kind=ErrorKind.UNSUPPORTED,
                    locator=uri,
                    detail=(
                        f"vault truncated to the {note_cap}-note cap; more notes exist. "
                        "Fetch a single note by path, or raise the cap"
                    ),
                )
            ],
        )
    return success(node)
