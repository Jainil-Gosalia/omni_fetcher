"""Central zoom application tests (issue 007, extended).

Zoom works for every connector at once, in *both* directions: when a
``ZoomSpec`` is supplied, ``BaseFetcher.fetch()`` and the orchestrator's
streaming path apply the pure ``decompose_result`` (finer-than-natural) and
then ``prune_to_zoom`` (coarser-than-natural) to each returned tree.
Connectors that ignore ``zoom`` therefore honor it.

- Pruning semantics are pinned by ``test_zoom.py``: atoms deeper than the
  requested budget are collapsed away, content is never windowed, and the
  input tree is never mutated.
- Decomposition semantics -- including which (format, level) pairs split,
  answer whole, or gap -- are pinned by ``test_decompose.py``.

This file proves the *wiring*: that both halves reach every connector via the
two central seams, in the right order. Proven with zoom-ignoring scripted
fetchers, the real local_file connector, and the orchestrator's fetch +
stream paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Optional

import pytest

from omni_fetcher.v1 import DepthLevel, ZoomSpec
from omni_fetcher.v1.atoms import AtomKind, Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.connectors.local_file import LocalFileFetcher
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.orchestrator import OmniFetcher
from omni_fetcher.v1.registry import FrozenRegistry, SourceDefinition
from omni_fetcher.v1.result import (
    Partial,
    Result,
    Success,
    error,
    gap,
    partial,
    success,
)

pytestmark = pytest.mark.asyncio

WHOLE_TEXT = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.WHOLE})


def _deep_tree() -> CompositionNode:
    """A natural tree with text atoms at the root and two levels down.

    Atoms assert ``PLAIN`` rather than taking the ``format`` default, which is
    ``OPAQUE`` -- "no claim about this syntax". These are prose fixtures for
    *pruning* tests, and leaving them OPAQUE would make a finer-level spec
    (correctly) refuse to decompose and raise a gap, turning a Success into a
    Partial and testing decomposition instead of the pruning under test.
    """
    sentence = CompositionNode(children=[Text(content="Deep.", format=TextFormat.PLAIN)])
    paragraph = CompositionNode(children=[Text(content="Mid.", format=TextFormat.PLAIN), sentence])
    return CompositionNode(children=[Text(content="Top.", format=TextFormat.PLAIN), paragraph])


def _texts(node: CompositionNode) -> list[str]:
    return [atom.content for atom in node.find_atoms(AtomKind.TEXT)]


class _ZoomIgnoring(BaseFetcher):
    """Emits a fixed deep tree, ignoring ``zoom`` like today's connectors."""

    def __init__(self, result: Optional[Result] = None) -> None:
        self._result = result if result is not None else success(_deep_tree())

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        yield self._result


async def test_fetch_prunes_even_when_the_connector_ignores_zoom() -> None:
    """WHOLE strips atoms below the root even for zoom-ignoring fetchers."""
    result = await _ZoomIgnoring().fetch("mem://x", zoom=WHOLE_TEXT)

    assert isinstance(result, Success)
    assert _texts(result.tree) == ["Top."]


async def test_no_spec_returns_the_natural_tree_unchanged() -> None:
    """Without a spec, fetch() output is byte-identical to 1.0 behavior."""
    natural = await _ZoomIgnoring().fetch("mem://x")

    assert isinstance(natural, Success)
    assert natural.tree.model_dump() == _deep_tree().model_dump()


async def test_pruned_fetch_is_deterministic() -> None:
    """Same input + spec yields an identical tree across runs."""
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SECTION})

    first = await _ZoomIgnoring().fetch("mem://x", zoom=spec)
    second = await _ZoomIgnoring().fetch("mem://x", zoom=spec)

    assert isinstance(first, Success) and isinstance(second, Success)
    assert first.tree.model_dump() == second.tree.model_dump()


async def test_partial_is_pruned_with_gaps_preserved() -> None:
    """A Partial's tree is pruned while its typed gaps are carried over."""
    holes = [gap(kind=ErrorKind.TRANSIENT, locator="mem://x", detail="hole")]
    fetcher = _ZoomIgnoring(partial(_deep_tree(), holes))

    result = await fetcher.fetch("mem://x", zoom=WHOLE_TEXT)

    assert isinstance(result, Partial)
    assert _texts(result.tree) == ["Top."]
    assert len(result.gaps) == 1 and result.gaps[0].kind == ErrorKind.TRANSIENT


async def test_error_results_pass_through_untouched() -> None:
    """Zoom never alters an Error result."""
    failure = error(kind=ErrorKind.NOT_FOUND, message="gone", locator="mem://x")
    fetcher = _ZoomIgnoring(failure)

    result = await fetcher.fetch("mem://x", zoom=WHOLE_TEXT)

    assert result is failure


async def test_orchestrator_stream_prunes_identically_to_fetch() -> None:
    """The orchestrator's streaming path applies the same pruning."""
    registry = FrozenRegistry(
        (SourceDefinition(name="deep", fetcher_class=_ZoomIgnoring, uri_patterns=("mem://*",)),)
    )
    omni = OmniFetcher(registry)

    fetched = await omni.fetch("mem://x", zoom=WHOLE_TEXT)
    streamed = [item async for item in omni.stream("mem://x", zoom=WHOLE_TEXT)]

    assert isinstance(fetched, Success)
    assert len(streamed) == 1 and isinstance(streamed[0], Success)
    assert fetched.tree.model_dump() == streamed[0].tree.model_dump()
    assert _texts(streamed[0].tree) == ["Top."]


async def test_real_connector_passes_through_the_central_path(
    tmp_path: Path,
) -> None:
    """A real connector fetch with a spec still succeeds end to end."""
    doc = tmp_path / "notes.md"
    doc.write_text("# Title\n\nzoom pass-through\n", encoding="utf-8")

    natural = await LocalFileFetcher().fetch(str(doc))
    zoomed = await LocalFileFetcher().fetch(str(doc), zoom=WHOLE_TEXT)

    assert isinstance(natural, Success) and isinstance(zoomed, Success)
    # local_file's natural tree is flat (atoms at the root), so WHOLE
    # changes nothing content-wise -- and must not break anything. The
    # per-fetch temporal stamp differs by design, so compare content.
    assert _texts(zoomed.tree) == _texts(natural.tree)
    assert zoomed.tree.metadata.kind == natural.tree.metadata.kind
    assert len(zoomed.tree.children) == len(natural.tree.children)


async def test_input_tree_is_never_mutated_by_the_central_path() -> None:
    """The fetcher's own tree object is left untouched by pruning."""
    tree = _deep_tree()
    before = tree.model_dump()
    fetcher = _ZoomIgnoring(success(tree))

    await fetcher.fetch("mem://x", zoom=WHOLE_TEXT)

    assert tree.model_dump() == before


# ---------------------------------------------------------------------------
# The other half: finer-than-natural decomposition, applied just as centrally
#
# These use fake connectors deliberately. After centralization a connector
# *cannot* ignore decomposition (it is applied above them), so sweeping the
# real registry would prove nothing a fake does not, while importing every
# optional dependency to do it. The vocabulary-level invariant (every format
# splits, answers whole, or gaps) is swept exhaustively in test_decompose.py.

PROSE = "First para. Still first.\n\nSecond para."
PAYLOAD = '{"id": 1, "bio": "Dr. Smith. Loves cats."}'


class _ProseZoomIgnoring(BaseFetcher):
    """A connector that takes ``zoom`` for conformance and never acts on it.

    The shape of most built-ins today (github, jira, slack, notion, ...).
    """

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        yield success(CompositionNode(children=[Text(content=PROSE, format=TextFormat.MARKDOWN)]))


class _OpaqueStream(BaseFetcher):
    """A kafka-shaped connector: OPAQUE payloads, more than one item."""

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        for _ in range(2):
            yield success(
                CompositionNode(children=[Text(content=PAYLOAD, format=TextFormat.OPAQUE)])
            )


@pytest.mark.parametrize(
    ("level", "expected"),
    [(DepthLevel.PARAGRAPH, 2), (DepthLevel.SENTENCE, 3)],
)
async def test_fetch_decomposes_even_when_the_connector_ignores_zoom(
    level: DepthLevel, expected: int
) -> None:
    """The bounded seam decomposes for a connector that never mentions zoom."""
    result = await _ProseZoomIgnoring().fetch(
        "mem://x", zoom=ZoomSpec(per_type={AtomKind.TEXT: level})
    )

    assert isinstance(result, Success)
    assert len(result.tree.find_atoms(AtomKind.TEXT)) == expected


async def test_central_decomposition_is_lossless() -> None:
    """Decomposed content still concatenates to the natural content."""
    natural = await _ProseZoomIgnoring().fetch("mem://x")
    zoomed = await _ProseZoomIgnoring().fetch(
        "mem://x", zoom=ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
    )

    assert "".join(_texts(zoomed.tree)) == "".join(_texts(natural.tree))


async def test_orchestrator_stream_decomposes_identically_to_fetch() -> None:
    """The streaming seam decomposes exactly as the bounded seam does."""
    registry = FrozenRegistry(
        (
            SourceDefinition(
                name="prose", fetcher_class=_ProseZoomIgnoring, uri_patterns=("mem://*",)
            ),
        )
    )
    omni = OmniFetcher(registry)
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})

    fetched = await omni.fetch("mem://x", zoom=spec)
    streamed = [item async for item in omni.stream("mem://x", zoom=spec)]

    assert len(streamed) == 1
    assert fetched.tree.model_dump() == streamed[0].tree.model_dump()


async def test_streaming_seam_never_shreds_opaque_payloads() -> None:
    """Centralization must not turn broker payloads into prose fragments.

    Every OPAQUE item survives whole with an honest gap -- the case the format
    dispatch exists to protect, at the seam where it would have done the most
    damage.
    """
    registry = FrozenRegistry(
        (SourceDefinition(name="ks", fetcher_class=_OpaqueStream, uri_patterns=("mem://*",)),)
    )
    omni = OmniFetcher(registry)
    spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})

    items = [item async for item in omni.stream("mem://t", zoom=spec)]

    assert len(items) == 2
    for item in items:
        assert isinstance(item, Partial) and item.gaps
        assert _texts(item.tree) == [PAYLOAD]


async def test_decomposition_runs_before_pruning() -> None:
    """Expand, then collapse: pruning must not eat decomposed structure.

    A SENTENCE spec decomposes the atom into sentence nodes one level down and
    prunes to that same budget, so the pieces survive. The reverse order would
    trim the structure decomposition was about to create.
    """
    result = await _ProseZoomIgnoring().fetch(
        "mem://x", zoom=ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.SENTENCE})
    )

    assert isinstance(result, Success)
    assert len(result.tree.find_by_kind("sentence")) == 3
    assert "".join(_texts(result.tree)) == PROSE
