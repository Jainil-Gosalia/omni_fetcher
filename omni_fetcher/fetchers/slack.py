"""Slack fetcher for OmniFetcher."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import httpx

from omni_fetcher.core.exceptions import FetchError, SourceNotFoundError
from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.atomics import TextDocument, TextFormat, ImageDocument
from omni_fetcher.schemas.slack import SlackMessage, SlackChannel, SlackThread, SlackDM


SLACK_API_BASE = "https://slack.com/api"

SLACK_MRKDWN_TO_MARKDOWN: dict[str, str] = {
    r"\*([^*]+)\*": r"**\1**",
    r"_([^_]+)_": r"_\1_",
    r"`([^`]+)`": r"`\1`",
    r"```([^`]+)```": r"```\1```",
}


@dataclass
class SlackRoute:
    """Parsed Slack URI route."""

    type: str
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    thread_ts: Optional[str] = None
    user_id: Optional[str] = None


def parse_slack_uri(uri: str) -> SlackRoute:
    """Parse a Slack URI into a route."""
    if not uri.startswith("slack://"):
        raise ValueError(f"Invalid Slack URI: {uri}")

    path = uri[8:].strip("/")
    parts = path.split("/")

    if len(parts) == 0:
        raise ValueError(f"Invalid Slack URI: {uri}")

    route_type = parts[0]

    if route_type == "channel":
        if len(parts) < 2:
            raise ValueError(f"Invalid Slack channel URI: {uri}")
        return SlackRoute(type="channel", channel_name=parts[1])

    if route_type == "thread":
        if len(parts) < 3:
            raise ValueError(f"Invalid Slack thread URI: {uri}")
        return SlackRoute(type="thread", channel_id=parts[1], thread_ts=parts[2])

    if route_type == "dm":
        if len(parts) < 2:
            raise ValueError(f"Invalid Slack DM URI: {uri}")
        return SlackRoute(type="dm", user_id=parts[1])

    if len(parts) == 1:
        channel_id = parts[0]
        if channel_id.startswith("C") or channel_id.startswith("G"):
            return SlackRoute(type="channel", channel_id=channel_id)

    if len(parts) == 2:
        return SlackRoute(type="thread", channel_id=parts[0], thread_ts=parts[1])

    raise ValueError(f"Invalid Slack URI: {uri}")


def convert_mrkdwn_to_markdown(text: str, user_map: dict[str, str]) -> str:
    """Convert Slack mrkdwn format to markdown."""
    if not text:
        return ""

    result = text

    result = re.sub(r"\*([^*]+)\*", r"**\1**", result)
    result = re.sub(r"_([^_]+)_", r"_\1_", result)
    result = re.sub(r"`([^`]+)`", r"`\1`", result)
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

    result = re.sub(
        r"<([^|>]+)\|([^>]+)>",
        r"[\2](\1)",
        result,
    )

    return result


@source(
    name="slack",
    uri_patterns=["slack://"],
    priority=15,
    description="Fetch from Slack — channels, threads, DMs, attachments",
    auth={"type": "bearer", "token_env": "SLACK_BOT_TOKEN"},
)
class SlackFetcher(BaseFetcher):
    """Slack fetcher for OmniFetcher."""

    name = "slack"
    priority = 15

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._user_cache: dict[str, str] = {}

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return uri.startswith("slack://")

    def _get_headers(self) -> dict[str, str]:
        auth_headers = self.get_auth_headers()
        headers = {
            **auth_headers,
            "Content-Type": "application/json; charset=utf-8",
        }

        if "Authorization" not in headers:
            token = os.environ.get("SLACK_BOT_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"

        return headers

    async def _get_channel_id(self, channel_name: str, client: httpx.AsyncClient) -> str:
        if channel_name.startswith("C") or channel_name.startswith("G"):
            return channel_name

        response = await client.get(
            f"{SLACK_API_BASE}/conversations.list",
            params={"types": "public_channel,private_channel", "limit": 200},
            headers=self._get_headers(),
        )
        data = response.json()

        if not data.get("ok"):
            raise FetchError(f"slack://channel/{channel_name}", data.get("error", "Unknown error"))

        for channel in data.get("channels", []):
            if channel.get("name") == channel_name:
                return channel["id"]

        raise SourceNotFoundError(f"Channel not found: {channel_name}")

    async def _resolve_user(self, user_id: str, client: httpx.AsyncClient) -> str:
        if user_id in self._user_cache:
            return self._user_cache[user_id]

        response = await client.get(
            f"{SLACK_API_BASE}/users.info",
            params={"user": user_id},
        )
        data = response.json()

        if data.get("ok"):
            user = data.get("user", {})
            display_name = (
                user.get("profile", {}).get("display_name") or user.get("real_name") or user_id
            )
            self._user_cache[user_id] = display_name
            return display_name

        return user_id

    def _parse_file(
        self, file_data: dict[str, Any], channel_id: str
    ) -> ImageDocument | TextDocument:
        mime_type = file_data.get("mimetype", "")
        url_private = file_data.get("url_private", "")

        if mime_type.startswith("image/"):
            return ImageDocument(
                source_uri=url_private,
                format=mime_type.replace("image/", "").upper(),
                width=file_data.get("original_w"),
                height=file_data.get("original_h"),
                alt_text=file_data.get("title", ""),
            )

        return TextDocument(
            source_uri=url_private,
            content=file_data.get("plain_text", ""),
            format=TextFormat.PLAIN,
            char_count=file_data.get("size", 0),
        )

    def _build_message(
        self,
        msg: dict[str, Any],
        channel_id: str,
        user_map: dict[str, str],
    ) -> SlackMessage:
        ts = msg.get("ts", "")
        text = msg.get("text", "")
        user_id = msg.get("user", "")

        markdown_text = convert_mrkdwn_to_markdown(text, user_map)

        files = []
        for f in msg.get("files", []):
            files.append(self._parse_file(f, channel_id))

        reactions = [r.get("name", "") for r in msg.get("reactions", [])]

        thread_ts = msg.get("thread_ts")
        if thread_ts and thread_ts != ts:
            reply_count = msg.get("reply_count", 0)
        else:
            reply_count = 0
            thread_ts = None

        created_at = datetime.fromtimestamp(float(ts))

        return SlackMessage(
            message_id=ts,
            channel_id=channel_id,
            author=user_map.get(user_id),
            author_id=user_id or None,
            text=TextDocument(
                source_uri="",
                content=markdown_text,
                format=TextFormat.MARKDOWN,
                char_count=len(markdown_text),
            ),
            files=files,
            reactions=reactions,
            thread_ts=thread_ts,
            reply_count=reply_count,
            created_at=created_at,
            url=msg.get("permalink"),
        )

    async def _fetch_channel(
        self,
        route: SlackRoute,
        uri: str,
        limit: int = 100,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        include_threads: bool = False,
        include_files: bool = True,
        resolve_users: bool = True,
        **kwargs: Any,
    ) -> SlackChannel:
        async with httpx.AsyncClient(timeout=30.0) as client:
            channel_id: Optional[str] = route.channel_id
            if not channel_id and route.channel_name:
                channel_id = await self._get_channel_id(route.channel_name, client)

            if not channel_id:
                raise SourceNotFoundError(f"Could not resolve channel: {uri}")

            channel_info_response = await client.get(
                f"{SLACK_API_BASE}/conversations.info",
                params={"channel": channel_id},
                headers=self._get_headers(),
            )
            channel_info = channel_info_response.json()

            if not channel_info.get("ok"):
                raise FetchError(uri, channel_info.get("error", "Unknown error"))

            channel = channel_info.get("channel", {})
            channel_name = channel.get("name", "unknown")
            is_private = channel.get("is_private", False)
            member_count = channel.get("num_members", 0)
            topic_text = channel.get("topic", {}).get("value", "")
            purpose_text = channel.get("purpose", {}).get("value", "")

            params: dict[str, Any] = {
                "channel": channel_id,
                "limit": limit,
            }
            if oldest:
                params["oldest"] = oldest
            if latest:
                params["latest"] = latest

            all_messages = []
            cursor = None

            while True:
                if cursor:
                    params["cursor"] = cursor

                response = await client.get(
                    f"{SLACK_API_BASE}/conversations.history",
                    params=params,
                    headers=self._get_headers(),
                )
                data = response.json()

                if not data.get("ok"):
                    raise FetchError(uri, data.get("error", "Unknown error"))

                messages = data.get("messages", [])
                all_messages.extend(messages)

                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

                if len(all_messages) >= limit:
                    break

            if resolve_users:
                user_ids = set()
                for msg in all_messages:
                    if msg.get("user"):
                        user_ids.add(msg["user"])

                for uid in user_ids:
                    await self._resolve_user(uid, client)

            user_map = dict(self._user_cache)

            slack_messages = []
            for msg in all_messages:
                if msg.get("subtype") == "bot_message":
                    continue
                slack_messages.append(self._build_message(msg, channel_id, user_map))

            topic_doc = None
            if topic_text:
                topic_doc = TextDocument(
                    source_uri="",
                    content=topic_text,
                    format=TextFormat.PLAIN,
                )

            purpose_doc = None
            if purpose_text:
                purpose_doc = TextDocument(
                    source_uri="",
                    content=purpose_text,
                    format=TextFormat.PLAIN,
                )

            return SlackChannel(
                source_uri=uri,
                source_name=self.name,
                channel_id=channel_id,
                name=channel_name,
                topic=topic_doc,
                purpose=purpose_doc,
                is_private=is_private,
                member_count=member_count,
                items=slack_messages,
                item_count=len(slack_messages),
                fetched_fully=len(all_messages) < limit,
                next_page_token=cursor,
            )

    async def _fetch_thread(
        self,
        route: SlackRoute,
        uri: str,
        limit: int = 100,
        include_files: bool = True,
        resolve_users: bool = True,
        **kwargs: Any,
    ) -> SlackThread:
        if not route.channel_id or not route.thread_ts:
            raise SourceNotFoundError(f"Invalid thread URI: {uri}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{SLACK_API_BASE}/conversations.replies",
                params={
                    "channel": route.channel_id,
                    "ts": route.thread_ts,
                    "limit": limit,
                },
                headers=self._get_headers(),
            )
            data = response.json()

            if not data.get("ok"):
                raise FetchError(uri, data.get("error", "Unknown error"))

            messages = data.get("messages", [])

            if not messages:
                raise SourceNotFoundError(f"Thread not found: {uri}")

            if resolve_users:
                user_ids = set()
                for msg in messages:
                    if msg.get("user"):
                        user_ids.add(msg["user"])

                for uid in user_ids:
                    await self._resolve_user(uid, client)

            user_map = dict(self._user_cache)

            slack_messages = []
            for msg in messages:
                if msg.get("subtype") == "bot_message":
                    continue
                slack_messages.append(self._build_message(msg, route.channel_id, user_map))

            parent = slack_messages[0] if slack_messages else None
            replies = slack_messages[1:] if len(slack_messages) > 1 else []

            if not parent:
                raise SourceNotFoundError(f"Thread not found: {uri}")

            participant_ids = set()
            for msg in slack_messages:
                if msg.author_id:
                    participant_ids.add(msg.author_id)

            return SlackThread(
                thread_ts=route.thread_ts,
                channel_id=route.channel_id,
                parent=parent,
                replies=replies,
                reply_count=len(replies),
                participant_count=len(participant_ids),
            )

    async def _fetch_dm(
        self,
        route: SlackRoute,
        uri: str,
        limit: int = 100,
        include_files: bool = True,
        resolve_users: bool = True,
        **kwargs: Any,
    ) -> SlackDM:
        if not route.user_id:
            raise SourceNotFoundError(f"Invalid DM URI: {uri}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{SLACK_API_BASE}/conversations.list",
                params={"types": "im", "limit": 200},
                headers=self._get_headers(),
            )
            data = response.json()

            if not data.get("ok"):
                raise FetchError(uri, data.get("error", "Unknown error"))

            dm_id = None
            for channel in data.get("channels", []):
                if channel.get("user") == route.user_id:
                    dm_id = channel["id"]
                    break

            if not dm_id:
                raise SourceNotFoundError(f"DM not found for user: {route.user_id}")

            messages_response = await client.get(
                f"{SLACK_API_BASE}/conversations.history",
                params={"channel": dm_id, "limit": limit},
                headers=self._get_headers(),
            )
            messages_data = messages_response.json()

            if not messages_data.get("ok"):
                raise FetchError(uri, messages_data.get("error", "Unknown error"))

            messages = messages_data.get("messages", [])

            if resolve_users:
                await self._resolve_user(route.user_id, client)

                me_response = await client.get(
                    f"{SLACK_API_BASE}/auth.identities",
                    headers=self._get_headers(),
                )
                me_data = me_response.json()
                if me_data.get("ok"):
                    self_user = me_data.get("user", {})
                    await self._resolve_user(self_user.get("user_id", ""), client)

            user_map = dict(self._user_cache)

            slack_messages = []
            for msg in messages:
                if msg.get("subtype") == "bot_message":
                    continue
                slack_messages.append(self._build_message(msg, dm_id, user_map))

            participants = [user_map.get(route.user_id, route.user_id)]

            return SlackDM(
                source_uri=uri,
                source_name=self.name,
                dm_id=dm_id,
                participants=participants,
                participant_ids=[route.user_id],
                items=slack_messages,
                item_count=len(slack_messages),
                fetched_fully=True,
                next_page_token=None,
            )

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        route = parse_slack_uri(uri)

        if route.type == "channel":
            return await self._fetch_channel(route, uri, **kwargs)
        elif route.type == "thread":
            return await self._fetch_thread(route, uri, **kwargs)
        elif route.type == "dm":
            return await self._fetch_dm(route, uri, **kwargs)

        raise SourceNotFoundError(f"Unsupported Slack URI: {uri}")
