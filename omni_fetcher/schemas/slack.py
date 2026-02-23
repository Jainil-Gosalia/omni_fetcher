"""Slack schemas for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from typing_extensions import Self

from pydantic import BaseModel, Field, model_validator

from omni_fetcher.schemas.atomics import TextDocument, ImageDocument
from omni_fetcher.schemas.base import DataCategory, MediaType


class SlackMessage(BaseModel):
    """Slack message representation."""

    message_id: str = Field(..., description="Slack timestamp (ts) used as ID")
    channel_id: str = Field(..., description="Channel ID containing the message")
    author: Optional[str] = Field(None, description="Display name resolved from user ID")
    author_id: Optional[str] = Field(None, description="Raw Slack user ID")
    text: TextDocument = Field(..., description="Message text as markdown")
    files: list[ImageDocument | TextDocument] = Field(
        default_factory=list, description="File attachments"
    )
    reactions: list[str] = Field(default_factory=list, description="Emoji reaction names")
    thread_ts: Optional[str] = Field(None, description="Thread timestamp if part of a thread")
    reply_count: int = Field(0, description="Number of replies in thread")
    created_at: datetime = Field(..., description="Message creation timestamp")
    url: Optional[str] = Field(None, description="Permalink to message")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["slack", "message"]
        self.tags = tags


class SlackThread(BaseModel):
    """Slack thread representation."""

    thread_ts: str = Field(..., description="Thread timestamp")
    channel_id: str = Field(..., description="Channel ID containing the thread")
    parent: SlackMessage = Field(..., description="The message that started the thread")
    replies: list[SlackMessage] = Field(default_factory=list, description="All replies in thread")
    reply_count: int = Field(0, description="Number of replies")
    participant_count: int = Field(0, description="Number of unique participants")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["slack", "thread"])
        if self.parent and self.parent.tags:
            all_tags.update(self.parent.tags)
        for reply in self.replies:
            if reply and reply.tags:
                all_tags.update(reply.tags)
        self.tags = sorted(all_tags)
        return self


class SlackChannel(BaseModel):
    """Slack channel representation."""

    source_uri: str = Field(default="", description="Original URI of the channel")
    fetched_at: datetime = Field(
        default_factory=datetime.now, description="Timestamp when channel was fetched"
    )
    source_name: Optional[str] = Field(None, description="Name of the source handler used")
    item_count: int = Field(0, ge=0, description="Total number of messages in channel")
    fetched_fully: bool = Field(
        False, description="Whether all messages were fetched or this is a partial result"
    )
    next_page_token: Optional[str] = Field(None, description="Token for fetching next page")
    tags: list[str] = Field(default_factory=list, description="Tags from all messages in channel")

    channel_id: str = Field(..., description="Channel ID")
    name: str = Field(..., description="Channel name")
    topic: Optional[TextDocument] = Field(None, description="Channel topic")
    purpose: Optional[TextDocument] = Field(None, description="Channel purpose")
    is_private: bool = Field(False, description="Whether channel is private")
    member_count: int = Field(0, description="Number of members in channel")
    items: list[SlackMessage] = Field(default_factory=list, description="Messages in channel")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["slack", "channel"])
        if self.is_private:
            all_tags.add("private")
        else:
            all_tags.add("public")
        for item in self.items:
            if item and item.tags:
                all_tags.update(item.tags)
        self.tags = sorted(all_tags)
        return self


class SlackDM(BaseModel):
    """Slack DM (direct message) representation."""

    source_uri: str = Field(default="", description="Original URI of the DM")
    fetched_at: datetime = Field(
        default_factory=datetime.now, description="Timestamp when DM was fetched"
    )
    source_name: Optional[str] = Field(None, description="Name of the source handler used")
    item_count: int = Field(0, ge=0, description="Total number of messages in DM")
    fetched_fully: bool = Field(
        False, description="Whether all messages were fetched or this is a partial result"
    )
    next_page_token: Optional[str] = Field(None, description="Token for fetching next page")
    tags: list[str] = Field(default_factory=list, description="Tags from all messages in DM")

    dm_id: str = Field(..., description="DM conversation ID")
    participants: list[str] = Field(
        default_factory=list, description="Display names of participants"
    )
    participant_ids: list[str] = Field(default_factory=list, description="Raw user IDs")
    items: list[SlackMessage] = Field(default_factory=list, description="Messages in DM")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["slack", "dm"])
        for item in self.items:
            if item and item.tags:
                all_tags.update(item.tags)
        self.tags = sorted(all_tags)
        return self
