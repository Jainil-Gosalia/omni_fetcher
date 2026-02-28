"""Tests for Linear schemas."""

from datetime import datetime, date

import pytest

from omni_fetcher.schemas.linear import (
    LinearIssue,
    LinearTeam,
    LinearProject,
    LinearCycle,
    priority_to_slug,
    state_type_to_slug,
)
from omni_fetcher.schemas.atomics import TextDocument, TextFormat


class TestPriorityToSlug:
    def test_priority_0_no_priority(self):
        assert priority_to_slug(0) == "no_priority"

    def test_priority_1_urgent(self):
        assert priority_to_slug(1) == "urgent"

    def test_priority_2_high(self):
        assert priority_to_slug(2) == "high"

    def test_priority_3_medium(self):
        assert priority_to_slug(3) == "medium"

    def test_priority_4_low(self):
        assert priority_to_slug(4) == "low"

    def test_priority_invalid(self):
        assert priority_to_slug(99) == "unknown"


class TestStateTypeToSlug:
    def test_state_triage(self):
        assert state_type_to_slug("triage") == "triage"

    def test_state_backlog(self):
        assert state_type_to_slug("backlog") == "backlog"

    def test_state_started(self):
        assert state_type_to_slug("started") == "started"

    def test_state_completed(self):
        assert state_type_to_slug("completed") == "completed"

    def test_state_cancelled(self):
        assert state_type_to_slug("cancelled") == "cancelled"

    def test_state_invalid(self):
        assert state_type_to_slug("unknown") == "unknown"


class TestLinearIssueSchema:
    def test_issue_minimal_fields(self):
        issue = LinearIssue(
            issue_id="abc123",
            identifier="ENG-123",
            title="Test issue",
            state="In Progress",
            state_type="started",
            priority=2,
            priority_label="High",
            team="Engineering",
            team_key="ENG",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://linear.app/team/issue/ENG-123",
        )
        assert issue.issue_id == "abc123"
        assert issue.identifier == "ENG-123"
        assert issue.title == "Test issue"


class TestLinearIssueTags:
    def test_issue_tags_basic(self):
        issue = LinearIssue(
            issue_id="abc123",
            identifier="ENG-123",
            title="Test issue",
            state="Backlog",
            state_type="backlog",
            priority=0,
            priority_label="No priority",
            team="Engineering",
            team_key="ENG",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://linear.app/team/issue/ENG-123",
        )
        assert "linear" in issue.tags
        assert "issue" in issue.tags

    def test_issue_tags_with_state_type(self):
        issue = LinearIssue(
            issue_id="abc123",
            identifier="ENG-123",
            title="Test issue",
            state="In Progress",
            state_type="started",
            priority=0,
            priority_label="No priority",
            team="Engineering",
            team_key="ENG",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            url="https://linear.app/team/issue/ENG-123",
        )
        assert "started" in issue.tags


class TestLinearTeamSchema:
    def test_team_minimal_fields(self):
        team = LinearTeam(
            team_id="team123",
            key="ENG",
            name="Engineering",
            items=[],
            issue_count=0,
        )
        assert team.team_id == "team123"
        assert team.key == "ENG"


class TestLinearProjectSchema:
    def test_project_minimal_fields(self):
        project = LinearProject(
            project_id="proj123",
            name="My Project",
            state="started",
            progress=0.5,
            items=[],
            issue_count=0,
            completed_count=0,
            url="https://linear.app/project/proj123",
        )
        assert project.project_id == "proj123"
        assert project.state == "started"


class TestLinearCycleSchema:
    def test_cycle_minimal_fields(self):
        cycle = LinearCycle(
            cycle_id="cycle123",
            number=42,
            name="Sprint 42",
            team="Engineering",
            state="active",
            progress=0.5,
            items=[],
            starts_at=datetime.now(),
            ends_at=datetime.now(),
        )
        assert cycle.cycle_id == "cycle123"
        assert cycle.state == "active"
