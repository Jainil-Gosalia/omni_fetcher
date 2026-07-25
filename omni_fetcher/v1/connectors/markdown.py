"""The ``markdown`` connector for the OmniFetcher v1 contract (v1.14).

Reads a local Markdown (or Org) note -- or a folder of them -- and maps it onto
the canonical contract via the knowledge-base spec (``_wiki_notes``): a
``kind="note"`` node carrying one ``Text`` atom (the body, ``TextFormat.MARKDOWN``
for ``.md``/``.markdown``/``.mdx``, ``PLAIN`` for ``.org``), with the title,
YAML frontmatter, ``[[wikilinks]]``, and ``#tags`` in ``source_extra["markdown"]``.
A folder yields a ``kind="collection"`` of note children.

URI: ``markdown://<path>`` (``markdown:///abs/note.md`` or ``markdown://rel.md``).
Local files need no credential and no optional extra (PyYAML is a core
dependency). The generic member of the knowledge-base family; Obsidian and
Logseq add vault conventions on top of the same base.
"""

from __future__ import annotations

from omni_fetcher.v1.connectors._local_notes import LocalNotesConnector


class MarkdownConnector(LocalNotesConnector):
    """
    Canonical v1 connector for local Markdown/Org notes
    ===============================================
    Fetches a single Markdown/Org note or a folder of them from the local
    filesystem, emitting a ``note`` node (or a ``collection`` of them) with the
    body as a ``Text`` atom and the note's graph (frontmatter, wikilinks, tags)
    in ``source_extra["markdown"]``. Read-only.
    ===============================================

    Methods
    -------
        stream:
        can_handle:
    """

    SCHEME = "markdown://"
    NAMESPACE = "markdown"
