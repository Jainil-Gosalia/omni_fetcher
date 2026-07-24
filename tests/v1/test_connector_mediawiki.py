"""External-behaviour tests for the v1 ``mediawiki`` connector.

The MediaWiki API is stubbed with an ``httpx.MockTransport`` (no real network):
a page yields a ``note`` node whose HTML body is a ``Text`` atom, whose outbound
links become ``wikilinks`` and categories become ``tags``; an API ``error``
object and an HTTP status each map onto the taxonomy; a bad URI is
``INVALID_INPUT``.
"""

from __future__ import annotations

import json
from typing import Callable

import httpx

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors.mediawiki import MediaWikiConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success


def _install_transport(monkeypatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _json_handler(payload: object, *, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(payload).encode("utf-8"),
        )

    return handler


_PARSE_OK = {
    "parse": {
        "title": "Python (programming language)",
        "pageid": 23862,
        "revid": 987654,
        "text": "<p>Python is a programming language.</p>",
        "links": [
            {"ns": 0, "title": "Guido van Rossum", "exists": True},
            {"ns": 0, "title": "Interpreter", "exists": True},
        ],
        "categories": [{"category": "Programming languages"}, {"category": "Python"}],
    }
}


async def test_page_yields_note_with_html_atom(monkeypatch):
    _install_transport(monkeypatch, _json_handler(_PARSE_OK))

    result = await MediaWikiConnector().fetch(
        "mediawiki://en.wikipedia.org/wiki/Python_(programming_language)"
    )

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "note"

    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].format == TextFormat.HTML
    assert atoms[0].content == "<p>Python is a programming language.</p>"

    extra = node.metadata.source_extra["mediawiki"]
    assert extra["title"] == "Python (programming language)"
    assert extra["wikilinks"] == ["Guido van Rossum", "Interpreter"]
    assert extra["tags"] == ["Programming languages", "Python"]
    assert extra["host"] == "en.wikipedia.org"
    assert extra["revid"] == 987654


async def test_terse_uri_without_wiki_segment(monkeypatch):
    _install_transport(monkeypatch, _json_handler(_PARSE_OK))

    result = await MediaWikiConnector().fetch("mediawiki://en.wikipedia.org/SomeTitle")

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == "note"


async def test_missing_page_is_not_found(monkeypatch):
    payload = {"error": {"code": "missingtitle", "info": "The page you specified doesn't exist."}}
    _install_transport(monkeypatch, _json_handler(payload))

    result = await MediaWikiConnector().fetch("mediawiki://en.wikipedia.org/wiki/Nope")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_http_500_is_transient(monkeypatch):
    _install_transport(monkeypatch, _json_handler({}, status_code=500))

    result = await MediaWikiConnector().fetch("mediawiki://en.wikipedia.org/wiki/X")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.TRANSIENT


async def test_http_403_is_permission_denied(monkeypatch):
    _install_transport(monkeypatch, _json_handler({}, status_code=403))

    result = await MediaWikiConnector().fetch("mediawiki://wiki.internal/wiki/Secret")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.PERMISSION_DENIED


async def test_bad_uri_is_invalid_input(monkeypatch):
    _install_transport(monkeypatch, _json_handler(_PARSE_OK))

    result = await MediaWikiConnector().fetch("mediawiki://only-a-host")  # no title

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


def test_can_handle():
    assert MediaWikiConnector.can_handle("mediawiki://en.wikipedia.org/wiki/X")
    assert not MediaWikiConnector.can_handle("https://en.wikipedia.org/wiki/X")
