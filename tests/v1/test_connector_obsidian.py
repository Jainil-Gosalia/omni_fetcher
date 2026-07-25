"""External-behaviour tests for the v1 ``obsidian`` connector (real files).

A single note reads as a ``note`` node; a vault folder reads as a
``collection`` of note children; over the note cap the collection degrades to a
``Partial`` with a typed gap.
"""

from __future__ import annotations

from omni_fetcher.v1.connectors.obsidian import ObsidianConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Partial, Success


def _vault(tmp_path):
    (tmp_path / "one.md").write_text("---\ntitle: One\n---\nBody one [[Two]]\n", encoding="utf-8")
    (tmp_path / "two.md").write_text("# Two\n#tag body\n", encoding="utf-8")
    return str(tmp_path).replace("\\", "/")


async def test_single_note(tmp_path):
    (tmp_path / "Solo Note.md").write_text("# Solo\ntext\n", encoding="utf-8")
    path = str(tmp_path / "Solo Note.md").replace("\\", "/")

    result = await ObsidianConnector().fetch(f"obsidian://{path}")

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == "note"
    # Obsidian's note title is the filename stem, not the first H1.
    assert result.tree.metadata.source_extra["obsidian"]["title"] == "Solo Note"


async def test_vault_folder_is_collection(tmp_path):
    root = _vault(tmp_path)

    result = await ObsidianConnector().fetch(f"obsidian://{root}")

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "collection"
    assert node.metadata.source_extra["obsidian"]["note_count"] == 2
    assert len(node.children) == 2
    # Each child is a note node with its own namespace facts.
    kinds = {child.metadata.kind for child in node.children}
    assert kinds == {"note"}


async def test_vault_over_cap_is_partial(tmp_path, monkeypatch):
    root = _vault(tmp_path)  # two notes
    monkeypatch.setattr(ObsidianConnector, "NOTE_CAP", 1)

    result = await ObsidianConnector().fetch(f"obsidian://{root}")

    assert isinstance(result, Partial)
    assert result.tree.metadata.source_extra["obsidian"]["note_count"] == 1
    assert any(g.kind == ErrorKind.UNSUPPORTED for g in result.gaps)


def test_can_handle():
    assert ObsidianConnector.can_handle("obsidian:///vault/note.md")
    assert not ObsidianConnector.can_handle("logseq:///g")
