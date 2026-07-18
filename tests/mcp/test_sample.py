"""Tests for the MCP ``sample`` tool: a bounded window over an unbounded stream.

Driven in-process via ``FastMCP.call_tool`` against fake streaming connectors,
so timeout, cancellation, and cleanup are exercised deterministically without a
broker. The cancellation/cleanup contract (the hard part per the MCP PRD) is
pinned by asserting each connector's ``stream`` finally-block ran.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Optional

import pytest

# Skip the whole module when the optional ``mcp`` extra is absent (D12).
pytest.importorskip("mcp")

from omni_fetcher.mcp.server import build_server  # noqa: E402
from omni_fetcher.v1 import (  # noqa: E402
    BaseFetcher,
    RegistryBuilder,
    SourceDefinition,
)
from omni_fetcher.v1.atoms import Text, TextFormat  # noqa: E402
from omni_fetcher.v1.auth import AuthCredential  # noqa: E402
from omni_fetcher.v1.errors import ErrorKind  # noqa: E402
from omni_fetcher.v1.mapping import build_node  # noqa: E402
from omni_fetcher.v1.result import Result, error, success  # noqa: E402

pytestmark = pytest.mark.asyncio


def _payload(res: Any) -> dict:
    blocks = res[0] if isinstance(res, tuple) else res
    return json.loads(blocks[0].text)


def _msg(text: str) -> Result:
    return success(build_node(kind="message", atoms=[Text(content=text, format=TextFormat.OPAQUE)]))


class InfiniteConnector(BaseFetcher):
    """Yields forever; records that its stream was finalised."""

    closed = False

    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: Any = None
    ) -> AsyncIterator[Result]:
        index = 0
        try:
            while True:
                yield _msg(f"item {index}")
                index += 1
                await asyncio.sleep(0)
        finally:
            type(self).closed = True

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("inf://")


class SlowConnector(BaseFetcher):
    """Yields one item every 0.2s; records finalisation."""

    closed = False

    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: Any = None
    ) -> AsyncIterator[Result]:
        index = 0
        try:
            while True:
                await asyncio.sleep(0.2)
                yield _msg(f"slow {index}")
                index += 1
        finally:
            type(self).closed = True

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("slow://")


class IdleConnector(BaseFetcher):
    """Never yields within any sane window; records finalisation."""

    closed = False

    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: Any = None
    ) -> AsyncIterator[Result]:
        try:
            await asyncio.sleep(100)
            yield _msg("never")
        finally:
            type(self).closed = True

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("idle://")


class BoundedishConnector(BaseFetcher):
    """Yields exactly two items then ends (a bounded stream)."""

    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: Any = None
    ) -> AsyncIterator[Result]:
        yield _msg("only one")
        yield _msg("only two")

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("two://")


class ErrorMidConnector(BaseFetcher):
    """Two items, then a transient error, then a third that must not arrive."""

    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: Any = None
    ) -> AsyncIterator[Result]:
        yield _msg("a")
        yield _msg("b")
        yield error(kind=ErrorKind.TRANSIENT, message="broker dropped", locator=uri)
        yield _msg("c-should-not-appear")

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("errmid://")


class ProseStreamConnector(BaseFetcher):
    """One item of multi-sentence prose, for zoom passthrough."""

    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: Any = None
    ) -> AsyncIterator[Result]:
        yield success(
            build_node(
                kind="message",
                atoms=[Text(content="One. Two. Three.", format=TextFormat.PLAIN)],
            )
        )

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("prose://")


class AuthEchoStream(BaseFetcher):
    received: list[Optional[AuthCredential]] = []

    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: Any = None
    ) -> AsyncIterator[Result]:
        type(self).received.append(auth)
        yield _msg("authed")

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("auth://")


def _server(name: str, cls: type[BaseFetcher], scheme: str, **kwargs):
    registry = (
        RegistryBuilder()
        .add(SourceDefinition(name=name, fetcher_class=cls, uri_patterns=(f"{scheme}://*",)))
        .build()
    )
    return build_server(registry, **kwargs)


def _sample_extra(data: dict) -> dict:
    return data["tree"]["metadata"]["source_extra"]["sample"]


# ---------------------------------------------------------------------------
# Stopping conditions


async def test_sample_takes_exactly_max_items() -> None:
    InfiniteConnector.closed = False
    server = _server("inf", InfiniteConnector, "inf")

    data = _payload(await server.call_tool("sample", {"uri": "inf://x", "max_items": 5}))

    assert data["state"] == "success"
    assert len(data["tree"]["children"]) == 5
    assert _sample_extra(data)["stopped"] == "max_items"
    assert InfiniteConnector.closed is True


async def test_sample_stops_at_timeout_with_a_partial_window() -> None:
    SlowConnector.closed = False
    server = _server("slow", SlowConnector, "slow")

    data = _payload(
        await server.call_tool(
            "sample", {"uri": "slow://x", "max_items": 100, "timeout_seconds": 0.5}
        )
    )

    assert data["state"] == "success"
    count = len(data["tree"]["children"])
    assert 1 <= count <= 3  # ~2 items in 0.5s at 0.2s each
    assert _sample_extra(data)["stopped"] == "timeout"
    assert SlowConnector.closed is True


async def test_idle_stream_yields_transient_and_closes() -> None:
    IdleConnector.closed = False
    server = _server("idle", IdleConnector, "idle")

    data = _payload(await server.call_tool("sample", {"uri": "idle://x", "timeout_seconds": 0.3}))

    assert data["state"] == "error" and data["kind"] == "transient"
    assert "0 items" in data["message"]
    assert IdleConnector.closed is True


async def test_sample_of_a_bounded_stream_stops_at_stream_end() -> None:
    server = _server("two", BoundedishConnector, "two")

    data = _payload(await server.call_tool("sample", {"uri": "two://x", "max_items": 10}))

    assert data["state"] == "success"
    assert len(data["tree"]["children"]) == 2
    assert _sample_extra(data)["stopped"] == "stream_end"


# ---------------------------------------------------------------------------
# Errors are folded, never dropped


async def test_error_item_folds_to_a_gap_and_stops_the_sample() -> None:
    server = _server("errmid", ErrorMidConnector, "errmid")

    data = _payload(await server.call_tool("sample", {"uri": "errmid://x", "max_items": 10}))

    assert data["state"] == "partial"
    assert len(data["tree"]["children"]) == 2  # the third item never arrives
    assert any(g["kind"] == "transient" for g in data["gaps"])
    assert _sample_extra(data)["stopped"] == "error"


# ---------------------------------------------------------------------------
# Metadata, zoom, credentials, validation


async def test_sample_records_its_stop_metadata() -> None:
    InfiniteConnector.closed = False
    server = _server("inf", InfiniteConnector, "inf")

    data = _payload(
        await server.call_tool("sample", {"uri": "inf://x", "max_items": 3, "timeout_seconds": 9.0})
    )

    extra = _sample_extra(data)
    assert extra == {"count": 3, "max_items": 3, "timeout_seconds": 9.0, "stopped": "max_items"}


async def test_sample_applies_zoom_to_each_item() -> None:
    server = _server("prose", ProseStreamConnector, "prose")

    data = _payload(await server.call_tool("sample", {"uri": "prose://x", "zoom": "text=sentence"}))

    # The single prose item decomposes into sentence child nodes under it.
    def count_kind(node: dict, kind: str) -> int:
        total = 1 if node.get("metadata", {}).get("kind") == kind else 0
        for child in node.get("children", []):
            if "children" in child:
                total += count_kind(child, kind)
        return total

    assert count_kind(data["tree"], "sentence") == 3


async def test_sample_injects_the_configured_credential() -> None:
    from omni_fetcher.mcp.credentials import load_credentials

    AuthEchoStream.received = []
    creds = load_credentials(frozenset({"auth"}), {"OMNI_FETCHER_AUTH_TOKEN": "tok"})
    registry = (
        RegistryBuilder()
        .add(
            SourceDefinition(name="auth", fetcher_class=AuthEchoStream, uri_patterns=("auth://*",))
        )
        .build()
    )
    server = build_server(registry, creds)

    _payload(await server.call_tool("sample", {"uri": "auth://x"}))

    assert getattr(AuthEchoStream.received[-1], "token", None) == "tok"


async def test_sample_rejects_non_positive_max_items() -> None:
    server = _server("inf", InfiniteConnector, "inf")

    data = _payload(await server.call_tool("sample", {"uri": "inf://x", "max_items": 0}))

    assert data["state"] == "error" and data["kind"] == "invalid_input"


async def test_sample_rejects_a_bad_zoom_spec() -> None:
    server = _server("inf", InfiniteConnector, "inf")

    data = _payload(await server.call_tool("sample", {"uri": "inf://x", "zoom": "text=nope"}))

    assert data["state"] == "error" and data["kind"] == "invalid_input"


async def test_sample_size_guard_bounds_the_window() -> None:
    """A large window still respects --max-bytes, degrading to a partial."""
    InfiniteConnector.closed = False
    server = _server("inf", InfiniteConnector, "inf", max_bytes=1500)

    data = _payload(await server.call_tool("sample", {"uri": "inf://x", "max_items": 50}))

    assert data["state"] in ("partial", "success")
    assert len(json.dumps(data).encode("utf-8")) <= 3000
