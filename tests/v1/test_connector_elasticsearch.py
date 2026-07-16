"""External-behaviour tests for the v1 ``elasticsearch`` fetcher (v1.5).

No cluster anywhere: a scripted fake client is injected through the
``_make_client`` seam (recording the spec it was built from, scroll calls,
and cleanup), and the ``elasticsearch-py`` availability flag is
monkeypatched so the suite runs without the ``elasticsearch`` extra
installed:

- a query yields one Success/Partial whose tree is a "search_results"
  container with one "json_document" child per matching document (Text
  atom, format CODE, full _source preserved) and query-level metadata
  (index, query, doc_count, total_hits, took_ms) in
  source_extra["elasticsearch"]; each document's own doc_id/index/score
  live on its own node;
- scroll pagination continues across pages until ?size= is reached or
  hits are exhausted; the scroll cursor is always cleared;
- auth (?user=&password=/?api_key=) reaches the client factory's spec;
- a missing index is NOT_FOUND, a malformed query is INVALID_INPUT, a
  connection failure is TRANSIENT, a scroll failure after some documents
  were collected is a Partial (not a bare error), and zero matches is
  NOT_FOUND;
- fetch() and stream() agree (single yielded item); a missing extra is a
  typed UNSUPPORTED naming it; malformed URIs are INVALID_INPUT.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.connectors import elasticsearch as es_module
from omni_fetcher.v1.connectors.elasticsearch import (
    ElasticsearchFetcher,
    _ElasticsearchNotFoundError,
    _ElasticsearchQueryError,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Partial, Success

pytestmark = pytest.mark.asyncio

URI = "es://search.example.com:9200/logs"


def _hit(doc_id: str, source: Dict[str, Any], *, index: str = "logs", score: float = 1.0):
    return {"_id": doc_id, "_index": index, "_score": score, "_source": source}


def _page(
    hits: List[Dict[str, Any]],
    *,
    scroll_id: Optional[str] = "scroll-1",
    total: int = 0,
    took: int = 5,
) -> Dict[str, Any]:
    return {
        "_scroll_id": scroll_id,
        "took": took,
        "hits": {"total": {"value": total, "relation": "eq"}, "hits": hits},
    }


class _FakeClient:
    """Scripted ``_Client`` recording every interaction."""

    def __init__(
        self,
        pages: List[Dict[str, Any]],
        *,
        search_error: Optional[Exception] = None,
        fail_scroll_after: Optional[int] = None,
    ) -> None:
        self._pages = list(pages)
        self._search_error = search_error
        self._fail_scroll_after = fail_scroll_after
        self.search_calls: List[Dict[str, Any]] = []
        self.scroll_calls: List[Dict[str, Any]] = []
        self.cleared_scroll_ids: List[str] = []
        self.closed = False

    async def search(self, *, index, q, size, scroll) -> Dict[str, Any]:
        self.search_calls.append({"index": index, "q": q, "size": size, "scroll": scroll})
        if self._search_error is not None:
            raise self._search_error
        return self._pages.pop(0)

    async def scroll(self, *, scroll_id, scroll) -> Dict[str, Any]:
        self.scroll_calls.append({"scroll_id": scroll_id, "scroll": scroll})
        if self._fail_scroll_after is not None and len(self.scroll_calls) > self._fail_scroll_after:
            raise ConnectionError("scroll context lost")
        return self._pages.pop(0)

    async def clear_scroll(self, *, scroll_id) -> None:
        self.cleared_scroll_ids.append(scroll_id)

    async def close(self) -> None:
        self.closed = True


def _connector_with(
    fake: _FakeClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[ElasticsearchFetcher, list]:
    """A fetcher whose cluster seam returns the scripted fake, recording specs."""
    monkeypatch.setattr(es_module, "ELASTICSEARCH_AVAILABLE", True)
    connector = ElasticsearchFetcher()
    specs: list = []

    async def make_client(spec, auth):
        specs.append(spec)
        return fake

    connector._make_client = make_client  # type: ignore[method-assign]
    return connector, specs


# ---------------------------------------------------------------------------
# Result shape


async def test_documents_map_onto_a_search_results_container(monkeypatch) -> None:
    """One page: a container with matching json_document children."""
    fake = _FakeClient(
        [
            _page(
                [
                    _hit("1", {"level": "error", "msg": "boom"}, score=2.5),
                    _hit("2", {"level": "info", "msg": "ok"}, score=1.0),
                ],
                scroll_id=None,
                total=2,
                took=7,
            )
        ]
    )
    connector, _ = _connector_with(fake, monkeypatch)

    result = await connector.fetch(URI + "?q=level:error")

    assert isinstance(result, Success)
    container = result.tree
    assert container.metadata.kind == "search_results"
    extra = container.metadata.source_extra["elasticsearch"]
    assert extra["index"] == "logs"
    assert extra["query"] == "level:error"
    assert extra["doc_count"] == 2
    assert extra["total_hits"] == 2
    assert extra["took_ms"] == 7

    assert len(container.children) == 2
    first, second = container.children
    assert first.metadata.kind == "json_document"
    atom = first.find_atoms(AtomKind.TEXT)[0]
    assert atom.format == TextFormat.CODE
    assert '"level": "error"' in atom.content
    assert '"msg": "boom"' in atom.content

    doc_extra = first.metadata.source_extra["elasticsearch"]
    assert doc_extra == {"doc_id": "1", "index": "logs", "score": 2.5}
    assert second.metadata.source_extra["elasticsearch"]["doc_id"] == "2"


async def test_stream_and_fetch_agree_on_the_single_item(monkeypatch) -> None:
    """stream() yields exactly one item; fetch() equals it."""
    fake = _FakeClient([_page([_hit("1", {"a": 1})], scroll_id=None, total=1)])
    connector, _ = _connector_with(fake, monkeypatch)

    items = [item async for item in connector.stream(URI)]
    assert len(items) == 1

    fake2 = _FakeClient([_page([_hit("1", {"a": 1})], scroll_id=None, total=1)])
    connector2, _ = _connector_with(fake2, monkeypatch)
    result = await connector2.fetch(URI)

    assert isinstance(items[0], Success) and isinstance(result, Success)
    assert items[0].tree.metadata.kind == result.tree.metadata.kind == "search_results"


# ---------------------------------------------------------------------------
# Scroll pagination + size bounding


async def test_scroll_continues_across_pages_until_exhausted(monkeypatch) -> None:
    """Multiple pages are consumed via scroll() until hits run out."""
    fake = _FakeClient(
        [
            _page([_hit("1", {"n": 1}), _hit("2", {"n": 2})], scroll_id="s1", total=3),
            _page([_hit("3", {"n": 3})], scroll_id="s1", total=3),
            _page([], scroll_id="s1", total=3),  # exhausted
        ]
    )
    connector, _ = _connector_with(fake, monkeypatch)

    result = await connector.fetch(URI + "?size=1000")

    assert isinstance(result, Success)
    assert len(result.tree.children) == 3
    assert len(fake.scroll_calls) == 2
    assert fake.cleared_scroll_ids == ["s1"]


async def test_size_param_bounds_documents_and_stops_scrolling(monkeypatch) -> None:
    """?size=N stops once N documents are collected, without over-scrolling."""
    fake = _FakeClient(
        [
            _page([_hit("1", {}), _hit("2", {}), _hit("3", {})], scroll_id="s1", total=100),
        ]
    )
    connector, _ = _connector_with(fake, monkeypatch)

    result = await connector.fetch(URI + "?size=2")

    assert isinstance(result, Success)
    assert len(result.tree.children) == 2
    assert fake.scroll_calls == []  # size satisfied from the first page
    assert fake.cleared_scroll_ids == ["s1"]


async def test_default_batch_size_and_scroll_timeout_are_passed(monkeypatch) -> None:
    """Defaults: size=100 request batch, scroll=1m, q=None (match all)."""
    fake = _FakeClient([_page([], scroll_id=None, total=0)])
    connector, _ = _connector_with(fake, monkeypatch)

    await connector.fetch(URI)

    assert fake.search_calls == [{"index": "logs", "q": None, "size": 100, "scroll": "1m"}]


async def test_custom_scroll_timeout_is_forwarded(monkeypatch) -> None:
    """?scroll=<timeout> overrides the default TTL on every request."""
    fake = _FakeClient(
        [
            _page([_hit("1", {})], scroll_id="s1", total=1),
            _page([_hit("2", {})], scroll_id="s1", total=1),
        ]
    )
    connector, _ = _connector_with(fake, monkeypatch)

    await connector.fetch(URI + "?scroll=30s&size=2")

    assert fake.search_calls[0]["scroll"] == "30s"
    assert fake.scroll_calls[0]["scroll"] == "30s"


# ---------------------------------------------------------------------------
# Auth


async def test_auth_travels_through_the_spec(monkeypatch) -> None:
    """?user=&password=/?api_key= reach the client factory's spec."""
    fake = _FakeClient([_page([], scroll_id=None, total=0)])
    connector, specs = _connector_with(fake, monkeypatch)

    await connector.fetch(URI + "?user=alice&password=hunter2")

    assert specs[0].user == "alice"
    assert specs[0].password == "hunter2"
    assert specs[0].api_key is None


async def test_api_key_auth_travels_through_the_spec(monkeypatch) -> None:
    """?api_key= is captured independently of user/password."""
    fake = _FakeClient([_page([], scroll_id=None, total=0)])
    connector, specs = _connector_with(fake, monkeypatch)

    await connector.fetch(URI + "?api_key=secret123")

    assert specs[0].api_key == "secret123"
    assert specs[0].user is None


# ---------------------------------------------------------------------------
# Error cases


async def test_missing_index_is_not_found(monkeypatch) -> None:
    """A NotFoundError from the client seam maps to Error(NOT_FOUND)."""
    fake = _FakeClient([], search_error=_ElasticsearchNotFoundError("index_not_found_exception"))
    connector, _ = _connector_with(fake, monkeypatch)

    result = await connector.fetch(URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND
    assert fake.closed


async def test_malformed_query_is_invalid_input(monkeypatch) -> None:
    """A QueryError from the client seam maps to Error(INVALID_INPUT)."""
    fake = _FakeClient([], search_error=_ElasticsearchQueryError("parsing_exception"))
    connector, _ = _connector_with(fake, monkeypatch)

    result = await connector.fetch(URI + "?q=bad[query")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_connect_failure_is_typed_transient(monkeypatch) -> None:
    """A connection-factory failure yields one typed TRANSIENT."""
    monkeypatch.setattr(es_module, "ELASTICSEARCH_AVAILABLE", True)
    connector = ElasticsearchFetcher()

    async def make_client(spec, auth):
        raise ConnectionRefusedError("no route to host")

    connector._make_client = make_client  # type: ignore[method-assign]

    result = await connector.fetch(URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.TRANSIENT


async def test_search_transport_failure_is_transient(monkeypatch) -> None:
    """A generic search failure (not classified) yields TRANSIENT."""
    fake = _FakeClient([], search_error=TimeoutError("upstream timeout"))
    connector, _ = _connector_with(fake, monkeypatch)

    result = await connector.fetch(URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.TRANSIENT
    assert fake.closed


async def test_no_matches_is_not_found(monkeypatch) -> None:
    """A query matching zero documents is an honest NOT_FOUND."""
    fake = _FakeClient([_page([], scroll_id=None, total=0)])
    connector, _ = _connector_with(fake, monkeypatch)

    result = await connector.fetch(URI + "?q=nothing_matches_this")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_scroll_failure_after_partial_progress_is_partial(monkeypatch) -> None:
    """A scroll failure after some docs were collected returns a Partial."""
    fake = _FakeClient(
        [_page([_hit("1", {})], scroll_id="s1", total=100)],
        fail_scroll_after=0,
    )
    connector, _ = _connector_with(fake, monkeypatch)

    result = await connector.fetch(URI + "?size=1000")

    assert isinstance(result, Partial)
    assert len(result.tree.children) == 1
    assert len(result.gaps) == 1
    assert result.gaps[0].kind == ErrorKind.TRANSIENT
    assert fake.cleared_scroll_ids == ["s1"]


# ---------------------------------------------------------------------------
# Cleanup contract


async def test_client_always_closed_and_scroll_cleared_once(monkeypatch) -> None:
    """clear_scroll and close are each called exactly once on a clean run."""
    fake = _FakeClient(
        [
            _page([_hit("1", {})], scroll_id="s1", total=1),
            _page([], scroll_id="s1", total=1),
        ]
    )
    connector, _ = _connector_with(fake, monkeypatch)

    await connector.fetch(URI)

    assert fake.cleared_scroll_ids == ["s1"]
    assert fake.closed


async def test_client_closed_even_on_missing_index(monkeypatch) -> None:
    """close() still runs when search() fails before any scroll_id exists."""
    fake = _FakeClient([], search_error=_ElasticsearchNotFoundError("nope"))
    connector, _ = _connector_with(fake, monkeypatch)

    await connector.fetch(URI)

    assert fake.cleared_scroll_ids == []  # no scroll_id was ever obtained
    assert fake.closed


# ---------------------------------------------------------------------------
# Stream-only-single-item + gating contract


async def test_missing_extra_is_typed_unsupported(monkeypatch) -> None:
    """Without elasticsearch-py, fetch() yields one UNSUPPORTED naming the extra."""
    monkeypatch.setattr(es_module, "ELASTICSEARCH_AVAILABLE", False)

    result = await ElasticsearchFetcher().fetch(URI)

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.UNSUPPORTED
    assert "elasticsearch" in result.message


async def test_malformed_uri_is_invalid_input(monkeypatch) -> None:
    """An es:// URI without an index is a typed INVALID_INPUT."""
    monkeypatch.setattr(es_module, "ELASTICSEARCH_AVAILABLE", True)

    result = await ElasticsearchFetcher().fetch("es://host-only")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_non_integer_size_is_invalid_input(monkeypatch) -> None:
    """?size= must be a positive integer."""
    monkeypatch.setattr(es_module, "ELASTICSEARCH_AVAILABLE", True)

    result = await ElasticsearchFetcher().fetch(URI + "?size=nope")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_zero_size_is_invalid_input(monkeypatch) -> None:
    """?size=0 (or negative) is rejected."""
    monkeypatch.setattr(es_module, "ELASTICSEARCH_AVAILABLE", True)

    result = await ElasticsearchFetcher().fetch(URI + "?size=0")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_can_handle_es_scheme() -> None:
    """can_handle() recognises es:// and rejects other schemes."""
    assert ElasticsearchFetcher.can_handle("es://host/index")
    assert not ElasticsearchFetcher.can_handle("http://host/index")
