"""Tests for SlackFetcher."""

import pytest
from unittest.mock import patch

from omni_fetcher.fetchers.slack import (
    SlackFetcher,
    parse_slack_uri,
    convert_mrkdwn_to_markdown,
)
from omni_fetcher.schemas.slack import SlackMessage, SlackChannel, SlackDM
from omni_fetcher.schemas.atomics import TextDocument, TextFormat
from datetime import datetime


class TestSlackFetcherCreation:
    def test_creation(self):
        """Can create SlackFetcher."""
        fetcher = SlackFetcher()
        assert fetcher.name == "slack"
        assert fetcher.priority == 15


class TestSlackFetcherCanHandle:
    def test_can_handle_slack_urls(self):
        """SlackFetcher handles slack:// URIs."""
        assert SlackFetcher.can_handle("slack://channel/general")
        assert SlackFetcher.can_handle("slack://C01234ABCDEF")
        assert SlackFetcher.can_handle("slack://thread/C01234/1234567890.123456")
        assert SlackFetcher.can_handle("slack://dm/U01234ABCDEF")

    def test_cannot_handle_non_slack(self):
        """SlackFetcher rejects non-slack URLs."""
        assert not SlackFetcher.can_handle("https://github.com/owner/repo")
        assert not SlackFetcher.can_handle("https://example.com/page")
        assert not SlackFetcher.can_handle("")


class TestParseSlackUri:
    def test_parse_channel_by_name(self):
        """Parse channel by name."""
        route = parse_slack_uri("slack://channel/engineering-general")
        assert route.type == "channel"
        assert route.channel_name == "engineering-general"

    def test_parse_channel_by_id(self):
        """Parse channel by ID."""
        route = parse_slack_uri("slack://C01234ABCDEF")
        assert route.type == "channel"
        assert route.channel_id == "C01234ABCDEF"

    def test_parse_thread(self):
        """Parse thread URI."""
        route = parse_slack_uri("slack://thread/C01234ABCDEF/1234567890.123456")
        assert route.type == "thread"
        assert route.channel_id == "C01234ABCDEF"
        assert route.thread_ts == "1234567890.123456"

    def test_parse_dm(self):
        """Parse DM URI."""
        route = parse_slack_uri("slack://dm/U01234ABCDEF")
        assert route.type == "dm"
        assert route.user_id == "U01234ABCDEF"

    def test_parse_invalid(self):
        """Invalid URIs raise ValueError."""
        with pytest.raises(ValueError):
            parse_slack_uri("https://example.com/page")

        with pytest.raises(ValueError):
            parse_slack_uri("slack://")


class TestConvertMrkdwnToMarkdown:
    def test_bold(self):
        """Convert bold text."""
        result = convert_mrkdwn_to_markdown("*bold text*", {})
        assert "**bold text**" in result

    def test_italic(self):
        """Convert italic text."""
        result = convert_mrkdwn_to_markdown("_italic text_", {})
        assert "_italic text_" in result

    def test_code(self):
        """Convert inline code."""
        result = convert_mrkdwn_to_markdown("`code`", {})
        assert "`code`" in result

    def test_code_block(self):
        """Convert code block."""
        result = convert_mrkdwn_to_markdown("```python\ncode\n```", {})
        assert "```python" in result

    def test_user_mention(self):
        """Convert user mention."""
        user_map = {"U01234": "Jane Doe"}
        result = convert_mrkdwn_to_markdown("<@U01234>", user_map)
        assert "@Jane Doe" in result

    def test_channel_link(self):
        """Convert channel link."""
        result = convert_mrkdwn_to_markdown("<#C01234|general>", {})
        assert "#general" in result

    def test_at_here(self):
        """Convert @here."""
        result = convert_mrkdwn_to_markdown("<!here>", {})
        assert "@here" in result

    def test_at_channel(self):
        """Convert @channel."""
        result = convert_mrkdwn_to_markdown("<!channel>", {})
        assert "@channel" in result

    def test_url_link(self):
        """Convert URL link."""
        result = convert_mrkdwn_to_markdown("<https://example.com|Example>", {})
        assert "[Example](https://example.com)" in result


class TestSlackSchemas:
    def test_slack_message_tags(self):
        """SlackMessage has correct tags."""
        msg = SlackMessage(
            message_id="1234567890.123456",
            channel_id="C01234",
            author="Jane Doe",
            author_id="U01234",
            text=TextDocument(source_uri="", content="Hello", format=TextFormat.MARKDOWN),
            files=[],
            reactions=["thumbsup"],
            created_at=datetime.now(),
        )
        assert "slack" in msg.tags
        assert "message" in msg.tags

    def test_slack_channel_tags(self):
        """SlackChannel has correct tags."""
        channel = SlackChannel(
            channel_id="C01234",
            name="general",
            is_private=False,
            member_count=10,
            items=[],
        )
        assert "slack" in channel.tags
        assert "channel" in channel.tags
        assert "public" in channel.tags

    def test_slack_channel_private_tags(self):
        """SlackChannel private has correct tags."""
        channel = SlackChannel(
            channel_id="G01234",
            name="private-channel",
            is_private=True,
            member_count=5,
            items=[],
        )
        assert "private" in channel.tags

    def test_slack_dm_tags(self):
        """SlackDM has correct tags."""
        dm = SlackDM(
            dm_id="D01234",
            participants=["Jane"],
            participant_ids=["U01234"],
            items=[],
        )
        assert "slack" in dm.tags
        assert "dm" in dm.tags


class TestSlackFetcherIntegration:
    @pytest.mark.asyncio
    async def test_fetch_without_auth(self):
        """Test fetching without auth raises error."""
        fetcher = SlackFetcher()
        with patch.object(fetcher, "get_auth_headers", return_value={}):
            with pytest.raises(Exception):
                await fetcher.fetch("slack://channel/general")
