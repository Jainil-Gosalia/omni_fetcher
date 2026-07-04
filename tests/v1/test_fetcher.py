"""External-behaviour tests for the v1 fetcher protocol + mapping helper.

These tests exercise only the public surface:

- the base ``BaseFetcher.fetch()`` collects a bounded ``stream()`` into one
  ``Result`` per the documented collection semantics (single terminal item
  returned as-is; multiple items assembled under one root; empty stream is a
  ``NOT_FOUND`` error; errors are never dropped);
- a bounded stream terminates and ``fetch()`` equals collecting that stream;
- streamed items carry a timestamp + monotonic sequence stamped via the
  mapping helper;
- the mapping helper builds canonical nodes (advisory ``kind`` set,
  source-specific data in ``source_extra``, content in atoms).

No connector internals are touched: each test defines a tiny in-memory
``BaseFetcher`` subclass whose ``stream()`` yields known results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import pytest

from omni_fetcher.v1.atoms import AtomKind, Text
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import COLLECTION_KIND, BaseFetcher
from omni_fetcher.v1.mapping import (
    SequenceCounter,
    build_node,
    now_utc,
    stamp_temporal,
)
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Error,
    Partial,
    Result,
    Success,
    error,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec


class _ScriptedFetcher(BaseFetcher):
    """A fetcher whose ``stream()`` replays a fixed list of results."""

    def __init__(self, items: list[Result]) -> None:
        self._items = items

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        for item in self._items:
            yield item


def _text_node(content: str, *, kind: str = "doc") -> CompositionNode:
    """A tiny canonical node carrying one text atom."""
    return build_node(kind=kind, atoms=[Text(content=content)])


# ---------------------------------------------------------------------------
# fetch() collection semantics


async def test_single_success_returned_as_is() -> None:
    """A lone terminal Success is fetch()'s result unchanged."""
    tree = _text_node("hello")
    fetcher = _ScriptedFetcher([success(tree)])

    result = await fetcher.fetch("mem://x")

    assert isinstance(result, Success)
    assert result.tree is tree


async def test_fetch_equals_collecting_stream() -> None:
    """A bounded source's fetch() equals collecting its stream()."""
    items = [success(_text_node("a")), success(_text_node("b"))]
    fetcher = _ScriptedFetcher(items)

    # Collect the stream by hand.
    collected: list[Result] = []
    async for item in fetcher.stream("mem://x"):
        collected.append(item)

    result = await fetcher.fetch("mem://x")

    # The stream terminated (bounded) and yielded exactly the scripted items.
    assert len(collected) == 2
    # fetch() assembled those same two trees under one collection root.
    assert isinstance(result, Success)
    assert result.tree.metadata.kind == COLLECTION_KIND
    children = list(result.tree.iter_children())
    assert len(children) == 2
    assert [c.metadata.kind for c in children] == ["doc", "doc"]
    # The collected leaf content matches what the stream yielded.
    texts = [a.content for a in result.tree.iter_atoms()]
    assert texts == ["a", "b"]


async def test_empty_stream_is_not_found() -> None:
    """An empty bounded stream is a NOT_FOUND error, not a silent success."""
    fetcher = _ScriptedFetcher([])

    result = await fetcher.fetch("mem://empty")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND


async def test_error_only_stream_surfaces_error() -> None:
    """A stream of only errors surfaces an Error (never dropped)."""
    fetcher = _ScriptedFetcher([error(ErrorKind.AUTH_FAILED, message="bad token")])

    result = await fetcher.fetch("mem://x")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.AUTH_FAILED


async def test_error_mixed_with_trees_becomes_partial() -> None:
    """An error mixed with built trees is folded into a Partial's gaps."""
    fetcher = _ScriptedFetcher(
        [
            success(_text_node("ok")),
            error(
                ErrorKind.UNSUPPORTED,
                message="cannot render widget",
                locator="mem://x#widget",
            ),
        ]
    )

    result = await fetcher.fetch("mem://x")

    assert isinstance(result, Partial)
    # The built tree is preserved.
    assert "ok" in [a.content for a in result.tree.iter_atoms()]
    # The error is surfaced as a gap, not dropped.
    assert len(result.gaps) == 1
    assert result.gaps[0].kind is ErrorKind.UNSUPPORTED
    assert result.gaps[0].locator == "mem://x#widget"


async def test_upstream_partial_gaps_are_aggregated() -> None:
    """Gaps from an upstream Partial are carried into the final result."""
    g = partial(_text_node("p"), [_gap()])
    fetcher = _ScriptedFetcher([success(_text_node("s")), g])

    result = await fetcher.fetch("mem://x")

    assert isinstance(result, Partial)
    assert len(result.gaps) == 1
    assert result.gaps[0].kind is ErrorKind.UNSUPPORTED


async def test_single_partial_returned_as_partial() -> None:
    """A lone Partial item stays a Partial through fetch()."""
    g = partial(_text_node("p"), [_gap()])
    fetcher = _ScriptedFetcher([g])

    result = await fetcher.fetch("mem://x")

    assert isinstance(result, Partial)
    assert len(result.gaps) == 1


def _gap():
    """A reusable typed gap for partial-result tests."""
    from omni_fetcher.v1.result import gap

    return gap(ErrorKind.UNSUPPORTED, detail="skipped")


# ---------------------------------------------------------------------------
# Streamed items carry timestamp + monotonic sequence via the helper


async def test_streamed_items_carry_timestamp_and_sequence() -> None:
    """Each streamed node carries a stamped timestamp + monotonic sequence."""

    class _TemporalFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            seq = SequenceCounter()
            ts = datetime(2026, 6, 19, tzinfo=timezone.utc)
            for i in range(3):
                node = _text_node(f"event-{i}", kind="event")
                stamp_temporal(node, sequence=seq.next(), timestamp=ts)
                yield success(node)

    fetcher = _TemporalFetcher()

    sequences: list[int] = []
    timestamps: list[datetime] = []
    async for item in fetcher.stream("mem://events"):
        assert isinstance(item, Success)
        temporal = item.tree.metadata.temporal
        assert temporal.sequence is not None
        assert temporal.timestamp is not None
        sequences.append(temporal.sequence)
        timestamps.append(temporal.timestamp)

    # Monotonic, gap-free sequence starting at 0.
    assert sequences == [0, 1, 2]
    # Timestamp lives in metadata, not on the atom.
    assert all(ts.tzinfo is not None for ts in timestamps)


def test_sequence_counter_is_monotonic_and_per_stream() -> None:
    """SequenceCounter hands out monotonic numbers and is independent."""
    a = SequenceCounter()
    assert [a.next(), a.next(), a.next()] == [0, 1, 2]
    assert a.peek() == 3

    # A separate stream's counter is independent (not global state).
    b = SequenceCounter(start=100)
    assert b.next() == 100
    assert a.next() == 3  # unaffected by b

    a.reset()
    assert a.next() == 0


def test_now_utc_is_timezone_aware() -> None:
    """now_utc returns a timezone-aware UTC timestamp for metadata stamping."""
    now = now_utc()
    assert now.tzinfo is not None


# ---------------------------------------------------------------------------
# Mapping helper builds canonical nodes


def test_build_node_sets_kind_and_atoms() -> None:
    """build_node sets the advisory kind and attaches content as atoms."""
    node = build_node(kind="issue", atoms=[Text(content="title")])

    assert node.metadata.kind == "issue"
    atoms = list(node.iter_atoms())
    assert len(atoms) == 1
    assert atoms[0].kind is AtomKind.TEXT
    assert atoms[0].content == "title"


def test_build_node_puts_source_data_in_source_extra() -> None:
    """Source-specific descriptive fields are namespaced in source_extra."""
    node = build_node(
        kind="issue",
        atoms=[Text(content="body")],
        id="123",
        author="octocat",
        source_url="https://github.com/o/r/issues/1",
        source_namespace="github",
        source_fields={"number": 1, "state": "open"},
    )

    md = node.metadata
    # Common core populated.
    assert md.id == "123"
    assert md.author == "octocat"
    assert md.source_url == "https://github.com/o/r/issues/1"
    # Source-specific data lives namespaced under source_extra...
    assert md.source_extra["github"] == {"number": 1, "state": "open"}
    # ...and is NOT inlined onto the content atom (atoms are content-only).
    atom = next(node.iter_atoms())
    assert set(atom.model_dump().keys()) == {
        "kind",
        "content",
        "format",
        "language",
        "encoding",
    }


def test_build_node_nests_child_nodes_after_atoms() -> None:
    """build_node appends child nodes after atoms, preserving order."""
    child = _text_node("child", kind="row")
    node = build_node(
        kind="table",
        atoms=[Text(content="caption")],
        children=[child],
    )

    children = list(node.iter_children())
    assert isinstance(children[0], Text)
    assert isinstance(children[1], CompositionNode)
    assert children[1].metadata.kind == "row"


def test_build_node_rejects_blank_source_namespace() -> None:
    """A blank source namespace is rejected (namespacing invariant)."""
    with pytest.raises(ValueError):
        build_node(kind="x", source_namespace="  ", source_fields={"a": 1})
