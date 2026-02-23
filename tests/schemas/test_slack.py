"""Tests for Slack schemas."""

from datetime import datetime

import pytest

from omni_fetcher.schemas.atomics import ImageDocument, TextDocument, TextFormat
from omni_fetcher.schemas.slack import (
    SlackChannel,
    SlackDM,
    SlackMessage,
    SlackThread,
)


class TestSlackMessageTags:
    def test_message_tags_include_source(self):
        """SlackMessage includes source tags."""
        msg = SlackMessage(
            message_id="1234567890.123456",
            channel_id="C01234",
            author="Jane Doe",
            text=TextDocument(source_uri="", content="Hello", format=TextFormat.MARKDOWN),
            created_at=datetime.now(),
        )
        assert "slack" in msg.tags
        assert "message" in msg.tags


class TestSlackChannelTags:
    def test_channel_tags_include_type_and_privacy(self):
        """SlackChannel includes type and privacy tags."""
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

    def test_private_channel_tags(self):
        """Private channel includes private tag."""
        channel = SlackChannel(
            channel_id="G01234",
            name="private",
            is_private=True,
            member_count=5,
            items=[],
        )
        assert "private" in channel.tags

    def test_channel_tags_merged_from_messages(self):
        """Channel tags are merged from messages."""
        msg = SlackMessage(
            message_id="1234567890.123456",
            channel_id="C01234",
            author="Jane",
            text=TextDocument(source_uri="", content="Hello", format=TextFormat.MARKDOWN),
            created_at=datetime.now(),
        )
        channel = SlackChannel(
            channel_id="C01234",
            name="general",
            is_private=False,
            member_count=10,
            items=[msg],
        )
        assert "message" in channel.tags


class TestSlackThreadTags:
    def test_thread_tags_include_source(self):
        """SlackThread includes source tags."""
        parent = SlackMessage(
            message_id="1234567890.123456",
            channel_id="C01234",
            author="Jane",
            text=TextDocument(source_uri="", content="Parent", format=TextFormat.MARKDOWN),
            created_at=datetime.now(),
        )
        thread = SlackThread(
            thread_ts="1234567890.123456",
            channel_id="C01234",
            parent=parent,
            replies=[],
            reply_count=0,
            participant_count=1,
        )
        assert "slack" in thread.tags
        assert "thread" in thread.tags


class TestSlackDMTags:
    def test_dm_tags_include_source(self):
        """SlackDM includes source tags."""
        dm = SlackDM(
            dm_id="D01234",
            participants=["Jane"],
            participant_ids=["U01234"],
            items=[],
        )
        assert "slack" in dm.tags
        assert "dm" in dm.tags


class TestSlackMessageSchema:
    def test_message_with_files(self):
        """Message with file attachments."""
        msg = SlackMessage(
            message_id="1234567890.123456",
            channel_id="C01234",
            author="Jane",
            text=TextDocument(source_uri="", content="Check this out", format=TextFormat.MARKDOWN),
            files=[
                ImageDocument(
                    source_uri="https://files.slack.com/files-pri/T01234/abc.png",
                    format="PNG",
                    width=100,
                    height=100,
                )
            ],
            reactions=["thumbsup", "rocket"],
            created_at=datetime.now(),
        )
        assert len(msg.files) == 1
        assert "thumbsup" in msg.reactions
        assert "rocket" in msg.reactions


class TestSlackChannelSchema:
    def test_channel_with_topic_and_purpose(self):
        """Channel with topic and purpose."""
        channel = SlackChannel(
            channel_id="C01234",
            name="engineering",
            topic=TextDocument(
                source_uri="", content="Engineering discussions", format=TextFormat.PLAIN
            ),
            purpose=TextDocument(
                source_uri="", content="For engineering team", format=TextFormat.PLAIN
            ),
            is_private=False,
            member_count=50,
            items=[],
        )
        assert channel.topic is not None
        assert channel.topic.content == "Engineering discussions"
        assert channel.purpose is not None


class TestSlackThreadSchema:
    def test_thread_with_replies(self):
        """Thread with replies."""
        parent = SlackMessage(
            message_id="1234567890.123456",
            channel_id="C01234",
            author="Jane",
            text=TextDocument(source_uri="", content="Parent message", format=TextFormat.MARKDOWN),
            created_at=datetime.now(),
        )
        reply = SlackMessage(
            message_id="1234567891.123456",
            channel_id="C01234",
            author="John",
            text=TextDocument(source_uri="", content="Reply message", format=TextFormat.MARKDOWN),
            created_at=datetime.now(),
        )
        thread = SlackThread(
            thread_ts="1234567890.123456",
            channel_id="C01234",
            parent=parent,
            replies=[reply],
            reply_count=1,
            participant_count=2,
        )
        assert len(thread.replies) == 1
        assert thread.reply_count == 1
        assert thread.participant_count == 2


class TestSlackDMSchema:
    def test_dm_with_participants(self):
        """DM with participants."""
        dm = SlackDM(
            dm_id="D01234",
            participants=["Jane Doe", "John Smith"],
            participant_ids=["U01234", "U05678"],
            items=[],
        )
        assert len(dm.participants) == 2
        assert len(dm.participant_ids) == 2
