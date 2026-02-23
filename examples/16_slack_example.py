"""Slack connector example for OmniFetcher."""

import asyncio
import os
from datetime import datetime

from omni_fetcher import OmniFetcher
from omni_fetcher.schemas.slack import (
    SlackChannel,
    SlackDM,
    SlackMessage,
    SlackThread,
)
from omni_fetcher.schemas.atomics import TextDocument, TextFormat


async def main():
    print("=" * 60)
    print("Slack Connector Examples")
    print("=" * 60)

    token = os.environ.get("SLACK_BOT_TOKEN")
    if token:
        fetcher = OmniFetcher(auth={"slack": {"type": "bearer", "token_env": "SLACK_BOT_TOKEN"}})
        print("Using SLACK_BOT_TOKEN for authenticated requests")
    else:
        print("No SLACK_BOT_TOKEN found - examples will show schema structure only")
        fetcher = OmniFetcher()

    print("\n1. Fetch channel by name")
    print("-" * 40)

    try:
        channel = await fetcher.fetch("slack://channel/engineering-general")
        print(f"Channel Name: {channel.name}")
        print(f"Channel ID: {channel.channel_id}")
        print(f"Is Private: {channel.is_private}")
        print(f"Member Count: {channel.member_count}")
        if channel.topic:
            print(f"Topic: {channel.topic.content}")
        if channel.purpose:
            print(f"Purpose: {channel.purpose.content}")
        print(f"Messages: {channel.item_count}")
        print(f"Tags: {channel.tags}")
        print(f"Fetched Fully: {channel.fetched_fully}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n2. Fetch channel by ID")
    print("-" * 40)

    try:
        channel = await fetcher.fetch("slack://C01234ABCDEF")
        print(f"Channel Name: {channel.name}")
        print(f"Channel ID: {channel.channel_id}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n3. Fetch channel with date range")
    print("-" * 40)

    try:
        channel = await fetcher.fetch(
            "slack://channel/engineering-general",
            oldest="1700000000",
            latest="1710000000",
            limit=500,
        )
        print(f"Messages in range: {channel.item_count}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n4. Fetch thread")
    print("-" * 40)

    try:
        thread = await fetcher.fetch("slack://thread/C01234ABCDEF/1234567890.123456")
        print(f"Thread TS: {thread.thread_ts}")
        print(f"Channel ID: {thread.channel_id}")
        print(f"Reply Count: {thread.reply_count}")
        print(f"Participant Count: {thread.participant_count}")
        print(f"Tags: {thread.tags}")
        if thread.parent:
            print("\nParent Message:")
            print(f"  Author: {thread.parent.author}")
            print(f"  Created: {thread.parent.created_at}")
            print(f"  Text: {thread.parent.text.content[:100]}...")
        print(f"\nReplies: {len(thread.replies)}")
        for reply in thread.replies[:3]:
            print(f"  - {reply.author}: {reply.text.content[:50]}...")
    except Exception as e:
        print(f"Error: {e}")

    print("\n5. Fetch DM")
    print("-" * 40)

    try:
        dm = await fetcher.fetch("slack://dm/U01234ABCDEF")
        print(f"DM ID: {dm.dm_id}")
        print(f"Participants: {dm.participants}")
        print(f"Messages: {dm.item_count}")
        print(f"Tags: {dm.tags}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n6. Accessing message details")
    print("-" * 40)

    try:
        channel = await fetcher.fetch("slack://channel/engineering-general")
        if channel.items:
            msg = channel.items[0]
            print(f"Message ID: {msg.message_id}")
            print(f"Author: {msg.author}")
            print(f"Author ID: {msg.author_id}")
            print(f"Created At: {msg.created_at}")
            print(f"Text Content: {msg.text.content}")
            print(f"Text Format: {msg.text.format}")
            print(f"Reactions: {msg.reactions}")
            print(f"Reply Count: {msg.reply_count}")
            print(f"URL: {msg.url}")
            print(f"Tags: {msg.tags}")
            if msg.files:
                print(f"Files: {len(msg.files)} attachments")
    except Exception as e:
        print(f"Error: {e}")

    print("\n7. Schema examples (without API calls)")
    print("-" * 40)

    slack_msg = SlackMessage(
        message_id="1234567890.123456",
        channel_id="C01234ABCDEF",
        author="Jane Doe",
        author_id="U01234ABCDEF",
        text=TextDocument(
            source_uri="slack://channel/general/p/1234567890",
            content="Hello, **world**!",
            format=TextFormat.MARKDOWN,
            char_count=14,
        ),
        files=[],
        reactions=["thumbsup", "rocket"],
        thread_ts=None,
        reply_count=0,
        created_at=datetime.now(),
        url="https://workspace.slack.com/archives/C01234ABCDEF/p1234567890",
    )
    print(f"SlackMessage tags: {slack_msg.tags}")

    thread_parent = SlackMessage(
        message_id="1234567890.123456",
        channel_id="C01234ABCDEF",
        author="John Smith",
        author_id="U05678ABCDEF",
        text=TextDocument(
            source_uri="",
            content="We need to discuss the architecture",
            format=TextFormat.PLAIN,
        ),
        files=[],
        reactions=["eyes"],
        thread_ts="1234567890.123456",
        reply_count=5,
        created_at=datetime.now(),
        url=None,
    )
    thread_reply = SlackMessage(
        message_id="1234567891.123456",
        channel_id="C01234ABCDEF",
        author="Jane Doe",
        author_id="U01234ABCDEF",
        text=TextDocument(
            source_uri="",
            content="I agree, let's schedule a meeting",
            format=TextFormat.PLAIN,
        ),
        files=[],
        reactions=[],
        thread_ts="1234567890.123456",
        reply_count=0,
        created_at=datetime.now(),
        url=None,
    )
    slack_thread = SlackThread(
        thread_ts="1234567890.123456",
        channel_id="C01234ABCDEF",
        parent=thread_parent,
        replies=[thread_reply],
        reply_count=1,
        participant_count=2,
    )
    print(f"SlackThread tags: {slack_thread.tags}")

    channel_topic = TextDocument(
        source_uri="",
        content="Engineering team discussions",
        format=TextFormat.PLAIN,
    )
    channel_purpose = TextDocument(
        source_uri="",
        content="For all engineering-related conversations",
        format=TextFormat.PLAIN,
    )
    slack_channel = SlackChannel(
        source_uri="slack://channel/engineering",
        source_name="slack",
        channel_id="C01234ABCDEF",
        name="engineering",
        topic=channel_topic,
        purpose=channel_purpose,
        is_private=False,
        member_count=50,
        items=[slack_msg],
        item_count=1,
    )
    print(f"SlackChannel tags: {slack_channel.tags}")

    slack_dm = SlackDM(
        source_uri="slack://dm/U01234ABCDEF",
        source_name="slack",
        dm_id="D01234ABCDEF",
        participants=["Jane Doe", "John Smith"],
        participant_ids=["U01234ABCDEF", "U05678ABCDEF"],
        items=[slack_msg],
        item_count=1,
    )
    print(f"SlackDM tags: {slack_dm.tags}")

    print("\n" + "=" * 60)
    print("Slack Connector supports:")
    print("  - Channels (by name or ID)")
    print("  - Threads (with replies)")
    print("  - Direct Messages (DMs)")
    print("  - Date range filtering (oldest/latest)")
    print("  - Pagination (limit)")
    print("  - Bearer token auth via SLACK_BOT_TOKEN")
    print("")
    print("Required bot scopes:")
    print("  channels:read, channels:history")
    print("  groups:read, groups:history")
    print("  im:read, im:history")
    print("  users:read, files:read")
    print("")
    print("Install: pip install omni_fetcher[slack]")


if __name__ == "__main__":
    asyncio.run(main())
