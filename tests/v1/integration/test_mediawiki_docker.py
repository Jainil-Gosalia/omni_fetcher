"""Real HTTP integration test for the v1 ``mediawiki`` connector.

Drives ``MediaWikiConnector`` through its real ``httpx`` client over an actual
socket against a Dockerised HTTP server (nginx) serving a MediaWiki
``action=parse`` API response, addressed via the connector's ``?endpoint=``
option. Unlike the ``MockTransport`` unit tests, this exercises the real network
round-trip and JSON decode. Skipped unless the server is reachable at
``$OMNI_TEST_WIKI_ENDPOINT`` (default ``http://localhost:8090/w/api.php``).

Spin one up with Docker (serving a ``w/api.php`` file that returns the JSON):

    docker run -d --name omni-wiki -p 8090:80 -v <dir>:/usr/share/nginx/html:ro nginx:alpine
"""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors.mediawiki import MediaWikiConnector
from omni_fetcher.v1.result import Success

_ENDPOINT = os.environ.get("OMNI_TEST_WIKI_ENDPOINT", "http://localhost:8090/w/api.php")


async def _first(stream):
    return await stream.__aiter__().__anext__()


@pytest.fixture
def wiki_up():
    try:
        httpx.get(_ENDPOINT, timeout=3)
    except Exception:
        pytest.skip(f"no MediaWiki HTTP source reachable at {_ENDPOINT}")
    yield


async def test_fetch_page_over_real_http(wiki_up):
    uri = f"mediawiki://localhost/wiki/Docker_Test_Page?endpoint={quote(_ENDPOINT, safe='')}"

    result = await _first(MediaWikiConnector().stream(uri))

    assert isinstance(result, Success), result
    node = result.tree
    assert node.metadata.kind == "note"

    atoms = list(node.iter_atoms())
    assert atoms[0].kind == AtomKind.TEXT
    assert atoms[0].format == TextFormat.HTML
    assert atoms[0].content == "<p>Hello from a real MediaWiki API over HTTP.</p>"

    extra = node.metadata.source_extra["mediawiki"]
    assert extra["title"] == "Docker Test Page"
    assert extra["wikilinks"] == ["Linked Page", "Second Link"]
    assert extra["tags"] == ["Test Pages", "Examples"]
    assert extra["revid"] == 100
