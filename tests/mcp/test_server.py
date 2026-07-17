"""Behavioural tests for the OmniFetcher MCP server.

The server is driven in-process via ``FastMCP.call_tool`` against fake and
built-in registries -- no network, no live MCP client. Each test pins one
promise from the v1.7 MCP PRD's Testing Decisions.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

import pytest

from omni_fetcher.mcp.credentials import load_credentials
from omni_fetcher.mcp.server import build_server
from omni_fetcher.v1 import (
    BaseFetcher,
    RegistryBuilder,
    SourceDefinition,
    builtin_registry,
)
from omni_fetcher.v1.atoms import AtomKind, Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.result import Result, ResultAdapter, error, success

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes


class ProseConnector(BaseFetcher):
    """A bounded connector returning a fixed multi-paragraph prose tree."""

    BODY = "First para. Still first.\n\nSecond para. Also second."

    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: Any = None
    ) -> AsyncIterator[Result]:
        yield success(
            build_node(kind="doc", atoms=[Text(content=self.BODY, format=TextFormat.MARKDOWN)])
        )

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("prose://")


class AuthEchoConnector(BaseFetcher):
    """Records the ``auth`` it received; fails AUTH when given none."""

    received: list[Optional[AuthCredential]] = []

    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: Any = None
    ) -> AsyncIterator[Result]:
        type(self).received.append(auth)
        if auth is None:
            yield error(kind=ErrorKind.AUTH_FAILED, message="401 unauthorized", locator=uri)
        else:
            yield success(
                build_node(kind="doc", atoms=[Text(content="ok", format=TextFormat.PLAIN)])
            )

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("secret://")


class BigConnector(BaseFetcher):
    """Returns a tree of many sibling subtrees, for size-guard tests."""

    async def stream(
        self, uri: str, *, auth: Optional[AuthCredential] = None, zoom: Any = None
    ) -> AsyncIterator[Result]:
        children = [
            build_node(kind="chunk", atoms=[Text(content="x" * 500, format=TextFormat.PLAIN)])
            for _ in range(20)
        ]
        yield success(build_node(kind="doc", children=children))

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("big://")


def _fake_registry(name: str, cls: type[BaseFetcher], scheme: str):
    return (
        RegistryBuilder()
        .add(SourceDefinition(name=name, fetcher_class=cls, uri_patterns=(f"{scheme}://*",)))
        .build()
    )


def _payload(res: Any) -> dict:
    """Unwrap a ``call_tool`` return into the tool's JSON dict."""
    blocks = res[0] if isinstance(res, tuple) else res
    return json.loads(blocks[0].text)


# ---------------------------------------------------------------------------
# Tool surface


async def test_fetch_schema_has_no_credential_field() -> None:
    """The fetch tool exposes uri/zoom/tags and nothing credential-shaped."""
    server = build_server(_fake_registry("prose", ProseConnector, "prose"))

    tools = {t.name: t for t in await server.list_tools()}
    props = set((tools["fetch"].inputSchema or {}).get("properties", {}))

    assert props == {"uri", "zoom", "tags"}
    assert not any(k in p.lower() for p in props for k in ("cred", "token", "auth", "secret"))


async def test_only_fetch_and_list_sources_are_exposed() -> None:
    server = build_server(_fake_registry("prose", ProseConnector, "prose"))
    assert {t.name for t in await server.list_tools()} == {"fetch", "list_sources"}


# ---------------------------------------------------------------------------
# fetch: round-trip, zoom, errors


async def test_fetch_result_round_trips_through_result_adapter() -> None:
    server = build_server(_fake_registry("prose", ProseConnector, "prose"))

    data = _payload(await server.call_tool("fetch", {"uri": "prose://x"}))
    rebuilt = ResultAdapter.validate_python(data)

    assert rebuilt.state.value == "success"
    assert "".join(a.content for a in rebuilt.tree.find_atoms(AtomKind.TEXT)) == ProseConnector.BODY


async def test_fetch_forwards_zoom_as_a_spec() -> None:
    server = build_server(_fake_registry("prose", ProseConnector, "prose"))

    data = _payload(await server.call_tool("fetch", {"uri": "prose://x", "zoom": "text=paragraph"}))
    rebuilt = ResultAdapter.validate_python(data)

    assert len(rebuilt.tree.find_by_kind("paragraph")) == 2


async def test_bad_zoom_spec_returns_invalid_input() -> None:
    server = build_server(_fake_registry("prose", ProseConnector, "prose"))

    data = _payload(await server.call_tool("fetch", {"uri": "prose://x", "zoom": "text=nonsense"}))

    assert data["state"] == "error" and data["kind"] == "invalid_input"


async def test_unrouted_uri_returns_not_found() -> None:
    server = build_server(_fake_registry("prose", ProseConnector, "prose"))
    data = _payload(await server.call_tool("fetch", {"uri": "nope://x"}))
    assert data["state"] == "error" and data["kind"] == "not_found"


# ---------------------------------------------------------------------------
# Credentials


async def test_configured_credential_is_injected_per_call() -> None:
    AuthEchoConnector.received = []
    creds = load_credentials(frozenset({"secret"}), {"OMNI_FETCHER_SECRET_TOKEN": "tok"})
    server = build_server(_fake_registry("secret", AuthEchoConnector, "secret"), creds)

    data = _payload(await server.call_tool("fetch", {"uri": "secret://x"}))

    assert data["state"] == "success"
    injected = AuthEchoConnector.received[-1]
    assert getattr(injected, "token", None) == "tok"


async def test_unconfigured_source_returns_auth_failed_naming_env_var() -> None:
    AuthEchoConnector.received = []
    server = build_server(
        _fake_registry("secret", AuthEchoConnector, "secret"),
        load_credentials(frozenset({"secret"}), {}),
    )

    data = _payload(await server.call_tool("fetch", {"uri": "secret://x"}))

    assert data["state"] == "error" and data["kind"] == "auth_failed"
    assert "OMNI_FETCHER_SECRET_TOKEN" in data["message"]


async def test_credential_value_never_appears_in_output_or_logs(caplog) -> None:
    AuthEchoConnector.received = []
    creds = load_credentials(frozenset({"secret"}), {"OMNI_FETCHER_SECRET_TOKEN": "s3cr3t-value"})
    server = build_server(_fake_registry("secret", AuthEchoConnector, "secret"), creds)

    with caplog.at_level(logging.DEBUG):
        data = _payload(await server.call_tool("fetch", {"uri": "secret://x"}))

    assert "s3cr3t-value" not in json.dumps(data)
    assert "s3cr3t-value" not in caplog.text


async def test_credentials_do_not_bleed_between_sources() -> None:
    """Two sources with distinct creds each receive only their own."""
    AuthEchoConnector.received = []
    registry = (
        RegistryBuilder()
        .add(
            SourceDefinition(
                name="alpha", fetcher_class=AuthEchoConnector, uri_patterns=("secret://*",)
            )
        )
        .build()
    )
    # 'alpha' configured, a different source name is not.
    creds = load_credentials(frozenset({"alpha"}), {"OMNI_FETCHER_ALPHA_TOKEN": "alpha-tok"})
    server = build_server(registry, creds)

    _payload(await server.call_tool("fetch", {"uri": "secret://x"}))
    assert getattr(AuthEchoConnector.received[-1], "token", None) == "alpha-tok"


# ---------------------------------------------------------------------------
# Unbounded sources


@pytest.mark.parametrize(
    "uri",
    [
        "kafka://host/topic",
        "tail://host/log",
        "redis://host/stream",
        "sse://host/events",
        "ws://host/socket",
        "postgres-cdc://host/db",
    ],
)
async def test_unbounded_scheme_returns_unsupported_via_builtin(uri: str) -> None:
    server = build_server(builtin_registry())

    data = _payload(await server.call_tool("fetch", {"uri": uri}))

    assert data["state"] == "error" and data["kind"] == "unsupported"


# ---------------------------------------------------------------------------
# Discovery


async def test_list_sources_labels_bounded_and_unbounded() -> None:
    server = build_server(builtin_registry())

    data = _payload(await server.call_tool("list_sources", {}))
    by_name = {s["name"]: s for s in data["sources"]}

    assert by_name["local_file"]["bounded"] is True
    assert by_name["pdf"]["bounded"] is True
    for unbounded in ("kafka", "tail", "sse", "websocket", "redis", "postgres_cdc"):
        assert by_name[unbounded]["bounded"] is False
    # Each source carries its routing patterns.
    assert by_name["github"]["uri_patterns"]


# ---------------------------------------------------------------------------
# Size guard


async def test_size_guard_degrades_to_partial_with_a_gap() -> None:
    server = build_server(_fake_registry("big", BigConnector, "big"), max_bytes=2000)

    res = await server.call_tool("fetch", {"uri": "big://x"})
    data = _payload(res)

    assert data["state"] == "partial"
    assert any(g["kind"] == "unsupported" for g in data["gaps"])
    assert len(json.dumps(data).encode("utf-8")) <= 4000  # comfortably bounded


async def test_size_guard_leaves_small_results_untouched() -> None:
    server = build_server(_fake_registry("prose", ProseConnector, "prose"), max_bytes=1_000_000)

    data = _payload(await server.call_tool("fetch", {"uri": "prose://x"}))

    assert data["state"] == "success"


# ---------------------------------------------------------------------------
# Statelessness


async def test_interleaved_fetches_do_not_share_state() -> None:
    server = build_server(builtin_registry())

    a = _payload(await server.call_tool("fetch", {"uri": "kafka://x"}))
    b = _payload(await server.call_tool("list_sources", {}))
    c = _payload(await server.call_tool("fetch", {"uri": "kafka://x"}))

    assert a == c
    assert b["sources"]
