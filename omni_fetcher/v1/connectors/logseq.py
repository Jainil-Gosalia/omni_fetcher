"""The ``logseq`` connector for the OmniFetcher v1 contract (v1.14).

Reads a page from a Logseq graph -- or a whole graph folder -- and maps it onto
the canonical contract via the knowledge-base spec (``_wiki_notes``): a
``kind="note"`` node carrying the Markdown/Org body as a ``Text`` atom, with the
title, frontmatter, ``[[wikilinks]]``, and ``#tags`` in
``source_extra["logseq"]``. A graph folder yields a ``kind="collection"``.

URI: ``logseq://<path>`` -- a single page file, or a graph folder. A Logseq
graph keeps its pages under ``pages/`` and ``journals/``; when the given folder
has those subdirectories the walk is scoped to them (falling back to the whole
folder otherwise), which is the only thing that distinguishes this from the
plain ``markdown`` connector. No optional extra is needed.
"""

from __future__ import annotations

from omni_fetcher.v1.connectors._local_notes import LocalNotesConnector


class LogseqConnector(LocalNotesConnector):
    """
    Canonical v1 connector for Logseq graph pages
    ===============================================
    Fetches a single Logseq page or a whole graph folder, emitting a ``note``
    node (or a ``collection`` of them) with the body as a ``Text`` atom and the
    page's graph (frontmatter, ``[[wikilinks]]``, ``#tags``) in
    ``source_extra["logseq"]``. A graph walk is scoped to ``pages/`` and
    ``journals/`` when present. Read-only.
    ===============================================

    Methods
    -------
        stream:
        can_handle:
    """

    SCHEME = "logseq://"
    NAMESPACE = "logseq"
    SUBDIRS = ("pages", "journals")
