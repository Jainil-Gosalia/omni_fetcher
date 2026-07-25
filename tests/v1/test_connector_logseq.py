"""External-behaviour tests for the v1 ``logseq`` connector (real files).

A graph walk is scoped to ``pages/`` and ``journals/`` when they exist, so a
stray Markdown file at the graph root is excluded; a single page reads as a
``note`` node.
"""

from __future__ import annotations

from omni_fetcher.v1.connectors.logseq import LogseqConnector
from omni_fetcher.v1.result import Success


async def test_graph_walk_is_scoped_to_pages_and_journals(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / "journals").mkdir()
    (tmp_path / "pages" / "a.md").write_text("# A\n[[B]]\n", encoding="utf-8")
    (tmp_path / "journals" / "2026_07_25.md").write_text("- a journal entry\n", encoding="utf-8")
    # A stray file at the graph root must NOT be included.
    (tmp_path / "config.md").write_text("# not a page\n", encoding="utf-8")
    root = str(tmp_path).replace("\\", "/")

    result = await LogseqConnector().fetch(f"logseq://{root}")

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "collection"
    assert node.metadata.source_extra["logseq"]["note_count"] == 2
    titles = {child.metadata.source_extra["logseq"]["title"] for child in node.children}
    assert titles == {"a", "2026_07_25"}


async def test_single_page(tmp_path):
    (tmp_path / "page.md").write_text("# Page\ntext\n", encoding="utf-8")
    path = str(tmp_path / "page.md").replace("\\", "/")

    result = await LogseqConnector().fetch(f"logseq://{path}")

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == "note"


async def test_folder_without_subdirs_falls_back_to_whole_root(tmp_path):
    (tmp_path / "loose.md").write_text("# Loose\n", encoding="utf-8")
    root = str(tmp_path).replace("\\", "/")

    result = await LogseqConnector().fetch(f"logseq://{root}")

    assert isinstance(result, Success)
    assert result.tree.metadata.source_extra["logseq"]["note_count"] == 1


def test_can_handle():
    assert LogseqConnector.can_handle("logseq:///graph")
    assert not LogseqConnector.can_handle("markdown:///x.md")
