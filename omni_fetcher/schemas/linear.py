"""Linear schemas for OmniFetcher."""

from __future__ import annotations

from datetime import datetime, date
from typing import Any, Optional
from typing_extensions import Self

from pydantic import BaseModel, Field, model_validator

from omni_fetcher.schemas.base import DataCategory, MediaType
from omni_fetcher.schemas.atomics import TextDocument
from omni_fetcher.schemas.containers import BaseContainer


def priority_to_slug(priority: int) -> str:
    """Convert priority integer to slug format."""
    mapping = {
        0: "no_priority",
        1: "urgent",
        2: "high",
        3: "medium",
        4: "low",
    }
    return mapping.get(priority, "unknown")


def state_type_to_slug(state_type: str) -> str:
    """Convert state type to slug format."""
    valid = {"triage", "backlog", "unstarted", "started", "completed", "cancelled"}
    if state_type.lower() in valid:
        return state_type.lower()
    return "unknown"


class LinearIssue(BaseModel):
    """Linear issue representation."""

    issue_id: str = Field(..., description="Linear issue UUID")
    identifier: str = Field(..., description="Issue identifier (e.g. ENG-123)")
    title: str = Field(..., description="Issue title")
    description: Optional[TextDocument] = Field(
        None, description="Issue description as TextDocument"
    )
    state: str = Field(..., description="Issue state (e.g. In Progress, Done)")
    state_type: str = Field(
        ...,
        description="Issue state type (triage, backlog, unstarted, started, completed, cancelled)",
    )
    priority: int = Field(..., description="Priority (0=none, 1=urgent, 2=high, 3=medium, 4=low)")
    priority_label: str = Field(
        ..., description="Priority label (No priority, Urgent, High, Medium, Low)"
    )
    assignee: Optional[str] = Field(None, description="Assignee display name")
    creator: Optional[str] = Field(None, description="Creator display name")
    team: str = Field(..., description="Team name")
    team_key: str = Field(..., description="Team key (e.g. ENG)")
    project: Optional[str] = Field(None, description="Project name")
    cycle: Optional[str] = Field(None, description="Cycle name")
    labels: list[str] = Field(default_factory=list, description="Issue labels")
    comments: list[TextDocument] = Field(default_factory=list, description="Issue comments")
    parent_id: Optional[str] = Field(None, description="Parent issue ID if sub-issue")
    estimate: Optional[float] = Field(None, description="Story points estimate")
    due_date: Optional[date] = Field(None, description="Due date")
    completed_at: Optional[datetime] = Field(None, description="Completion timestamp")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    url: str = Field(..., description="Issue URL")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["linear", "issue", state_type_to_slug(self.state_type)]
        tags.append(priority_to_slug(self.priority))
        tags.extend([label.lower() for label in self.labels])
        self.tags = tags


class LinearTeam(BaseContainer[LinearIssue]):
    """Linear team representation."""

    team_id: str = Field(..., description="Team UUID")
    key: str = Field(..., description="Team key (e.g. ENG)")
    name: str = Field(..., description="Team name")
    description: Optional[TextDocument] = Field(
        None, description="Team description as TextDocument"
    )

    items: list[LinearIssue] = Field(default_factory=list, description="Issues in team")
    issue_count: int = Field(0, ge=0, description="Total issue count")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["linear", "team"])
        for item in self.items:
            if item and getattr(item, "tags", None):
                all_tags.update(item.tags)
        self.tags = sorted(all_tags)
        return self


class LinearProject(BaseContainer[LinearIssue]):
    """Linear project representation."""

    project_id: str = Field(..., description="Project UUID")
    name: str = Field(..., description="Project name")
    description: Optional[TextDocument] = Field(
        None, description="Project description as TextDocument"
    )
    state: str = Field(
        ..., description="Project state (planned, started, paused, completed, cancelled)"
    )
    progress: float = Field(0.0, ge=0.0, le=1.0, description="Project progress (0.0 to 1.0)")
    lead: Optional[str] = Field(None, description="Project lead name")
    target_date: Optional[date] = Field(None, description="Target completion date")

    items: list[LinearIssue] = Field(default_factory=list, description="Issues in project")
    issue_count: int = Field(0, ge=0, description="Total issue count")
    completed_count: int = Field(0, ge=0, description="Completed issue count")
    url: str = Field(..., description="Project URL")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["linear", "project", self.state])
        for item in self.items:
            if item and getattr(item, "tags", None):
                all_tags.update(item.tags)
        self.tags = sorted(all_tags)
        return self


class LinearCycle(BaseContainer[LinearIssue]):
    """Linear cycle representation."""

    cycle_id: str = Field(..., description="Cycle UUID")
    number: int = Field(..., description="Cycle number")
    name: Optional[str] = Field(None, description="Cycle name")
    team: str = Field(..., description="Team name")
    state: str = Field(..., description="Cycle state (active, completed, future)")
    progress: float = Field(0.0, ge=0.0, le=1.0, description="Cycle progress (0.0 to 1.0)")

    items: list[LinearIssue] = Field(default_factory=list, description="Issues in cycle")
    starts_at: datetime = Field(..., description="Cycle start timestamp")
    ends_at: datetime = Field(..., description="Cycle end timestamp")
    completed_at: Optional[datetime] = Field(None, description="Cycle completion timestamp")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["linear", "cycle", self.state])
        for item in self.items:
            if item and getattr(item, "tags", None):
                all_tags.update(item.tags)
        self.tags = sorted(all_tags)
        return self
