"""The ``obsidian`` connector for the OmniFetcher v1 contract (v1.14).

Reads a note from an Obsidian vault -- or a whole vault folder -- and maps it
onto the canonical contract via the knowledge-base spec (``_wiki_notes``): a
``kind="note"`` node carrying the Markdown body as a ``Text`` atom, with the
title, YAML frontmatter, ``[[wikilinks]]``, and ``#tags`` in
``source_extra["obsidian"]``. A vault folder yields a ``kind="collection"`` of
its notes.

URI: ``obsidian://<path>`` -- a single ``.md`` note, or a vault folder (walked
for ``.md``/``.markdown``/``.mdx``/``.org`` notes, capped). An Obsidian vault is
just a folder of Markdown files, so this connector is the local-notes base with
the ``obsidian`` namespace; no optional extra is needed.
"""

from __future__ import annotations

from omni_fetcher.v1.connectors._local_notes import LocalNotesConnector


class ObsidianConnector(LocalNotesConnector):
    """
    Canonical v1 connector for Obsidian vault notes
    ===============================================
    Fetches a single Obsidian note or a whole vault folder, emitting a ``note``
    node (or a ``collection`` of them) with the Markdown body as a ``Text`` atom
    and the note's graph (frontmatter, ``[[wikilinks]]``, ``#tags``) in
    ``source_extra["obsidian"]``. Read-only.
    ===============================================

    Methods
    -------
        stream:
        can_handle:
    """

    SCHEME = "obsidian://"
    NAMESPACE = "obsidian"
