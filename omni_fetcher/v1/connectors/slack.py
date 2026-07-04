"""The canonical ``slack`` connector for the OmniFetcher v1 contract.

Fetches Slack conversations over the Web API and maps them onto the canonical
contract. A channel or a thread is a container ``CompositionNode`` whose
children are per-message nodes; a single message is a leaf node. There are no
``Slack*`` output types -- the former ``SlackMessage`` / ``SlackThread`` /
``SlackChannel`` / ``SlackDM`` data is re-expressed as canonical atoms plus
metadata.

The content/description split is strict:

- Message text is the only content, so it becomes a ``Text`` atom
  (``TextFormat.MARKDOWN`` -- Slack ``mrkdwn`` is converted to Markdown).
- Everything that *describes* a message or container (author, Slack ``ts``,
  channel id, ``thread_ts``, reactions, reply counts, channel topic/purpose,
  ...) is descriptive metadata: it lives in the ``Metadata`` core plus the
  namespaced ``source_extra["slack"]`` mapping, never inline on an atom.

Auth is per call: a Slack bot token is passed as a ``BearerAuth`` credential
and resolved transiently into request headers. The connector reads no ambient
environment.

Expected failures are returned as typed ``Error`` results, never raised. Slack
API ``ok: false`` errors and HTTP statuses map onto the taxonomy: ``not_found``
-> ``NOT_FOUND``; ``not_authed`` / ``invalid_auth`` -> ``AUTH_FAILED``;
``missing_scope`` (and similar scope errors) -> ``PERMISSION_DENIED``;
``ratelimited`` -> ``RATE_LIMITED``; everything else -> ``TRANSIENT``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import httpx

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential, NormalizedAuthResolver
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import (
    SequenceCounter,
    build_node,
    stamp_temporal,
)
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import Error, Result, error, success
from omni_fetcher.v1.zoom import ZoomSpec

# The Slack Web API base URL.
SLACK_API_BASE = "https://slack.com/api"

# The source namespace under which this connector files its descriptive
# fields in ``Metadata.source_extra``.
SOURCE_NAMESPACE = "slack"

# Advisory semantic ``kind`` labels for the nodes this connector emits.
KIND_MESSAGE = "message"
KIND_THREAD = "thread"
KIND_CHANNEL = "channel"

# Default transport timeout (seconds) for a single request.
DEFAULT_TIMEOUT = 30.0

# Default and maximum number of messages collected for a container.
DEFAULT_LIMIT = 100

# Slack API ``error`` strings that map onto a permission failure.
_PERMISSION_ERRORS = frozenset(
    {
        "missing_scope",
        "not_allowed_token_type",
        "no_permission",
        "not_in_channel",
        "channel_not_found",
        "restricted_action",
    }
)

# Slack API ``error`` strings that map onto an auth failure.
_AUTH_ERRORS = frozenset(
    {
        "not_authed",
        "invalid_auth",
        "account_inactive",
        "token_revoked",
        "token_expired",
    }
)

# Slack API ``error`` strings that map onto a not-found failure.
_NOT_FOUND_ERRORS = frozenset(
    {
        "not_found",
        "thread_not_found",
        "message_not_found",
    }
)


def _slack_error_to_kind(slack_error: str) -> ErrorKind:
    """Map a Slack API ``error`` string onto a taxonomy ``ErrorKind``."""
    if slack_error in _AUTH_ERRORS:
        return ErrorKind.AUTH_FAILED
    if slack_error in _PERMISSION_ERRORS:
        return ErrorKind.PERMISSION_DENIED
    if slack_error == "ratelimited":
        return ErrorKind.RATE_LIMITED
    if slack_error in _NOT_FOUND_ERRORS:
        return ErrorKind.NOT_FOUND
    return ErrorKind.TRANSIENT


def _status_to_error_kind(status_code: int) -> ErrorKind:
    """Map an HTTP status code onto a taxonomy ``ErrorKind``."""
    if status_code == 401:
        return ErrorKind.AUTH_FAILED
    if status_code == 403:
        return ErrorKind.PERMISSION_DENIED
    if status_code == 404:
        return ErrorKind.NOT_FOUND
    if status_code == 429:
        return ErrorKind.RATE_LIMITED
    if 500 <= status_code <= 599:
        return ErrorKind.TRANSIENT
    return ErrorKind.INVALID_INPUT


def _convert_mrkdwn(text: str, user_map: dict[str, str]) -> str:
    """Convert Slack ``mrkdwn`` to Markdown (best-effort, deterministic)."""
    if not text:
        return ""

    result = text
    result = re.sub(r"\*([^*]+)\*", r"**\1**", result)
    result = re.sub(r"```([^`]+)```", r"```\1```", result)
    result = re.sub(r"<\|([^*]+)\|>", r"\1", result)
    result = re.sub(
        r"<@(U[0-9A-Z]+)\|?([^>]*)>",
        lambda m: f"@{m.group(2) or user_map.get(m.group(1), m.group(1))}",
        result,
    )
    result = re.sub(
        r"<#(C[0-9A-Z]+)\|?([^>]*)>",
        lambda m: f"#{m.group(2) or m.group(1)}",
        result,
    )
    result = re.sub(r"<!here>", "@here", result)
    result = re.sub(r"<!channel>", "@channel", result)
    result = re.sub(r"<!everyone>", "@everyone", result)
    result = re.sub(r"<([^|>]+)\|([^>]+)>", r"[\2](\1)", result)
    return result


def _ts_to_datetime(ts: str) -> Optional[datetime]:
    """Convert a Slack ``ts`` (epoch seconds string) to a UTC datetime."""
    if not ts:
        return None
    try:
        seconds = float(ts)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


@dataclass(frozen=True)
class SlackRoute:
    """Parsed Slack URI route (internal value, never an output type)."""

    type: str
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    thread_ts: Optional[str] = None
    user_id: Optional[str] = None


def parse_slack_uri(uri: str) -> Optional[SlackRoute]:
    """Parse a ``slack://`` URI into a route, or ``None`` if invalid.

    Returns ``None`` for any URI the connector cannot route; the caller turns
    that into an ``INVALID_INPUT`` error rather than raising.
    """
    if not uri.startswith("slack://"):
        return None

    path = uri[len("slack://") :].strip("/")
    if not path:
        return None
    parts = path.split("/")
    route_type = parts[0]

    if route_type == "channel":
        if len(parts) < 2:
            return None
        name = parts[1]
        if name.startswith("C") or name.startswith("G"):
            return SlackRoute(type="channel", channel_id=name)
        return SlackRoute(type="channel", channel_name=name)

    if route_type == "thread":
        if len(parts) < 3:
            return None
        return SlackRoute(
            type="thread",
            channel_id=parts[1],
            thread_ts=parts[2],
        )

    if route_type == "dm":
        if len(parts) < 2:
            return None
        return SlackRoute(type="dm", user_id=parts[1])

    if len(parts) == 1:
        ident = parts[0]
        if ident.startswith("C") or ident.startswith("G"):
            return SlackRoute(type="channel", channel_id=ident)
        return None

    if len(parts) == 2:
        return SlackRoute(
            type="thread",
            channel_id=parts[0],
            thread_ts=parts[1],
        )

    return None


class SlackConnector(BaseFetcher):
    """
    Canonical Slack connector
    ===============================================
    Fetches Slack channels, threads and DMs over the Web API and streams them
    as canonical ``CompositionNode`` trees. A channel/thread/DM is a container
    node (advisory ``kind`` ``"channel"``/``"thread"``) whose children are
    per-message nodes (``kind`` ``"message"``); message text becomes a
    ``Text`` atom and all descriptive fields (author, ``ts``, channel,
    ``thread_ts``, reactions, ...) live in the ``Metadata`` core plus
    ``source_extra["slack"]``. Expected failures (Slack ``ok: false``, HTTP
    status, network errors) are returned as typed ``Error`` results, never
    raised.
    ===============================================
    NOTE:
        1. This connector implements only ``stream()``; ``fetch()`` is the
           inherited base sugar that collects the bounded stream.
        2. Credentials are passed per call via ``auth`` (a Slack bot token as
           a ``BearerAuth``) and resolved transiently into request headers;
           nothing -- token or user cache -- is stored on the instance across
           calls. The connector reads no ambient environment.
        3. Output is deterministic and read-only.

    Attributes
    ----------
        timeout:
            Per-request transport timeout in seconds.
        limit:
            Maximum number of messages collected for a container resource.

    Methods
    -------
        can_handle:
        stream:
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        """
        Create a Slack connector

        Parameters
        ----------
            timeout:
                Per-request transport timeout in seconds.
            limit:
                Maximum number of messages collected for a container.
        """
        self.timeout = timeout
        self.limit = limit
        self._auth_resolver = NormalizedAuthResolver()

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether a URI is a Slack resource URI

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` when ``uri`` begins with ``slack://``.
        """
        return uri.startswith("slack://")

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream a Slack resource as one canonical result

        Routes the ``slack://`` URI to a channel, thread, or DM fetch and
        yields exactly one ``Result``. A channel/thread/DM yields a
        ``Success`` whose tree is a container ``CompositionNode`` with one
        message node per Slack message; a single unparseable or unsupported
        URI yields a typed ``Error``. Slack ``ok: false`` errors, HTTP error
        statuses, and network failures all yield typed ``Error`` results.

        NOTE:
            1. Expected failures are yielded as ``Error`` results, never
               raised.
            2. ``zoom`` is accepted for contract conformance. This connector
               emits messages at their natural granularity (one ``Text`` atom
               per message under a container) and does not further decompose
               them, so ``zoom`` does not change the output.

        Parameters
        ----------
            uri:
                The ``slack://`` resource URI.
            auth:
                The per-call credential (a Slack bot token as a
                ``BearerAuth``), or ``None``. Resolved transiently into
                request headers.
            zoom:
                Optional per-atom-type zoom spec; accepted but not acted on.

        Return
        ------
            results:
                An async iterator yielding exactly one ``Result``.
        """
        route = parse_slack_uri(uri)
        if route is None:
            yield error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"unroutable Slack URI: {uri}",
                locator=uri,
            )
            return

        headers = self._auth_resolver.resolve_headers(auth)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if route.type == "channel":
                    result = await self._fetch_channel(client, route, uri, headers)
                elif route.type == "thread":
                    result = await self._fetch_thread(client, route, uri, headers)
                else:  # route.type == "dm"
                    result = await self._fetch_dm(client, route, uri, headers)
        except httpx.HTTPError as exc:
            yield error(
                kind=ErrorKind.TRANSIENT,
                message=f"request failed: {exc}",
                locator=uri,
            )
            return

        yield result

    async def _call(
        self,
        client: httpx.AsyncClient,
        method: str,
        params: dict[str, Any],
        headers: dict[str, str],
        uri: str,
    ) -> tuple[Optional[dict[str, Any]], Optional[Error]]:
        """Call a Slack Web API method; return (data, error) -- one is None."""
        response = await client.get(
            f"{SLACK_API_BASE}/{method}",
            params=params,
            headers=headers,
        )
        if response.status_code >= 400:
            return None, error(
                kind=_status_to_error_kind(response.status_code),
                message=f"HTTP {response.status_code} from {method}",
                locator=uri,
            )
        try:
            data = response.json()
        except (ValueError, UnicodeError) as exc:
            return None, error(
                kind=ErrorKind.PARSE_ERROR,
                message=f"{method} body is not valid JSON: {exc}",
                locator=uri,
            )
        if not data.get("ok"):
            slack_error = data.get("error", "unknown_error")
            return None, error(
                kind=_slack_error_to_kind(slack_error),
                message=f"Slack API error from {method}: {slack_error}",
                locator=uri,
            )
        return data, None

    async def _resolve_users(
        self,
        client: httpx.AsyncClient,
        user_ids: set[str],
        headers: dict[str, str],
    ) -> dict[str, str]:
        """Resolve user ids to display names (best-effort, never raises)."""
        user_map: dict[str, str] = {}
        for uid in sorted(user_ids):
            if not uid:
                continue
            response = await client.get(
                f"{SLACK_API_BASE}/users.info",
                params={"user": uid},
                headers=headers,
            )
            if response.status_code >= 400:
                continue
            try:
                data = response.json()
            except (ValueError, UnicodeError):
                continue
            if not data.get("ok"):
                continue
            user = data.get("user", {})
            profile = user.get("profile", {})
            display = profile.get("display_name") or user.get("real_name") or uid
            user_map[uid] = display
        return user_map

    def _build_message_node(
        self,
        msg: dict[str, Any],
        channel_id: str,
        user_map: dict[str, str],
        uri: str,
        counter: SequenceCounter,
    ) -> CompositionNode:
        """Build a canonical ``"message"`` node for one Slack message."""
        ts = msg.get("ts", "")
        raw_text = msg.get("text", "")
        user_id = msg.get("user", "") or ""
        markdown = _convert_mrkdwn(raw_text, user_map)

        reactions = [r.get("name", "") for r in msg.get("reactions", [])]
        thread_ts = msg.get("thread_ts")
        reply_count = msg.get("reply_count", 0) if thread_ts else 0
        created = _ts_to_datetime(ts)
        author = user_map.get(user_id) if user_id else None

        source_fields: dict[str, Any] = {
            "ts": ts,
            "channel_id": channel_id,
            "user_id": user_id or None,
            "thread_ts": thread_ts,
            "reply_count": reply_count,
            "reactions": reactions,
            "subtype": msg.get("subtype"),
            "permalink": msg.get("permalink"),
        }

        node = build_node(
            kind=KIND_MESSAGE,
            atoms=[Text(content=markdown, format=TextFormat.MARKDOWN)],
            id=ts or None,
            created=created,
            author=author,
            source_url=msg.get("permalink") or uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        return stamp_temporal(node, sequence=counter.next(), timestamp=created)

    def _build_message_nodes(
        self,
        messages: list[dict[str, Any]],
        channel_id: str,
        user_map: dict[str, str],
        uri: str,
        counter: SequenceCounter,
    ) -> list[CompositionNode]:
        """Build message nodes, skipping bot messages (deterministic order)."""
        nodes: list[CompositionNode] = []
        for msg in messages:
            if msg.get("subtype") == "bot_message":
                continue
            nodes.append(self._build_message_node(msg, channel_id, user_map, uri, counter))
        return nodes

    async def _collect_users(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        headers: dict[str, str],
    ) -> dict[str, str]:
        """Resolve every author id referenced by ``messages``."""
        user_ids = {msg["user"] for msg in messages if msg.get("user")}
        if not user_ids:
            return {}
        return await self._resolve_users(client, user_ids, headers)

    async def _resolve_channel_id(
        self,
        client: httpx.AsyncClient,
        route: SlackRoute,
        headers: dict[str, str],
        uri: str,
    ) -> tuple[Optional[str], Optional[Error]]:
        """Resolve a channel name to an id; ids pass through unchanged."""
        if route.channel_id:
            return route.channel_id, None
        if not route.channel_name:
            return None, error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"channel URI has neither id nor name: {uri}",
                locator=uri,
            )

        data, err = await self._call(
            client,
            "conversations.list",
            {"types": "public_channel,private_channel", "limit": 200},
            headers,
            uri,
        )
        if err is not None:
            return None, err
        # _call returns exactly one of (data, err) non-None; narrow for
        # the type checker.
        assert data is not None
        for channel in data.get("channels", []):
            if channel.get("name") == route.channel_name:
                return channel["id"], None
        return None, error(
            kind=ErrorKind.NOT_FOUND,
            message=f"channel not found: {route.channel_name}",
            locator=uri,
        )

    async def _fetch_channel(
        self,
        client: httpx.AsyncClient,
        route: SlackRoute,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch a channel as a container node of message children."""
        channel_id, err = await self._resolve_channel_id(client, route, headers, uri)
        if err is not None:
            return err
        assert channel_id is not None

        info, err = await self._call(
            client,
            "conversations.info",
            {"channel": channel_id},
            headers,
            uri,
        )
        if err is not None:
            return err
        assert info is not None

        channel = info.get("channel", {})

        history, err = await self._call(
            client,
            "conversations.history",
            {"channel": channel_id, "limit": self.limit},
            headers,
            uri,
        )
        if err is not None:
            return err
        assert history is not None

        messages = history.get("messages", [])
        user_map = await self._collect_users(client, messages, headers)
        counter = SequenceCounter()
        children = self._build_message_nodes(messages, channel_id, user_map, uri, counter)

        source_fields: dict[str, Any] = {
            "channel_id": channel_id,
            "name": channel.get("name"),
            "is_private": channel.get("is_private", False),
            "member_count": channel.get("num_members"),
            "topic": channel.get("topic", {}).get("value", ""),
            "purpose": channel.get("purpose", {}).get("value", ""),
            "message_count": len(children),
        }

        node = build_node(
            kind=KIND_CHANNEL,
            children=children,
            id=channel_id,
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        return success(node)

    async def _fetch_thread(
        self,
        client: httpx.AsyncClient,
        route: SlackRoute,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch a thread as a container node of message children."""
        if not route.channel_id or not route.thread_ts:
            return error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"thread URI missing channel or ts: {uri}",
                locator=uri,
            )

        data, err = await self._call(
            client,
            "conversations.replies",
            {
                "channel": route.channel_id,
                "ts": route.thread_ts,
                "limit": self.limit,
            },
            headers,
            uri,
        )
        if err is not None:
            return err
        assert data is not None

        messages = data.get("messages", [])
        if not messages:
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"thread not found: {uri}",
                locator=uri,
            )

        user_map = await self._collect_users(client, messages, headers)
        counter = SequenceCounter()
        children = self._build_message_nodes(messages, route.channel_id, user_map, uri, counter)

        participant_ids = sorted({msg["user"] for msg in messages if msg.get("user")})
        source_fields: dict[str, Any] = {
            "channel_id": route.channel_id,
            "thread_ts": route.thread_ts,
            "reply_count": max(len(children) - 1, 0),
            "participant_count": len(participant_ids),
            "message_count": len(children),
        }

        node = build_node(
            kind=KIND_THREAD,
            children=children,
            id=route.thread_ts,
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        return success(node)

    async def _fetch_dm(
        self,
        client: httpx.AsyncClient,
        route: SlackRoute,
        uri: str,
        headers: dict[str, str],
    ) -> Result:
        """Fetch a DM as a container node of message children."""
        if not route.user_id:
            return error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"DM URI missing user id: {uri}",
                locator=uri,
            )

        listing, err = await self._call(
            client,
            "conversations.list",
            {"types": "im", "limit": 200},
            headers,
            uri,
        )
        if err is not None:
            return err
        assert listing is not None

        dm_id: Optional[str] = None
        for channel in listing.get("channels", []):
            if channel.get("user") == route.user_id:
                dm_id = channel["id"]
                break
        if dm_id is None:
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"DM not found for user: {route.user_id}",
                locator=uri,
            )

        history, err = await self._call(
            client,
            "conversations.history",
            {"channel": dm_id, "limit": self.limit},
            headers,
            uri,
        )
        if err is not None:
            return err
        assert history is not None

        messages = history.get("messages", [])
        user_map = await self._collect_users(client, messages, headers)
        counter = SequenceCounter()
        children = self._build_message_nodes(messages, dm_id, user_map, uri, counter)

        source_fields: dict[str, Any] = {
            "dm_id": dm_id,
            "user_id": route.user_id,
            "message_count": len(children),
        }

        node = build_node(
            kind=KIND_CHANNEL,
            children=children,
            id=dm_id,
            source_url=uri,
            source_namespace=SOURCE_NAMESPACE,
            source_fields=source_fields,
        )
        return success(node)
