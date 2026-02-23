"""Jira schemas for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from typing_extensions import Self

from pydantic import BaseModel, Field, model_validator

from omni_fetcher.schemas.base import DataCategory, MediaType
from omni_fetcher.schemas.atomics import TextDocument
from omni_fetcher.schemas.containers import BaseContainer


def status_to_slug(status: str) -> str:
    """Convert status to slug format: 'In Progress' -> 'in_progress'."""
    return status.lower().replace(" ", "_").replace("-", "_")


class JiraIssue(BaseModel):
    """Jira issue representation."""

    issue_key: str = Field(..., description="Jira issue key (e.g. ENG-123)")
    issue_id: str = Field(..., description="Internal Jira issue ID")
    issue_type: str = Field(..., description="Issue type (Bug, Story, Epic, Task, Subtask)")
    title: str = Field(..., description="Issue summary/title")
    description: Optional[TextDocument] = Field(
        None, description="Issue description as TextDocument"
    )
    comments: list[TextDocument] = Field(
        default_factory=list, description="Issue comments as TextDocuments"
    )
    status: str = Field(..., description="Issue status")
    priority: Optional[str] = Field(None, description="Issue priority")
    assignee: Optional[str] = Field(None, description="Assignee display name")
    reporter: Optional[str] = Field(None, description="Reporter display name")
    labels: list[str] = Field(default_factory=list, description="Issue labels")
    components: list[str] = Field(default_factory=list, description="Issue components")
    fix_versions: list[str] = Field(default_factory=list, description="Fix versions")
    sprint: Optional[str] = Field(None, description="Sprint name")
    epic_key: Optional[str] = Field(None, description="Parent epic key")
    epic_name: Optional[str] = Field(None, description="Parent epic name")
    story_points: Optional[float] = Field(None, description="Story points")
    created_at: datetime = Field(..., description="Issue creation timestamp")
    updated_at: datetime = Field(..., description="Issue last update timestamp")
    resolved_at: Optional[datetime] = Field(None, description="Issue resolution timestamp")
    url: str = Field(..., description="Issue URL")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["jira", "issue", self.issue_type.lower(), status_to_slug(self.status)]
        tags.extend([label.lower() for label in self.labels])
        self.tags = tags


class JiraEpic(BaseModel):
    """Jira epic representation."""

    epic_key: str = Field(..., description="Epic key (e.g. ENG-123)")
    epic_id: str = Field(..., description="Internal Jira epic ID")
    name: str = Field(..., description="Epic name")
    summary: Optional[TextDocument] = Field(None, description="Epic summary as TextDocument")
    status: str = Field(..., description="Epic status")
    assignee: Optional[str] = Field(None, description="Assignee display name")
    issues: list[JiraIssue] = Field(default_factory=list, description="Issues in epic")
    issue_count: int = Field(0, ge=0, description="Number of issues in epic")
    url: str = Field(..., description="Epic URL")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["jira", "epic", status_to_slug(self.status)])
        for issue in self.issues:
            if issue.tags:
                all_tags.update(issue.tags)
        self.tags = sorted(all_tags)
        return self


class JiraSprint(BaseContainer[JiraIssue]):
    """Jira sprint representation."""

    sprint_id: int = Field(..., description="Sprint ID")
    name: str = Field(..., description="Sprint name")
    state: str = Field(..., description="Sprint state (active, closed, future)")
    goal: Optional[TextDocument] = Field(None, description="Sprint goal as TextDocument")
    board_id: Optional[int] = Field(None, description="Board ID")
    start_date: Optional[datetime] = Field(None, description="Sprint start date")
    end_date: Optional[datetime] = Field(None, description="Sprint end date")
    complete_date: Optional[datetime] = Field(None, description="Sprint completion date")

    items: list[JiraIssue] = Field(default_factory=list, description="Issues in sprint")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["jira", "sprint", self.state])
        for item in self.items:
            if item.tags:
                all_tags.update(item.tags)
        self.tags = sorted(all_tags)
        return self


class JiraProject(BaseContainer[JiraIssue]):
    """Jira project representation."""

    project_key: str = Field(..., description="Project key (e.g. ENG)")
    project_id: str = Field(..., description="Internal Jira project ID")
    name: str = Field(..., description="Project name")
    description: Optional[TextDocument] = Field(
        None, description="Project description as TextDocument"
    )
    project_type: str = Field(..., description="Project type (software, business, service_desk)")
    lead: Optional[str] = Field(None, description="Project lead")
    issue_count: int = Field(0, ge=0, description="Total issue count in project")
    url: str = Field(..., description="Project URL")

    items: list[JiraIssue] = Field(default_factory=list, description="Issues in project")

    media_type: MediaType = MediaType.TEXT_PLAIN
    category: DataCategory = DataCategory.STRUCTURED

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["jira", "project", self.project_type])
        for item in self.items:
            if item.tags:
                all_tags.update(item.tags)
        self.tags = sorted(all_tags)
        return self
