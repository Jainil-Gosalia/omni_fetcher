"""External-behaviour tests for the stateless ``OmniFetcher`` orchestrator.

These tests exercise only the public surface:

- routing a URI to the registry's resolved fetcher;
- an unrouted URI returning ``error(NOT_FOUND)`` (returned, not raised), from
  both ``fetch()`` and ``stream()``;
- per-call ``auth`` and ``zoom`` being passed through unchanged to the
  fetcher;
- no state retained across two calls (interleaved calls do not contaminate
  each other);
- caller ``tags`` merged into the returned node's ``Metadata.tags`` via the
  contract's deduplicating tag handling.

Each test wires a tiny in-memory ``BaseFetcher`` into a ``FrozenRegistry``;
no connector internals are touched.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from omni_fetcher.v1.atoms import AtomKind, Text
from omni_fetcher.v1.auth import AuthCredential, BearerAuth
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.orchestrator import OmniFetcher
from omni_fetcher.v1.registry import (
    FrozenRegistry,
    SourceDefinition,
)
from omni_fetcher.v1.result import (
    Error,
    Partial,
    Result,
    Success,
    partial,
    success,
)
from omni_fetcher.v1.zoom import DepthLevel, ZoomSpec


def _sentence_zoom() -> ZoomSpec:
    """A concrete per-atom-type zoom spec for pass-through assertions."""
    return ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})

# A call log shared by a test and the fetcher it instantiates. The fetcher
# records what auth/zoom it was invoked with so the test can assert
# pass-through. Each test uses its own list; nothing is process-global.
_CallLog = list[dict[str, object]]


def _record_node(content: str, *, kind: str = "doc") -> CompositionNode:
    """A tiny canonical node carrying one text atom."""
    return build_node(kind=kind, atoms=[Text(content=content)])


def _make_registry(
    name: str,
    pattern: str,
    fetcher_class: type[BaseFetcher],
) -> FrozenRegistry:
    """Build a one-source frozen registry routing ``pattern`` to a class."""
    return FrozenRegistry(
        (
            SourceDefinition(
                name=name,
                fetcher_class=fetcher_class,
                uri_patterns=(pattern,),
            ),
        )
    )


# ---------------------------------------------------------------------------
# Routing


async def test_fetch_routes_to_resolved_fetcher() -> None:
    """fetch() routes the URI to the registry's resolved fetcher."""

    class AlphaFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            yield success(_record_node("alpha", kind="alpha"))

    omni = OmniFetcher(_make_registry("alpha", "alpha://*", AlphaFetcher))

    result = await omni.fetch("alpha://thing")

    assert isinstance(result, Success)
    assert result.tree.metadata.kind == "alpha"


async def test_routing_distinguishes_two_sources() -> None:
    """Each URI is routed to its own distinct fetcher."""

    class AFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            yield success(_record_node("a", kind="a"))

    class BFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            yield success(_record_node("b", kind="b"))

    registry = FrozenRegistry(
        (
            SourceDefinition(
                name="a",
                fetcher_class=AFetcher,
                uri_patterns=("a://*",),
            ),
            SourceDefinition(
                name="b",
                fetcher_class=BFetcher,
                uri_patterns=("b://*",),
            ),
        )
    )
    omni = OmniFetcher(registry)

    a_result = await omni.fetch("a://x")
    b_result = await omni.fetch("b://y")

    assert isinstance(a_result, Success)
    assert isinstance(b_result, Success)
    assert a_result.tree.metadata.kind == "a"
    assert b_result.tree.metadata.kind == "b"


# ---------------------------------------------------------------------------
# Unknown URI -> error(NOT_FOUND), returned not raised


async def test_fetch_unknown_uri_is_not_found() -> None:
    """fetch() on an unrouted URI returns error(NOT_FOUND), not raised."""
    omni = OmniFetcher(FrozenRegistry(()))

    result = await omni.fetch("nope://x")

    assert isinstance(result, Error)
    assert result.kind is ErrorKind.NOT_FOUND
    assert result.locator == "nope://x"


async def test_stream_unknown_uri_yields_single_not_found() -> None:
    """stream() on an unrouted URI yields one NOT_FOUND item, then ends."""
    omni = OmniFetcher(FrozenRegistry(()))

    items = [item async for item in omni.stream("nope://x")]

    assert len(items) == 1
    assert isinstance(items[0], Error)
    assert items[0].kind is ErrorKind.NOT_FOUND


# ---------------------------------------------------------------------------
# Per-call auth + zoom passed through to the fetcher


async def test_fetch_passes_auth_and_zoom_through() -> None:
    """fetch() forwards the per-call auth + zoom to the fetcher unchanged."""
    log: _CallLog = []

    class RecordingFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            log.append({"uri": uri, "auth": auth, "zoom": zoom})
            yield success(_record_node("ok"))

    omni = OmniFetcher(_make_registry("rec", "rec://*", RecordingFetcher))
    cred = BearerAuth(token="secret-token")
    zoom = _sentence_zoom()

    await omni.fetch("rec://x", auth=cred, zoom=zoom)

    assert len(log) == 1
    assert log[0]["uri"] == "rec://x"
    assert log[0]["auth"] is cred
    assert log[0]["zoom"] is zoom


async def test_stream_passes_auth_and_zoom_through() -> None:
    """stream() forwards the per-call auth + zoom to the fetcher unchanged."""
    log: _CallLog = []

    class RecordingFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            log.append({"auth": auth, "zoom": zoom})
            yield success(_record_node("ok"))

    omni = OmniFetcher(_make_registry("rec", "rec://*", RecordingFetcher))
    cred = BearerAuth(token="abc")
    zoom = _sentence_zoom()

    async for _ in omni.stream("rec://x", auth=cred, zoom=zoom):
        pass

    assert len(log) == 1
    assert log[0]["auth"] is cred
    assert log[0]["zoom"] is zoom


# ---------------------------------------------------------------------------
# No state retained across calls


async def test_no_state_retained_across_two_calls() -> None:
    """Two interleaved calls with different auth never contaminate."""
    log: _CallLog = []

    class RecordingFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            log.append({"uri": uri, "auth": auth})
            token = auth.token if isinstance(auth, BearerAuth) else None
            yield success(_record_node(token or "anon", kind="rec"))

    omni = OmniFetcher(_make_registry("rec", "rec://*", RecordingFetcher))

    first = await omni.fetch("rec://1", auth=BearerAuth(token="tenant-1"))
    second = await omni.fetch("rec://2", auth=BearerAuth(token="tenant-2"))

    # Each call saw only its own auth -- no leakage from the other.
    assert log[0]["uri"] == "rec://1"
    assert log[0]["auth"].token == "tenant-1"
    assert log[1]["uri"] == "rec://2"
    assert log[1]["auth"].token == "tenant-2"

    # Each result reflects only its own call's credential.
    assert isinstance(first, Success)
    assert isinstance(second, Success)
    assert [a.content for a in first.tree.iter_atoms()] == ["tenant-1"]
    assert [a.content for a in second.tree.iter_atoms()] == ["tenant-2"]


async def test_fresh_fetcher_instance_per_call() -> None:
    """The orchestrator instantiates a fresh fetcher for every call."""
    instances: list[int] = []

    class CountingFetcher(BaseFetcher):
        def __init__(self) -> None:
            instances.append(id(self))

        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            yield success(_record_node("ok"))

    omni = OmniFetcher(_make_registry("c", "c://*", CountingFetcher))

    await omni.fetch("c://1")
    await omni.fetch("c://2")

    # Two calls -> two distinct fetcher instances (no shared instance held).
    assert len(instances) == 2
    assert instances[0] != instances[1]


# ---------------------------------------------------------------------------
# Caller tags merge into returned node metadata


async def test_fetch_merges_tags_into_metadata() -> None:
    """Caller tags are merged into the returned node's metadata tags."""

    class TaggedFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            node = build_node(kind="doc", atoms=[Text(content="x")])
            node.metadata.tags = ["existing"]
            yield success(node)

    omni = OmniFetcher(_make_registry("t", "t://*", TaggedFetcher))

    result = await omni.fetch("t://x", tags=["alpha", "beta"])

    assert isinstance(result, Success)
    assert result.tree.metadata.tags == ["existing", "alpha", "beta"]


async def test_tag_merge_dedupes_via_contract_handling() -> None:
    """Tag merge uses the contract's dedup/blank-drop tag handling."""

    class TaggedFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            node = build_node(kind="doc", atoms=[Text(content="x")])
            node.metadata.tags = ["dup"]
            yield success(node)

    omni = OmniFetcher(_make_registry("t", "t://*", TaggedFetcher))

    # "dup" repeats the node's own tag; "" is blank and dropped.
    result = await omni.fetch("t://x", tags=["dup", "", "new"])

    assert isinstance(result, Success)
    assert result.tree.metadata.tags == ["dup", "new"]


async def test_tags_merge_into_partial_result() -> None:
    """Tags merge into a Partial's tree too (not only Success)."""

    class PartialFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            from omni_fetcher.v1.result import gap

            node = build_node(kind="doc", atoms=[Text(content="x")])
            yield partial(node, [gap(ErrorKind.UNSUPPORTED)])

    omni = OmniFetcher(_make_registry("p", "p://*", PartialFetcher))

    result = await omni.fetch("p://x", tags=["t1"])

    assert isinstance(result, Partial)
    assert "t1" in result.tree.metadata.tags
    assert len(result.gaps) == 1


async def test_tags_omitted_leaves_tree_unchanged() -> None:
    """With no tags supplied the returned tree is the fetcher's own tree."""
    tree = _record_node("x")
    tree.metadata.tags = ["keep"]

    class PassthroughFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            yield success(tree)

    omni = OmniFetcher(_make_registry("p", "p://*", PassthroughFetcher))

    result = await omni.fetch("p://x")

    # No tag merge happened: the fetcher's own tree flows straight through
    # (no copy made) with its tags untouched.
    assert isinstance(result, Success)
    assert result.tree is tree
    assert result.tree.metadata.tags == ["keep"]


async def test_tags_do_not_mutate_fetcher_tree() -> None:
    """Merging tags copies the node; the fetcher's own tree is untouched."""
    shared_node = build_node(kind="doc", atoms=[Text(content="x")])
    shared_node.metadata.tags = ["orig"]

    class SharingFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom: Optional[ZoomSpec] = None,
        ) -> AsyncIterator[Result]:
            yield success(shared_node)

    omni = OmniFetcher(_make_registry("s", "s://*", SharingFetcher))

    result = await omni.fetch("s://x", tags=["added"])

    assert isinstance(result, Success)
    assert result.tree.metadata.tags == ["orig", "added"]
    # The fetcher's source node is unchanged (merge worked on a copy).
    assert shared_node.metadata.tags == ["orig"]
