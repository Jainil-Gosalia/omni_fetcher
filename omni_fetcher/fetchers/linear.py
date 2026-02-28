"""Linear fetcher for OmniFetcher."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, date
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from omni_fetcher.core.exceptions import FetchError, SourceNotFoundError
from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.atomics import TextDocument, TextFormat
from omni_fetcher.schemas.linear import (
    LinearCycle,
    LinearIssue,
    LinearProject,
    LinearTeam,
)


LINEAR_API_URL = "https://api.linear.app/graphql"

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

ISSUE_IDENTIFIER_PATTERN = re.compile(r"^[A-Z]+-\d+$")


def _parse_datetime(value: Any) -> Optional[datetime]:
    """Parse datetime string to datetime object."""
    if not value:
        return None
    try:
        dt_str = value.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)
    except (ValueError, AttributeError):
        return None


def _parse_date(value: Any) -> Optional[date]:
    """Parse date string to date object."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, AttributeError):
        return None


def _get_nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely get nested dictionary value."""
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return default
        if current is None:
            return default
    return current if current is not None else default


@dataclass
class LinearRoute:
    """Parsed Linear URI route."""

    type: str
    identifier: Optional[str] = None
    team_key: Optional[str] = None
    key: Optional[str] = None
    project_id: Optional[str] = None
    cycle_id: Optional[str] = None


def parse_linear_uri(uri: str) -> LinearRoute:
    """Parse Linear URI to determine route type."""

    # Handle linear:// scheme
    if uri.startswith("linear://"):
        path = uri[9:].strip("/")
        parts = path.split("/")

        if len(parts) >= 2:
            if parts[0] == "issue":
                return LinearRoute(type="issue", identifier=parts[1])
            elif parts[0] == "team":
                return LinearRoute(type="team", key=parts[1])
            elif parts[0] == "project":
                return LinearRoute(type="project", project_id=parts[1])
            elif parts[0] == "cycle":
                return LinearRoute(type="cycle", cycle_id=parts[1])

        if len(parts) == 1:
            maybe_uuid = parts[0]
            if UUID_PATTERN.match(maybe_uuid):
                return LinearRoute(type="issue", identifier=maybe_uuid)
            if ISSUE_IDENTIFIER_PATTERN.match(maybe_uuid.upper()):
                team_key = maybe_uuid.rsplit("-", 1)[0]
                return LinearRoute(type="issue", identifier=maybe_uuid, team_key=team_key)
            # Assume it's a team key
            return LinearRoute(type="team", key=maybe_uuid)

        raise ValueError(f"Invalid linear:// URI: {uri}")

    # Handle https://linear.app URLs
    if "linear.app" not in uri.lower():
        raise ValueError(f"Invalid Linear URI: {uri}")

    # Parse the path
    parsed = urlparse(uri)
    path = parsed.path.strip("/")
    parts = path.split("/")

    # Handle /team/TEAMKEY/issue/ISSUE format
    if "/issue/" in uri:
        issue_idx = uri.index("/issue/")
        issue_part = uri[issue_idx + 7 :]
        identifier = issue_part.split("/")[0] if "/" in issue_part else issue_part

        # Extract team_key from /team/TEAMKEY/ pattern
        team_key = None
        if "/team/" in uri:
            team_idx = uri.index("/team/")
            team_part = uri[team_idx + 6 :]
            team_key = team_part.split("/")[0] if "/" in team_part else team_part

        return LinearRoute(type="issue", identifier=identifier, team_key=team_key)

    # Handle /team/TEAMKEY format
    if len(parts) >= 2 and parts[0] == "team":
        team_key = parts[1]
        return LinearRoute(type="team", key=team_key)

    # Handle just /team with nothing after
    if parts[0] == "team" and len(parts) == 1:
        raise ValueError(f"Invalid Linear URI: {uri}")

    raise ValueError(f"Invalid Linear URI: {uri}")


@source(
    name="linear",
    uri_patterns=["linear.app", "linear://"],
    priority=15,
    description="Fetch from Linear — issues, teams, projects, cycles",
    auth={"type": "bearer", "token_env": "LINEAR_API_KEY"},
)
class LinearFetcher(BaseFetcher):
    """Fetcher for Linear API - issues, teams, projects, cycles."""

    name = "linear"
    priority = 15

    def __init__(self, timeout: float = 30.0):
        super().__init__()
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if URI is a Linear URL."""
        if not uri:
            return False
        lower_uri = uri.lower()
        if "linear.app" in lower_uri:
            return True
        if lower_uri.startswith("linear://"):
            return True
        return False

    def _get_token(self) -> str:
        """Get Linear API token from environment."""
        token = os.environ.get("LINEAR_API_KEY")
        if not token:
            raise FetchError(
                "LINEAR_API_KEY environment variable not set",
                "Linear API requires authentication. Set LINEAR_API_KEY env var.",
            )
        return token

    async def _graphql(
        self,
        query: str,
        variables: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute GraphQL query against Linear API."""
        token = self._get_token()
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": "omni_fetcher",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                LINEAR_API_URL,
                json={"query": query, "variables": variables or {}},
                headers=headers,
            )

            if response.status_code != 200:
                raise FetchError(
                    LINEAR_API_URL,
                    f"Linear API error: {response.status_code} - {response.text}",
                )

            data = response.json()

            if "errors" in data:
                error_messages = [e.get("message", "Unknown error") for e in data["errors"]]
                raise FetchError(LINEAR_API_URL, f"GraphQL errors: {', '.join(error_messages)}")

            return data.get("data", {})

    def _parse_issue_fields(self, issue_data: dict[str, Any]) -> dict[str, Any]:
        """Parse issue fields from GraphQL response."""
        state = _get_nested(issue_data, "state", "name", default="Unknown")
        state_type = _get_nested(issue_data, "state", "type", default="unknown")
        priority = issue_data.get("priority", 0)
        priority_labels = {
            0: "No priority",
            1: "Urgent",
            2: "High",
            3: "Medium",
            4: "Low",
        }

        description_doc = None
        if issue_data.get("description"):
            description_doc = TextDocument(
                source_uri=issue_data.get("url", ""),
                content=issue_data["description"],
                format=TextFormat.MARKDOWN,
            )

        labels = []
        for label in issue_data.get("labels", {}).get("nodes", []):
            if label:
                labels.append(label.get("name", ""))

        comments = []
        for comment in issue_data.get("comments", {}).get("nodes", []):
            if comment and comment.get("body"):
                comments.append(
                    TextDocument(
                        source_uri=comment.get("url", ""),
                        content=comment["body"],
                        format=TextFormat.PLAIN,
                    )
                )

        return {
            "issue_id": issue_data.get("id", ""),
            "identifier": issue_data.get("identifier", ""),
            "title": issue_data.get("title", ""),
            "description": description_doc,
            "state": state,
            "state_type": state_type.lower(),
            "priority": priority,
            "priority_label": priority_labels.get(priority, "Unknown"),
            "assignee": _get_nested(issue_data, "assignee", "name"),
            "creator": _get_nested(issue_data, "creator", "name"),
            "team": _get_nested(issue_data, "team", "name", default=""),
            "team_key": _get_nested(issue_data, "team", "key", default=""),
            "project": _get_nested(issue_data, "project", "name"),
            "cycle": _get_nested(issue_data, "cycle", "name"),
            "labels": labels,
            "comments": comments,
            "parent_id": issue_data.get("parent { id }"),
            "estimate": issue_data.get("estimate"),
            "due_date": _parse_date(issue_data.get("dueDate")),
            "completed_at": _parse_datetime(issue_data.get("completedAt")),
            "created_at": _parse_datetime(issue_data.get("createdAt")) or datetime.now(),
            "updated_at": _parse_datetime(issue_data.get("updatedAt")) or datetime.now(),
            "url": issue_data.get("url", ""),
        }

    def _parse_team_issue(self, issue_data: dict[str, Any]) -> LinearIssue:
        """Parse issue from team query (limited fields)."""
        state = _get_nested(issue_data, "state", "name", default="Unknown")
        state_type = _get_nested(issue_data, "state", "type", default="unknown")
        priority = issue_data.get("priority", 0)
        priority_labels = {
            0: "No priority",
            1: "Urgent",
            2: "High",
            3: "Medium",
            4: "Low",
        }

        return LinearIssue(
            issue_id=issue_data.get("id", ""),
            identifier=issue_data.get("identifier", ""),
            title=issue_data.get("title", ""),
            description=None,
            state=state,
            state_type=state_type.lower(),
            priority=priority,
            priority_label=priority_labels.get(priority, "Unknown"),
            assignee=_get_nested(issue_data, "assignee", "name"),
            creator=_get_nested(issue_data, "creator", "name"),
            team=_get_nested(issue_data, "team", "name", default=""),
            team_key=_get_nested(issue_data, "team", "key", default=""),
            project=_get_nested(issue_data, "project", "name"),
            cycle=_get_nested(issue_data, "cycle", "name"),
            labels=[],
            comments=[],
            parent_id=issue_data.get("parent { id }"),
            estimate=issue_data.get("estimate"),
            due_date=_parse_date(issue_data.get("dueDate")),
            completed_at=_parse_datetime(issue_data.get("completedAt")),
            created_at=_parse_datetime(issue_data.get("createdAt")) or datetime.now(),
            updated_at=_parse_datetime(issue_data.get("updatedAt")) or datetime.now(),
            url=issue_data.get("url", ""),
        )

    async def _fetch_issue(self, route: LinearRoute, uri: str) -> LinearIssue:
        """Fetch a single issue by ID or identifier."""
        identifier = route.identifier
        is_uuid = UUID_PATTERN.match(identifier or "")

        if is_uuid:
            query = """
                query Issue($id: String!) {
                    issue(id: $id) {
                        id
                        identifier
                        title
                        description
                        url
                        priority
                        estimate
                        dueDate
                        completedAt
                        createdAt
                        updatedAt
                        parent { id }
                        state {
                            name
                            type
                        }
                        team {
                            id
                            key
                            name
                        }
                        assignee {
                            name
                        }
                        creator {
                            name
                        }
                        project {
                            name
                        }
                        cycle {
                            name
                        }
                        labels {
                            nodes {
                                name
                            }
                        }
                        comments {
                            nodes {
                                body
                                url
                            }
                        }
                    }
                }
            """
            variables = {"id": identifier}
        else:
            # For non-UUID identifiers like ENG-1, use the same query with id
            # Linear API accepts both UUID and identifier as id
            query = """
                query Issue($id: String!) {
                    issue(id: $id) {
                        id
                        identifier
                        title
                        description
                        url
                        priority
                        estimate
                        dueDate
                        completedAt
                        createdAt
                        updatedAt
                        parent { id }
                        state {
                            name
                            type
                        }
                        team {
                            id
                            key
                            name
                        }
                        assignee {
                            name
                        }
                        creator {
                            name
                        }
                        project {
                            name
                        }
                        cycle {
                            name
                        }
                        labels {
                            nodes {
                                name
                            }
                        }
                        comments {
                            nodes {
                                body
                                url
                            }
                        }
                    }
                }
            """
            variables = {"id": identifier}

        data = await self._graphql(query, variables)
        issue_data = data.get("issue")

        if not issue_data:
            raise SourceNotFoundError(f"Issue not found: {uri}")

        fields = self._parse_issue_fields(issue_data)
        return LinearIssue(**fields)

    async def _fetch_team(
        self,
        team_id: str,
        uri: str,
        max_issues: int = 100,
    ) -> LinearTeam:
        """Fetch team with its issues using pagination."""
        query = """
            query Team($id: String!) {
                team(id: $id) {
                    id
                    key
                    name
                    description
                    issues(
                        first: 100
                        orderBy: updatedAt
                    ) {
                        nodes {
                            id
                            identifier
                            title
                            url
                            priority
                            estimate
                            dueDate
                            completedAt
                            createdAt
                            updatedAt
                            parent { id }
                            state {
                                name
                                type
                            }
                            assignee {
                                name
                            }
                            creator {
                                name
                            }
                            project {
                                name
                            }
                            cycle {
                                name
                            }
                        }
                        pageInfo {
                            hasNextPage
                            endCursor
                        }
                    }
                }
            }
        """

        all_issues: list[LinearIssue] = []
        cursor = None
        total_count = 0

        while len(all_issues) < max_issues:
            variables = {"id": team_id}
            if cursor:
                variables["cursor"] = cursor

            data = await self._graphql(query, variables)
            team_data = data.get("team")

            if not team_data:
                raise SourceNotFoundError(f"Team not found: {team_id}")

            issue_nodes = team_data.get("issues", {}).get("nodes", [])
            total_count = len(issue_nodes)

            for issue_data in issue_nodes:
                if issue_data:
                    all_issues.append(self._parse_team_issue(issue_data))

            page_info = team_data.get("issues", {}).get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

            if len(all_issues) >= max_issues:
                break

        description_doc = None
        if team_data.get("description"):
            description_doc = TextDocument(
                source_uri=uri,
                content=team_data["description"],
                format=TextFormat.PLAIN,
            )

        return LinearTeam(
            team_id=team_data.get("id", ""),
            key=team_data.get("key", ""),
            name=team_data.get("name", ""),
            description=description_doc,
            items=all_issues[:max_issues],
            issue_count=total_count,
            source_uri=uri,
            item_count=len(all_issues[:max_issues]),
        )

    async def _fetch_project(
        self,
        project_id: str,
        uri: str,
        max_issues: int = 100,
    ) -> LinearProject:
        """Fetch project with its issues using pagination."""
        query = """
            query Project($id: String!) {
                project(id: $id) {
                    id
                    name
                    description
                    state
                    progress
                    url
                    lead {
                        name
                    }
                    targetDate
                    issues(
                        first: 100
                        orderBy: updatedAt
                    ) {
                        nodes {
                            id
                            identifier
                            title
                            url
                            priority
                            estimate
                            dueDate
                            completedAt
                            createdAt
                            updatedAt
                            parent { id }
                            state {
                                name
                                type
                            }
                            assignee {
                                name
                            }
                            creator {
                                name
                            }
                            team {
                                id
                                key
                                name
                            }
                            cycle {
                                name
                            }
                        }
                        pageInfo {
                            hasNextPage
                            endCursor
                        }
                    }
                }
            }
        """

        all_issues: list[LinearIssue] = []
        cursor = None
        total_count = 0
        completed_count = 0

        while len(all_issues) < max_issues:
            variables = {"id": project_id}
            if cursor:
                variables["cursor"] = cursor

            data = await self._graphql(query, variables)
            project_data = data.get("project")

            if not project_data:
                raise SourceNotFoundError(f"Project not found: {project_id}")

            issue_nodes = project_data.get("issues", {}).get("nodes", [])
            total_count = len(issue_nodes)

            for issue_data in issue_nodes:
                if issue_data:
                    issue = self._parse_team_issue(issue_data)
                    all_issues.append(issue)
                    if issue.state_type == "completed":
                        completed_count += 1

            page_info = project_data.get("issues", {}).get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

            if len(all_issues) >= max_issues:
                break

        description_doc = None
        if project_data.get("description"):
            description_doc = TextDocument(
                source_uri=uri,
                content=project_data["description"],
                format=TextFormat.PLAIN,
            )

        return LinearProject(
            project_id=project_data.get("id", ""),
            name=project_data.get("name", ""),
            description=description_doc,
            state=project_data.get("state", "unknown"),
            progress=project_data.get("progress", 0.0),
            lead=_get_nested(project_data, "lead", "name"),
            target_date=_parse_date(project_data.get("targetDate")),
            items=all_issues[:max_issues],
            issue_count=total_count,
            completed_count=completed_count,
            url=project_data.get("url", uri),
            source_uri=uri,
            item_count=len(all_issues[:max_issues]),
        )

    async def _fetch_cycle(
        self,
        cycle_id: str,
        uri: str,
        max_issues: int = 100,
    ) -> LinearCycle:
        """Fetch cycle with its issues using pagination."""
        query = """
            query Cycle($id: String!) {
                cycle(id: $id) {
                    id
                    number
                    name
                    state
                    progress
                    startsAt
                    endsAt
                    completedAt
                    team {
                        name
                    }
                    issues(
                        first: 100
                        orderBy: updatedAt
                    ) {
                        nodes {
                            id
                            identifier
                            title
                            url
                            priority
                            estimate
                            dueDate
                            completedAt
                            createdAt
                            updatedAt
                            parent { id }
                            state {
                                name
                                type
                            }
                            assignee {
                                name
                            }
                            creator {
                                name
                            }
                            team {
                                id
                                key
                                name
                            }
                            project {
                                name
                            }
                        }
                        pageInfo {
                            hasNextPage
                            endCursor
                        }
                    }
                }
            }
        """

        all_issues: list[LinearIssue] = []
        cursor = None

        while len(all_issues) < max_issues:
            variables = {"id": cycle_id}
            if cursor:
                variables["cursor"] = cursor

            data = await self._graphql(query, variables)
            cycle_data = data.get("cycle")

            if not cycle_data:
                raise SourceNotFoundError(f"Cycle not found: {cycle_id}")

            issue_nodes = cycle_data.get("issues", {}).get("nodes", [])

            for issue_data in issue_nodes:
                if issue_data:
                    issue = self._parse_team_issue(issue_data)
                    all_issues.append(issue)

            page_info = cycle_data.get("issues", {}).get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

            if len(all_issues) >= max_issues:
                break

        return LinearCycle(
            cycle_id=cycle_data.get("id", ""),
            number=cycle_data.get("number", 0),
            name=cycle_data.get("name"),
            team=_get_nested(cycle_data, "team", "name", default=""),
            state=cycle_data.get("state", "unknown"),
            progress=cycle_data.get("progress", 0.0),
            items=all_issues[:max_issues],
            starts_at=_parse_datetime(cycle_data.get("startsAt")) or datetime.now(),
            ends_at=_parse_datetime(cycle_data.get("endsAt")) or datetime.now(),
            completed_at=_parse_datetime(cycle_data.get("completedAt")),
            source_uri=uri,
            item_count=len(all_issues[:max_issues]),
        )

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch from Linear based on URI type."""
        route = parse_linear_uri(uri)
        max_issues = kwargs.get("max_issues", 100)

        if route.type == "issue":
            return await self._fetch_issue(route, uri)
        elif route.type == "team":
            return await self._fetch_team(route.key or "", uri, max_issues)
        elif route.type == "project":
            return await self._fetch_project(route.project_id or "", uri, max_issues)
        elif route.type == "cycle":
            return await self._fetch_cycle(route.cycle_id or "", uri, max_issues)
        else:
            raise SourceNotFoundError(f"Unsupported Linear route type: {route.type}")
