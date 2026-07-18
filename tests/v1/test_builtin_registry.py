"""External-behaviour tests for ``omni_fetcher.v1.builtin_registry``.

What is asserted:

- every built-in source's documented URI shapes route to the right
  connector class (by name -- resolution returns the lazy stand-in);
- resolution is pattern-only: heavy connector modules are not imported by
  building the registry or routing, only by instantiating the resolved
  class (proven with an import blocker for ``yt_dlp``);
- a missing optional extra skips that source without breaking the rest;
- the wiring one-liner ``OmniFetcher(builtin_registry())`` fetches a real
  local file end to end;
- the registry is immutable and two builds route identically.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from omni_fetcher.v1 import (
    AtomKind,
    BaseFetcher,
    OmniFetcher,
    Success,
    builtin_registry,
)
from omni_fetcher.v1 import builtin as builtin_module

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Routing table


ROUTES: list[tuple[str, str]] = [
    ("jira://issue/PROJ-1", "JiraConnector"),
    ("https://acme.atlassian.net/browse/PROJ-1", "JiraConnector"),
    ("https://acme.atlassian.net/wiki/spaces/ENG", "ConfluenceConnector"),
    ("slack://channel/C0123456789", "SlackConnector"),
    ("tail:///var/log/app.log?from=end", "TailConnector"),
    ("notion://database/abc123", "NotionConnector"),
    ("https://www.notion.so/Page-abc123", "NotionConnector"),
    ("linear://issue/ABC-1", "LinearConnector"),
    ("https://linear.app/acme/issue/ABC-1", "LinearConnector"),
    ("sharepoint://team/Documents", "SharePointConnector"),
    ("https://acme.sharepoint.com/sites/eng", "SharePointConnector"),
    ("s3://bucket/data/key.csv", "S3Fetcher"),
    ("https://github.com/psf/requests/issues/42", "GitHubConnector"),
    ("https://api.github.com/repos/psf/requests", "GitHubConnector"),
    ("https://drive.google.com/file/d/ABC", "GoogleDriveFetcher"),
    ("https://docs.google.com/document/d/DOC1", "GoogleDriveFetcher"),
    ("https://youtube.com/watch?v=xyz123", "YouTubeConnector"),
    ("https://youtu.be/xyz123", "YouTubeConnector"),
    ("https://api.example.com/graphql", "GraphQLConnector"),
    ("file:///music/song.mp3", "AudioConnector"),
    ("/home/user/track.flac", "AudioConnector"),
    ("report.docx", "DocxConnector"),
    ("file:///deck.pptx", "PptxConnector"),
    ("/docs/paper.pdf", "PDFConnector"),
    ("data/sales.csv", "CSVConnector"),
    ("sqlite:///data/app.db?table=users", "SQLiteQueryConnector"),
    ("https://blog.example.com/feed.xml", "RSSConnector"),
    ("https://example.com/rss", "RSSConnector"),
    ("https://example.com/api/item", "HTTPJSONConnector"),
    ("file:///notes.txt", "LocalFileFetcher"),
    ("https://example.com/page", "HTTPURLConnector"),
    ("README.md", "LocalFileFetcher"),
    ("C:\\Users\\me\\notes.txt", "LocalFileFetcher"),
]

# Routed only when their optional extras are probed available (see the
# extras section below) -- kept separate from ROUTES since a stock test
# environment has neither aiokafka, websockets, nor aiohttp installed.
EXTRA_GATED_ROUTES: list[tuple[str, str]] = [
    ("kafka://broker:9092/events?offset=latest", "KafkaConnector"),
    ("ws://live.example.com/events", "WebSocketConnector"),
    ("wss://live.example.com/events", "WebSocketConnector"),
    ("sse://events.example.com/live", "SSEConnector"),
    ("sses://events.example.com/live", "SSEConnector"),
    ("es://search.example.com:9200/logs?q=error", "ElasticsearchFetcher"),
    ("postgres-cdc://db.example.com:5432/mydb?slot=feed", "PostgresCDCConnector"),
    ("postgres://db.example.com:5432/app?query=SELECT%201", "PostgresQueryConnector"),
]


@pytest.mark.parametrize(("uri", "expected"), ROUTES)
async def test_documented_uri_shapes_route(uri: str, expected: str) -> None:
    """Each documented URI shape resolves to its connector (by class name)."""
    registry = builtin_registry()

    resolved = registry.resolve(uri)

    assert resolved is not None, f"no source claimed {uri!r}"
    assert resolved.__name__ == expected


async def test_personal_sharepoint_sites_are_not_claimed_by_sharepoint() -> None:
    """A personal ``-my`` SharePoint URL falls through to generic HTTP."""
    registry = builtin_registry()

    resolved = registry.resolve("https://acme-my.sharepoint.com/personal/me")

    assert resolved is not None
    assert resolved.__name__ == "HTTPURLConnector"


async def test_unknown_scheme_is_unrouted() -> None:
    """A URI with an unknown scheme matches nothing (NOT_FOUND at fetch)."""
    registry = builtin_registry()

    assert registry.resolve("nope://x") is None


# ---------------------------------------------------------------------------
# Laziness


class _ImportBlocker:
    """Meta-path finder that fails imports for the given top-level names."""

    def __init__(self, names: frozenset[str]) -> None:
        self.names = names

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname.split(".")[0] in self.names:
            raise ImportError(f"blocked for test: {fullname}")
        return None


async def test_registry_and_routing_do_not_import_heavy_connectors() -> None:
    """Building + routing never import yt_dlp; only instantiation does."""
    blocked = frozenset({"yt_dlp"})
    blocker = _ImportBlocker(blocked)
    saved = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name.split(".")[0] in blocked or name.startswith("omni_fetcher.v1.connectors.youtube")
    }
    sys.meta_path.insert(0, blocker)
    try:
        registry = builtin_registry()
        resolved = registry.resolve("https://youtube.com/watch?v=xyz")
        assert resolved is not None and resolved.__name__ == "YouTubeConnector"

        # The real module import happens only at instantiation -- and is
        # blocked, proving it had not happened earlier.
        with pytest.raises(ImportError):
            resolved()
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(saved)


async def test_resolved_class_instantiates_the_real_connector() -> None:
    """Instantiating a resolved proxy yields the real connector instance."""
    registry = builtin_registry()
    resolved = registry.resolve("data/sales.csv")
    assert resolved is not None

    instance = resolved()

    assert type(instance).__name__ == "CSVConnector"
    assert type(instance).__module__ == "omni_fetcher.v1.connectors.csv"
    assert isinstance(instance, BaseFetcher)


# ---------------------------------------------------------------------------
# Optional extras


async def test_kafka_routes_when_the_extra_is_available(monkeypatch) -> None:
    """With aiokafka probed available, kafka:// routes (still lazily)."""
    monkeypatch.setattr(builtin_module, "_extra_available", lambda module: True)

    registry = builtin_registry()

    resolved = registry.resolve("kafka://broker:9092/events?offset=latest")
    assert resolved is not None and resolved.__name__ == "KafkaConnector"


async def test_kafka_is_skipped_without_the_extra(monkeypatch) -> None:
    """Without aiokafka, the kafka source is skipped; others survive."""
    real_probe = builtin_module._extra_available
    monkeypatch.setattr(
        builtin_module,
        "_extra_available",
        lambda module: False if module == "aiokafka" else real_probe(module),
    )

    registry = builtin_registry()

    assert "kafka" not in [definition.name for definition in registry.definitions()]
    assert registry.resolve("tail:///var/log/app.log") is not None


@pytest.mark.parametrize(("uri", "expected"), EXTRA_GATED_ROUTES)
async def test_documented_uri_shapes_route_when_extra_available(
    monkeypatch, uri: str, expected: str
) -> None:
    """With every optional extra probed available, gated sources still route."""
    monkeypatch.setattr(builtin_module, "_extra_available", lambda module: True)

    registry = builtin_registry()

    resolved = registry.resolve(uri)
    assert resolved is not None, f"no source claimed {uri!r}"
    assert resolved.__name__ == expected


async def test_websocket_is_skipped_without_the_extra(monkeypatch) -> None:
    """Without websockets, ws(s):// sources are skipped; others survive."""
    real_probe = builtin_module._extra_available
    monkeypatch.setattr(
        builtin_module,
        "_extra_available",
        lambda module: False if module == "websockets" else real_probe(module),
    )

    registry = builtin_registry()

    assert "websocket" not in [definition.name for definition in registry.definitions()]
    assert registry.resolve("tail:///var/log/app.log") is not None


async def test_sse_is_skipped_without_the_extra(monkeypatch) -> None:
    """Without aiohttp, sse(s):// sources are skipped; others survive."""
    real_probe = builtin_module._extra_available
    monkeypatch.setattr(
        builtin_module,
        "_extra_available",
        lambda module: False if module == "aiohttp" else real_probe(module),
    )

    registry = builtin_registry()

    assert "sse" not in [definition.name for definition in registry.definitions()]
    assert registry.resolve("tail:///var/log/app.log") is not None


async def test_elasticsearch_is_skipped_without_the_extra(monkeypatch) -> None:
    """Without elasticsearch-py, es:// sources are skipped; others survive."""
    real_probe = builtin_module._extra_available
    monkeypatch.setattr(
        builtin_module,
        "_extra_available",
        lambda module: False if module == "elasticsearch" else real_probe(module),
    )

    registry = builtin_registry()

    assert "elasticsearch" not in [definition.name for definition in registry.definitions()]
    assert registry.resolve("tail:///var/log/app.log") is not None


async def test_postgres_cdc_is_skipped_without_the_extra(monkeypatch) -> None:
    """Without asyncpg, postgres-cdc:// sources are skipped; others survive."""
    real_probe = builtin_module._extra_available
    monkeypatch.setattr(
        builtin_module,
        "_extra_available",
        lambda module: False if module == "asyncpg" else real_probe(module),
    )

    registry = builtin_registry()

    assert "postgres_cdc" not in [definition.name for definition in registry.definitions()]
    # A hyphenated scheme must never fall through to the local-file
    # fallback -- it stays unrouted (NOT_FOUND), like any unknown scheme.
    assert registry.resolve("postgres-cdc://db.example.com/mydb") is None
    assert registry.resolve("tail:///var/log/app.log") is not None


async def test_missing_extra_skips_source_and_keeps_the_rest(monkeypatch) -> None:
    """With python-docx absent, docx is skipped and other routes survive."""
    real_probe = builtin_module._extra_available
    monkeypatch.setattr(
        builtin_module,
        "_extra_available",
        lambda module_name: False if module_name == "docx" else real_probe(module_name),
    )

    registry = builtin_registry()

    names = [definition.name for definition in registry.definitions()]
    assert "docx" not in names
    # A .docx URI now falls back to the schemeless local-path route.
    resolved = registry.resolve("report.docx")
    assert resolved is not None and resolved.__name__ == "LocalFileFetcher"
    # Unrelated sources are unaffected.
    assert registry.resolve("jira://issue/PROJ-1") is not None


# ---------------------------------------------------------------------------
# End-to-end wiring + immutability


async def test_one_liner_wiring_fetches_a_local_file(tmp_path: Path) -> None:
    """The README one-liner works: OmniFetcher(builtin_registry()).fetch()."""
    doc = tmp_path / "notes.md"
    doc.write_text("# Title\n\nhello builtin registry\n", encoding="utf-8")

    result = await OmniFetcher(builtin_registry()).fetch(str(doc))

    assert isinstance(result, Success)
    texts = result.tree.find_atoms(AtomKind.TEXT)
    assert texts and "hello builtin registry" in texts[0].content


async def test_two_builds_route_identically_and_are_immutable() -> None:
    """builtin_registry() is deterministic and produces frozen registries."""
    first = builtin_registry()
    second = builtin_registry()

    assert [d.name for d in first.definitions()] == [d.name for d in second.definitions()]
    assert [d.priority for d in first.definitions()] == [d.priority for d in second.definitions()]
    with pytest.raises(AttributeError):
        first.anything = 1  # type: ignore[attr-defined]
