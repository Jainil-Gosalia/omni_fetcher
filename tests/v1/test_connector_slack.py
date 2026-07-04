"""External-behaviour tests for the v1 ``slack`` connector.

These tests exercise only the public surface of ``SlackConnector`` via
``fetch()`` / ``stream()`` (the inherited base sugar over ``stream()``). No
real network is used: every ``httpx.AsyncClient`` is forced onto an
``httpx.MockTransport`` whose handler dispatches per Slack Web API method.

What is asserted (behaviour, not internals):

- a single message maps onto a canonical ``"message"`` node whose only
  content is a ``Text`` atom and whose descriptive fields live in
  ``source_extra["slack"]`` -- never on the atom (there are no ``Slack*``
  output types);
- a channel and a thread map onto a container node whose children are
  ``"message"`` nodes;
- ``invalid_auth`` (Slack ``ok: false``) yields ``Error(AUTH_FAILED)`` and
  the other Slack/HTTP error strings map onto the taxonomy;
- the per-call bearer token is resolved into the outgoing request headers.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from omni_fetcher.v1.atoms import AtomKind, TextFormat
from omni_fetcher.v1.auth import BearerAuth
from omni_fetcher.v1.connectors.slack import SlackConnector
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.result import Error, Success

pytestmark = pytest.mark.asyncio


SlackHandler = Callable[[httpx.Request], httpx.Response]


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: SlackHandler,
) -> None:
    """Force every ``httpx.AsyncClient`` to use a mock transport."""
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _ok(payload: dict[str, Any]) -> httpx.Response:
    """Build an ``ok: true`` Slack JSON response."""
    body = {"ok": True, **payload}
    return httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=json.dumps(body).encode("utf-8"),
    )


def _not_ok(error_str: str) -> httpx.Response:
    """Build an ``ok: false`` Slack JSON response (HTTP 200, as Slack does)."""
    return httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=json.dumps({"ok": False, "error": error_str}).encode("utf-8"),
    )


def _method(request: httpx.Request) -> str:
    """Extract the Slack API method name from a request URL path."""
    return request.url.path.rsplit("/", 1)[-1]


def _user_info(uid: str, name: str) -> dict[str, Any]:
    """A ``users.info`` payload mapping ``uid`` to a display name."""
    return {"user": {"profile": {"display_name": name}}}


def _make_handler(routes: dict[str, httpx.Response]) -> SlackHandler:
    """Dispatch by Slack method name; ``users.info`` resolves any user."""

    def handler(request: httpx.Request) -> httpx.Response:
        method = _method(request)
        if method == "users.info":
            uid = request.url.params.get("user", "U?")
            return _ok(_user_info(uid, f"name-{uid}"))
        if method in routes:
            return routes[method]
        return _not_ok("unknown_method")

    return handler


async def test_channel_yields_container_of_message_nodes(monkeypatch):
    """A channel maps onto a ``"channel"`` container of ``"message"`` nodes."""
    routes = {
        "conversations.info": _ok(
            {
                "channel": {
                    "id": "C123",
                    "name": "general",
                    "is_private": False,
                    "num_members": 5,
                    "topic": {"value": "Topic here"},
                    "purpose": {"value": "Purpose here"},
                }
            }
        ),
        "conversations.history": _ok(
            {
                "messages": [
                    {"ts": "1700000000.0001", "user": "U1", "text": "*hi* there"},
                    {"ts": "1700000001.0002", "user": "U2", "text": "second"},
                ]
            }
        ),
    }
    _install_transport(monkeypatch, _make_handler(routes))

    result = await SlackConnector().fetch("slack://channel/C123", auth=BearerAuth(token="xoxb-tok"))

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "channel"

    message_nodes = [c for c in node.children if c.metadata.kind == "message"]
    assert len(message_nodes) == 2

    # Each message node carries its text only as a Text atom.
    first = message_nodes[0]
    text_atoms = first.find_atoms(AtomKind.TEXT)
    assert len(text_atoms) == 1
    assert text_atoms[0].format == TextFormat.MARKDOWN
    assert text_atoms[0].content == "**hi** there"  # mrkdwn -> markdown

    # Descriptive channel fields live under source_extra, not on atoms.
    extra = node.metadata.source_extra["slack"]
    assert extra["channel_id"] == "C123"
    assert extra["name"] == "general"
    assert extra["topic"] == "Topic here"
    assert extra["message_count"] == 2


async def test_message_descriptive_fields_in_source_extra_not_atom(monkeypatch):
    """Author / ts / channel / reactions are metadata, never on the atom."""
    routes = {
        "conversations.info": _ok({"channel": {"id": "C1", "name": "c"}}),
        "conversations.history": _ok(
            {
                "messages": [
                    {
                        "ts": "1700000000.0001",
                        "user": "U1",
                        "text": "hello",
                        "reactions": [{"name": "thumbsup"}],
                    }
                ]
            }
        ),
    }
    _install_transport(monkeypatch, _make_handler(routes))

    result = await SlackConnector().fetch("slack://channel/C1")

    assert isinstance(result, Success)
    msg = result.tree.children[0]
    assert msg.metadata.kind == "message"

    # Descriptive fields are in metadata core + source_extra["slack"].
    extra = msg.metadata.source_extra["slack"]
    assert extra["ts"] == "1700000000.0001"
    assert extra["channel_id"] == "C1"
    assert extra["user_id"] == "U1"
    assert extra["reactions"] == ["thumbsup"]
    assert msg.metadata.author == "name-U1"
    assert msg.metadata.id == "1700000000.0001"

    # The atom carries content only -- no descriptive leakage.
    atom = msg.find_atoms(AtomKind.TEXT)[0]
    dumped = atom.model_dump()
    assert set(dumped) <= {"kind", "content", "format", "language", "encoding"}


async def test_thread_yields_container_of_message_nodes(monkeypatch):
    """A thread maps onto a ``"thread"`` container of ``"message"`` nodes."""
    routes = {
        "conversations.replies": _ok(
            {
                "messages": [
                    {
                        "ts": "1700000000.0001",
                        "user": "U1",
                        "text": "parent",
                        "thread_ts": "1700000000.0001",
                        "reply_count": 1,
                    },
                    {
                        "ts": "1700000002.0003",
                        "user": "U2",
                        "text": "reply",
                        "thread_ts": "1700000000.0001",
                    },
                ]
            }
        ),
    }
    _install_transport(monkeypatch, _make_handler(routes))

    result = await SlackConnector().fetch(
        "slack://thread/C123/1700000000.0001",
        auth=BearerAuth(token="xoxb-tok"),
    )

    assert isinstance(result, Success)
    node = result.tree
    assert node.metadata.kind == "thread"

    message_nodes = [c for c in node.children if c.metadata.kind == "message"]
    assert len(message_nodes) == 2
    assert message_nodes[0].find_atoms(AtomKind.TEXT)[0].content == "parent"

    extra = node.metadata.source_extra["slack"]
    assert extra["channel_id"] == "C123"
    assert extra["thread_ts"] == "1700000000.0001"
    assert extra["reply_count"] == 1


async def test_invalid_auth_yields_auth_failed(monkeypatch):
    """A Slack ``invalid_auth`` (ok:false) yields a typed AUTH_FAILED error."""
    routes = {"conversations.info": _not_ok("invalid_auth")}
    _install_transport(monkeypatch, _make_handler(routes))

    result = await SlackConnector().fetch("slack://channel/C123")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.AUTH_FAILED


@pytest.mark.parametrize(
    "slack_error,expected",
    [
        ("not_authed", ErrorKind.AUTH_FAILED),
        ("invalid_auth", ErrorKind.AUTH_FAILED),
        ("missing_scope", ErrorKind.PERMISSION_DENIED),
        ("ratelimited", ErrorKind.RATE_LIMITED),
        ("not_found", ErrorKind.NOT_FOUND),
    ],
)
async def test_slack_error_maps_to_kind(monkeypatch, slack_error, expected):
    """Slack ``ok: false`` error strings map onto the error taxonomy."""
    routes = {"conversations.info": _not_ok(slack_error)}
    _install_transport(monkeypatch, _make_handler(routes))

    result = await SlackConnector().fetch("slack://channel/C123")

    assert isinstance(result, Error)
    assert result.kind == expected


async def test_http_404_maps_to_not_found(monkeypatch):
    """A raw HTTP 404 maps onto NOT_FOUND, not a raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=404, content=b"nope")

    _install_transport(monkeypatch, handler)

    result = await SlackConnector().fetch("slack://channel/C123")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.NOT_FOUND


async def test_invalid_uri_yields_invalid_input(monkeypatch):
    """An unroutable Slack URI yields INVALID_INPUT without any network."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made for an invalid URI")

    _install_transport(monkeypatch, handler)

    result = await SlackConnector().fetch("slack://")

    assert isinstance(result, Error)
    assert result.kind == ErrorKind.INVALID_INPUT


async def test_per_call_bearer_token_is_sent(monkeypatch):
    """The per-call bearer token is resolved into the request headers."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        method = _method(request)
        if method == "conversations.info":
            return _ok({"channel": {"id": "C1", "name": "c"}})
        if method == "conversations.history":
            return _ok({"messages": []})
        if method == "users.info":
            return _ok(_user_info("U1", "n"))
        return _not_ok("unknown_method")

    _install_transport(monkeypatch, handler)

    result = await SlackConnector().fetch(
        "slack://channel/C1", auth=BearerAuth(token="xoxb-secret")
    )

    assert isinstance(result, Success)
    assert captured["authorization"] == "Bearer xoxb-secret"


async def test_bot_messages_are_skipped(monkeypatch):
    """Bot messages are filtered out of the container's children."""
    routes = {
        "conversations.info": _ok({"channel": {"id": "C1", "name": "c"}}),
        "conversations.history": _ok(
            {
                "messages": [
                    {"ts": "1.0", "user": "U1", "text": "human"},
                    {"ts": "2.0", "subtype": "bot_message", "text": "bot"},
                ]
            }
        ),
    }
    _install_transport(monkeypatch, _make_handler(routes))

    result = await SlackConnector().fetch("slack://channel/C1")

    assert isinstance(result, Success)
    message_nodes = [c for c in result.tree.children if c.metadata.kind == "message"]
    assert len(message_nodes) == 1
    assert message_nodes[0].find_atoms(AtomKind.TEXT)[0].content == "human"
