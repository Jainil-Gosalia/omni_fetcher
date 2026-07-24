"""External-behaviour tests for the v1 ``markdown`` connector (real files).

A single note is read from a real temp file: frontmatter is split, wikilinks and
tags are extracted, and the body becomes a ``Text`` atom (Markdown for ``.md``,
plain for ``.org``). A missing path is ``NOT_FOUND`` and a bad URI is
``INVALID_INPUT``.
"""

from __future__ import annotations

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors.markdown import MarkdownConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

_NOTE = """---
title: My Note
tags: [alpha, beta]
---
# Heading

Body with a [[Linked Note]] and [[Other|an alias]] and a #gamma tag.
"""


def _write(tmp_path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path).replace("\\", "/")


async def test_markdown_note_is_success(tmp_path):
    path = _write(tmp_path, "note.md", _NOTE)

    result = await MarkdownConnector().fetch(f"markdown://{path}")

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "note"

    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].format == TextFormat.MARKDOWN
    assert atoms[0].content.startswith("# Heading")
    assert "[[Linked Note]]" in atoms[0].content  # frontmatter stripped, body intact

    extra = node.metadata.source_extra["markdown"]
    assert extra["title"] == "My Note"
    assert extra["wikilinks"] == ["Linked Note", "Other"]
    assert extra["tags"] == ["alpha", "beta", "gamma"]
    assert extra["frontmatter"]["title"] == "My Note"


async def test_org_note_is_plain(tmp_path):
    path = _write(tmp_path, "note.org", "* Heading\nsome [[Link]] text\n")

    result = await MarkdownConnector().fetch(f"markdown://{path}")

    assert isinstance(result, Success)
    atoms = list(result.tree.iter_atoms())
    assert atoms[0].format == TextFormat.PLAIN
    assert result.tree.metadata.source_extra["markdown"]["wikilinks"] == ["Link"]


async def test_note_without_frontmatter(tmp_path):
    path = _write(tmp_path, "plain.md", "Just body, no frontmatter, #tag here.\n")

    result = await MarkdownConnector().fetch(f"markdown://{path}")

    assert isinstance(result, Success)
    extra = result.tree.metadata.source_extra["markdown"]
    assert extra["title"] == "plain"  # falls back to the file stem
    assert extra["tags"] == ["tag"]
    assert extra["frontmatter"] == {}


async def test_missing_file_is_not_found(tmp_path):
    missing = str(tmp_path / "nope.md").replace("\\", "/")

    result = await MarkdownConnector().fetch(f"markdown://{missing}")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_empty_path_is_invalid_input():
    result = await MarkdownConnector().fetch("markdown://")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


def test_can_handle():
    assert MarkdownConnector.can_handle("markdown:///note.md")
    assert not MarkdownConnector.can_handle("obsidian:///note.md")
