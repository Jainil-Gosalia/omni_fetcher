"""Tests for Jira schemas."""

from datetime import datetime

import pytest

from omni_fetcher.schemas.jira import (
    JiraEpic,
    JiraIssue,
    JiraProject,
    JiraSprint,
    status_to_slug,
)
from omni_fetcher.schemas.atomics import TextDocument, TextFormat


class TestStatusToSlug:
    def test_simple_status(self):
        """Simple status conversion."""
        assert status_to_slug("Done") == "done"
        assert status_to_slug("Open") == "open"
        assert status_to_slug("Closed") == "closed"

    def test_status_with_space(self):
        """Status with space conversion."""
        assert status_to_slug("In Progress") == "in_progress"
        assert status_to_slug("To Do") == "to_do"
        assert status_to_slug("In Review") == "in_review"

    def test_status_with_dash(self):
        """Status with dash conversion."""
        assert status_to_slug("In-Review") == "in_review"
        assert status_to_slug("Done-Done") == "done_done"


class TestJiraIssueTags:
    def test_issue_tags_include_type_and_status(self):
        """Issue type and status are included in tags."""
        issue = JiraIssue(
            issue_key="ENG-123",
            issue_id="12345",
            issue_type="Bug",
            title="Test issue",
            status="In Progress",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://company.atlassian.net/browse/ENG-123",
        )
        assert "jira" in issue.tags
        assert "issue" in issue.tags
        assert "bug" in issue.tags
        assert "in_progress" in issue.tags

    def test_issue_tags_include_labels(self):
        """Labels are merged into tags."""
        issue = JiraIssue(
            issue_key="ENG-123",
            issue_id="12345",
            issue_type="Bug",
            title="Test issue",
            status="Done",
            labels=["security", "backend", "urgent"],
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://company.atlassian.net/browse/ENG-123",
        )
        assert "security" in issue.tags
        assert "backend" in issue.tags
        assert "urgent" in issue.tags

    def test_issue_tags_lowercase(self):
        """All tags are lowercase."""
        issue = JiraIssue(
            issue_key="ENG-123",
            issue_id="12345",
            issue_type="Story",
            title="Test issue",
            status="In Progress",
            labels=["Frontend"],
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://company.atlassian.net/browse/ENG-123",
        )
        for tag in issue.tags:
            assert tag == tag.lower()


class TestJiraProjectTags:
    def test_project_tags_merged_from_issues(self):
        """Child issue tags are merged into project tags."""
        issue = JiraIssue(
            issue_key="ENG-123",
            issue_id="12345",
            issue_type="Bug",
            title="Test issue",
            status="In Progress",
            labels=["security"],
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://company.atlassian.net/browse/ENG-123",
        )
        project = JiraProject(
            project_key="ENG",
            project_id="10000",
            name="Engineering",
            project_type="software",
            items=[issue],
            issue_count=1,
            url="https://company.atlassian.net/projects/ENG",
        )
        assert "jira" in project.tags
        assert "project" in project.tags
        assert "software" in project.tags
        assert "bug" in project.tags
        assert "security" in project.tags
        assert "in_progress" in project.tags

    def test_project_tags_include_type(self):
        """Project type is included in tags."""
        project = JiraProject(
            project_key="ENG",
            project_id="10000",
            name="Engineering",
            project_type="software",
            items=[],
            issue_count=0,
            url="https://company.atlassian.net/projects/ENG",
        )
        assert "jira" in project.tags
        assert "project" in project.tags
        assert "software" in project.tags


class TestJiraSprintTags:
    def test_sprint_tags_include_state(self):
        """Sprint state is included in tags."""
        sprint = JiraSprint(
            sprint_id=42,
            name="Sprint 42",
            state="active",
            items=[],
            item_count=0,
        )
        assert "jira" in sprint.tags
        assert "sprint" in sprint.tags
        assert "active" in sprint.tags

    def test_sprint_tags_from_issues(self):
        """Tags from issues are merged."""
        issue = JiraIssue(
            issue_key="ENG-123",
            issue_id="12345",
            issue_type="Story",
            title="Test issue",
            status="In Progress",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://company.atlassian.net/browse/ENG-123",
        )
        sprint = JiraSprint(
            sprint_id=42,
            name="Sprint 42",
            state="active",
            items=[issue],
            item_count=1,
        )
        assert "story" in sprint.tags
        assert "in_progress" in sprint.tags


class TestJiraEpicTags:
    def test_epic_tags_include_status(self):
        """Epic status is included in tags."""
        epic = JiraEpic(
            epic_key="ENG-10",
            epic_id="10010",
            name="Epic Name",
            status="In Progress",
            issues=[],
            issue_count=0,
            url="https://company.atlassian.net/browse/ENG-10",
        )
        assert "jira" in epic.tags
        assert "epic" in epic.tags
        assert "in_progress" in epic.tags

    def test_epic_tags_from_issues(self):
        """Tags from issues are merged."""
        issue = JiraIssue(
            issue_key="ENG-123",
            issue_id="12345",
            issue_type="Task",
            title="Task",
            status="Done",
            labels=["feature"],
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://company.atlassian.net/browse/ENG-123",
        )
        epic = JiraEpic(
            epic_key="ENG-10",
            epic_id="10010",
            name="Epic Name",
            status="In Progress",
            issues=[issue],
            issue_count=1,
            url="https://company.atlassian.net/browse/ENG-10",
        )
        assert "task" in epic.tags
        assert "done" in epic.tags
        assert "feature" in epic.tags


class TestJiraIssueSchema:
    def test_issue_with_description(self):
        """Issue with description as TextDocument."""
        issue = JiraIssue(
            issue_key="ENG-123",
            issue_id="12345",
            issue_type="Bug",
            title="Login bug",
            description=TextDocument(
                source_uri="",
                content="Bug description",
                format=TextFormat.MARKDOWN,
            ),
            status="Open",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://company.atlassian.net/browse/ENG-123",
        )
        assert issue.description is not None
        assert issue.description.content == "Bug description"
        assert issue.description.format == TextFormat.MARKDOWN

    def test_issue_with_comments(self):
        """Issue with comments as list of TextDocument."""
        issue = JiraIssue(
            issue_key="ENG-123",
            issue_id="12345",
            issue_type="Bug",
            title="Login bug",
            comments=[
                TextDocument(source_uri="", content="Comment 1", format=TextFormat.PLAIN),
                TextDocument(source_uri="", content="Comment 2", format=TextFormat.PLAIN),
            ],
            status="Open",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://company.atlassian.net/browse/ENG-123",
        )
        assert len(issue.comments) == 2
        assert issue.comments[0].content == "Comment 1"

    def test_issue_fields(self):
        """All issue fields are properly set."""
        issue = JiraIssue(
            issue_key="ENG-123",
            issue_id="12345",
            issue_type="Story",
            title="New feature",
            status="In Progress",
            priority="High",
            assignee="Jane Doe",
            reporter="John Smith",
            labels=["feature"],
            components=["auth"],
            fix_versions=["v1.0"],
            sprint="Sprint 42",
            epic_key="ENG-10",
            epic_name="Authentication",
            story_points=5.0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            resolved_at=datetime.now(),
            url="https://company.atlassian.net/browse/ENG-123",
        )
        assert issue.issue_key == "ENG-123"
        assert issue.priority == "High"
        assert issue.assignee == "Jane Doe"
        assert issue.reporter == "John Smith"
        assert issue.story_points == 5.0
        assert issue.epic_key == "ENG-10"


class TestJiraProjectSchema:
    def test_project_with_description(self):
        """Project with description as TextDocument."""
        project = JiraProject(
            project_key="ENG",
            project_id="10000",
            name="Engineering",
            description=TextDocument(
                source_uri="",
                content="Engineering team project",
                format=TextFormat.PLAIN,
            ),
            project_type="software",
            lead="Jane Doe",
            items=[],
            issue_count=0,
            url="https://company.atlassian.net/projects/ENG",
        )
        assert project.description is not None
        assert project.description.content == "Engineering team project"
        assert project.lead == "Jane Doe"


class TestJiraSprintSchema:
    def test_sprint_with_goal(self):
        """Sprint with goal as TextDocument."""
        sprint = JiraSprint(
            sprint_id=42,
            name="Sprint 42",
            state="active",
            goal=TextDocument(
                source_uri="",
                content="Complete authentication",
                format=TextFormat.PLAIN,
            ),
            board_id=1,
            start_date=datetime.now(),
            end_date=datetime.now(),
            items=[],
            item_count=0,
        )
        assert sprint.goal is not None
        assert sprint.goal.content == "Complete authentication"
        assert sprint.board_id == 1
