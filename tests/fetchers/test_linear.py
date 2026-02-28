"""Tests for LinearFetcher."""

import pytest

from omni_fetcher.fetchers.linear import (
    LinearFetcher,
    parse_linear_uri,
    LinearRoute,
)


class TestLinearFetcherCreation:
    def test_creation(self):
        """Can create LinearFetcher."""
        fetcher = LinearFetcher()
        assert fetcher.name == "linear"
        assert fetcher.priority == 15


class TestLinearFetcherCanHandle:
    def test_can_handle_linear_app_urls(self):
        """LinearFetcher handles linear.app URLs."""
        assert LinearFetcher.can_handle("https://linear.app/team/engineering")
        assert LinearFetcher.can_handle("https://linear.app/team/engineering/issue/ENG-123")
        assert LinearFetcher.can_handle("https://mycompany.linear.app/team/test")

    def test_can_handle_linear_app_issue_url(self):
        """LinearFetcher handles linear.app issue URLs."""
        assert LinearFetcher.can_handle("https://linear.app/team/ENG/issue/ENG-123")

    def test_can_handle_linear_protocol(self):
        """LinearFetcher handles linear:// URIs."""
        assert LinearFetcher.can_handle("linear://issue/ENG-123")
        assert LinearFetcher.can_handle("linear://team/ENG")
        assert LinearFetcher.can_handle("linear://project/project-uuid")
        assert LinearFetcher.can_handle("linear://cycle/cycle-uuid")

    def test_cannot_handle_non_linear(self):
        """LinearFetcher rejects non-Linear URLs."""
        assert not LinearFetcher.can_handle("https://github.com/owner/repo")
        assert not LinearFetcher.can_handle("https://example.com/page")
        assert not LinearFetcher.can_handle("jira://issue/ENG-123")
        assert not LinearFetcher.can_handle("")


class TestParseLinearUri:
    def test_parse_issue_from_linear_app_url(self):
        """Parse issue from linear.app URL."""
        route = parse_linear_uri("https://linear.app/team/engineering/issue/ENG-123")
        assert route.type == "issue"
        assert route.identifier == "ENG-123"
        assert route.team_key == "engineering"

    def test_parse_team_from_linear_app_url(self):
        """Parse team from linear.app URL."""
        route = parse_linear_uri("https://linear.app/team/engineering")
        assert route.type == "team"
        assert route.key == "engineering"

    def test_parse_issue_by_identifier(self):
        """Parse issue by identifier (ENG-123)."""
        route = parse_linear_uri("linear://issue/ENG-123")
        assert route.type == "issue"
        assert route.identifier == "ENG-123"

    def test_parse_issue_by_uuid(self):
        """Parse issue by UUID."""
        route = parse_linear_uri("linear://abc12345-abc1-abc1-abc1-abc123456789")
        assert route.type == "issue"
        assert route.identifier == "abc12345-abc1-abc1-abc1-abc123456789"

    def test_parse_team_by_key(self):
        """Parse team by key."""
        route = parse_linear_uri("linear://team/ENG")
        assert route.type == "team"
        assert route.key == "ENG"

    def test_parse_project(self):
        """Parse project."""
        route = parse_linear_uri("linear://project/project-uuid")
        assert route.type == "project"
        assert route.project_id == "project-uuid"

    def test_parse_cycle(self):
        """Parse cycle."""
        route = parse_linear_uri("linear://cycle/cycle-uuid")
        assert route.type == "cycle"
        assert route.cycle_id == "cycle-uuid"

    def test_parse_invalid_uri_raises_error(self):
        """Invalid URIs raise ValueError."""
        with pytest.raises(ValueError):
            parse_linear_uri("https://example.com/page")

        with pytest.raises(ValueError):
            parse_linear_uri("linear://unknown/path")
