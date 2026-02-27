"""Jira fetcher for OmniFetcher."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

from omni_fetcher.core.exceptions import FetchError, SourceNotFoundError
from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.atomics import TextDocument, TextFormat
from omni_fetcher.schemas.jira import (
    JiraEpic,
    JiraIssue,
    JiraProject,
    JiraSprint,
)

try:
    from atlassian import Jira as AtlassianJira

    ATLASSIAN_AVAILABLE = True
except ImportError:
    ATLASSIAN_AVAILABLE = False


JIRA_CLOUD_URL = "https://api.atlassian.com"

STORY_POINT_FIELDS = [
    "story_points",
    "customfield_10016",
    "customfield_10028",
    "customfield_10026",
]

SPRINT_FIELD = "customfield_10020"
EPIC_LINK_FIELD = "customfield_10014"


@dataclass
class JiraRoute:
    """Parsed Jira URI route."""

    type: str
    issue_key: Optional[str] = None
    project_key: Optional[str] = None
    sprint_id: Optional[int] = None
    epic_key: Optional[str] = None


def parse_jira_uri(uri: str) -> JiraRoute:
    """Parse a Jira URI into a route."""
    if uri.startswith("jira://"):
        path = uri[7:].strip("/")
        parts = path.split("/")

        if len(parts) >= 2 and parts[0] == "issue":
            return JiraRoute(type="issue", issue_key=parts[1])
        if len(parts) >= 2 and parts[0] == "project":
            return JiraRoute(type="project", project_key=parts[1])
        if len(parts) >= 2 and parts[0] == "sprint":
            return JiraRoute(type="sprint", sprint_id=int(parts[1]))
        if len(parts) >= 2 and parts[0] == "epic":
            return JiraRoute(type="epic", epic_key=parts[1])

        raise ValueError(f"Invalid jira:// URI: {uri}")

    parsed = urlparse(uri)
    path = parsed.path

    if "/browse/" in uri:
        match = re.search(r"/browse/([A-Z]+-\d+)", path)
        if match:
            return JiraRoute(type="issue", issue_key=match.group(1))

    if "/projects/" in uri:
        match = re.search(r"/projects/([A-Z0-9]+)/?", path)
        if match:
            return JiraRoute(type="project", project_key=match.group(1))

    raise ValueError(f"Invalid Jira URI: {uri}")


def convert_adf_to_markdown(adf_content: dict[str, Any]) -> str:
    """Convert Atlassian Document Format (ADF) to markdown."""
    if not adf_content:
        return ""

    if isinstance(adf_content, str):
        return adf_content

    result = []
    for node in adf_content.get("content", []):
        result.append(_convert_adf_node(node))

    return "\n".join(result)


def _convert_adf_node(node: dict[str, Any]) -> str:
    """Convert a single ADF node to markdown."""
    node_type = node.get("type", "")
    content = node.get("content", [])

    if node_type == "paragraph":
        text = "".join(_get_text_from_nodes(content))
        return text

    if node_type == "heading":
        level = node.get("attrs", {}).get("level", 1)
        text = "".join(_get_text_from_nodes(content))
        return "#" * level + " " + text

    if node_type == "bulletList":
        items = []
        for item in content:
            if item.get("type") == "listItem":
                item_text = "".join(_get_text_from_nodes(item.get("content", [])))
                items.append(f"- {item_text}")
        return "\n".join(items)

    if node_type == "orderedList":
        items = []
        for idx, item in enumerate(content, 1):
            if item.get("type") == "listItem":
                item_text = "".join(_get_text_from_nodes(item.get("content", [])))
                items.append(f"{idx}. {item_text}")
        return "\n".join(items)

    if node_type == "codeBlock":
        language = node.get("attrs", {}).get("language", "")
        text = "".join(_get_text_from_nodes(content))
        if language:
            return f"```{language}\n{text}\n```"
        return f"```\n{text}\n```"

    if node_type == "blockquote":
        text = "".join(_get_text_from_nodes(content))
        return "> " + text.replace("\n", "\n> ")

    if node_type == "table":
        rows = []
        for row in content:
            if row.get("type") == "tableRow":
                cells = []
                for cell in row.get("content", []):
                    cell_text = "".join(_get_text_from_nodes(cell.get("content", [])))
                    cells.append(cell_text)
                rows.append("| " + " | ".join(cells) + " |")
        return "\n".join(rows)

    if node_type == "inlineCard":
        attrs = node.get("attrs", {})
        url = attrs.get("url", "")
        if "browse" in url:
            match = re.search(r"/browse/([A-Z]+-\d+)", url)
            if match:
                return f"[{match.group(1)}]({url})"
        return f"[{url}]({url})"

    if node_type == "mention":
        attrs = node.get("attrs", {})
        display_name = attrs.get("displayName", "")
        return f"@{display_name}"

    if node_type == "emoji":
        attrs = node.get("attrs", {})
        short_name = attrs.get("shortName", "")
        return f":{short_name}:"

    if node_type == "hardBreak":
        return "\n"

    if node_type == "text":
        marks = node.get("marks", [])
        text = node.get("text", "")
        for mark in marks:
            if mark.get("type") == "strong":
                text = f"**{text}**"
            elif mark.get("type") == "em":
                text = f"_{text}_"
            elif mark.get("type") == "code":
                text = f"`{text}`"
            elif mark.get("type") == "link":
                href = mark.get("attrs", {}).get("href", "")
                text = f"[{text}]({href})"
        return text

    return "".join(_get_text_from_nodes(content))


def _get_text_from_nodes(nodes: list[dict[str, Any]]) -> list[str]:
    """Extract text from a list of ADF nodes."""
    result = []
    for node in nodes:
        if node.get("type") == "text":
            text = node.get("text", "")
            marks = node.get("marks", [])
            for mark in marks:
                if mark.get("type") == "strong":
                    text = f"**{text}**"
                elif mark.get("type") == "em":
                    text = f"_{text}_"
                elif mark.get("type") == "code":
                    text = f"`{text}`"
                elif mark.get("type") == "link":
                    href = mark.get("attrs", {}).get("href", "")
                    text = f"[{text}]({href})"
            result.append(text)
        elif node.get("type") == "mention":
            display_name = node.get("attrs", {}).get("displayName", "")
            result.append(f"@{display_name}")
        elif node.get("type") == "emoji":
            short_name = node.get("attrs", {}).get("shortName", "")
            result.append(f":{short_name}:")
        else:
            result.append(_convert_adf_node(node))
    return result


@source(
    name="jira",
    uri_patterns=[
        "atlassian.net/browse",
        "atlassian.net/jira",
        "atlassian.net/projects",
        "jira://",
    ],
    priority=15,
    description="Fetch from Jira — issues, projects, sprints, epics",
    auth={"type": "basic", "username_env": "JIRA_USER", "password_env": "JIRA_TOKEN"},
)
class JiraFetcher(BaseFetcher):
    """Fetcher for Jira API - issues, projects, sprints, and epics."""

    name = "jira"
    priority = 15

    def __init__(self, timeout: float = 30.0):
        super().__init__()
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        if not uri:
            return False
        lower_uri = uri.lower()
        return (
            "atlassian.net/browse" in lower_uri
            or "atlassian.net/jira" in lower_uri
            or "atlassian.net/projects" in lower_uri
            or lower_uri.startswith("jira://")
        )

    def _get_client(self, base_url: Optional[str] = None) -> AtlassianJira:
        if not ATLASSIAN_AVAILABLE:
            raise ImportError(
                "atlassian-python-api is not installed. Install with: pip install atlassian-python-api"
            )

        if self._auth_config:
            username = self._auth_config.get_username()
            password = self._auth_config.get_password()
            token = self._auth_config.get_token()
        else:
            username = None
            password = None
            token = None

        if not username and not token:
            raise FetchError(
                "jira:// or https://company.atlassian.net/browse/...",
                "Jira requires authentication. Set JIRA_USER and JIRA_TOKEN environment variables.",
            )

        url = base_url or JIRA_CLOUD_URL

        if username and password:
            return AtlassianJira(
                url=url,
                username=username,
                password=password,
                api_version="3",
                cloud=True,
                timeout=self.timeout,
            )

        return AtlassianJira(
            url=url,
            token=token,
            api_version="3",
            cloud=True,
            timeout=self.timeout,
        )

    def _parse_description(self, fields: dict[str, Any]) -> Optional[TextDocument]:
        rendered = fields.get("renderedFields", {})
        desc = rendered.get("description") or fields.get("fields", {}).get("description")

        if not desc:
            return None

        if isinstance(desc, str):
            return TextDocument(
                source_uri="",
                content=desc,
                format=TextFormat.MARKDOWN,
            )

        if isinstance(desc, dict):
            markdown = convert_adf_to_markdown(desc)
            return TextDocument(
                source_uri="",
                content=markdown,
                format=TextFormat.MARKDOWN,
            )

        return None

    def _parse_comments(self, issue_key: str, client: AtlassianJira) -> list[TextDocument]:
        try:
            comments = client.get_comments(issue_key)
            result = []
            for comment in comments.get("comments", []):
                body = comment.get("body", {})
                if isinstance(body, str):
                    content = body
                elif isinstance(body, dict):
                    content = convert_adf_to_markdown(body)
                else:
                    content = ""

                result.append(
                    TextDocument(
                        source_uri="",
                        content=content,
                        format=TextFormat.MARKDOWN,
                    )
                )
            return result
        except Exception:
            return []

    def _get_field_value(self, fields: dict[str, Any], field_names: list[str]) -> Any:
        for field_name in field_names:
            value = fields.get(field_name)
            if value is not None:
                return value
        return None

    def _build_issue(
        self,
        issue_data: dict[str, Any],
        base_url: str,
        include_comments: bool = True,
        client: Optional[AtlassianJira] = None,
    ) -> JiraIssue:
        fields = issue_data.get("fields", {})
        key = issue_data.get("key", "")
        issue_id = issue_data.get("id", "")

        status = fields.get("status", {})
        status_name = status.get("name", "Unknown") if isinstance(status, dict) else str(status)

        priority = None
        priority_data = fields.get("priority")
        if isinstance(priority_data, dict):
            priority = priority_data.get("name")

        assignee = None
        assignee_data = fields.get("assignee")
        if isinstance(assignee_data, dict):
            assignee = assignee_data.get("displayName")

        reporter = None
        reporter_data = fields.get("reporter")
        if isinstance(reporter_data, dict):
            reporter = reporter_data.get("displayName")

        labels = fields.get("labels", [])

        components = []
        for comp in fields.get("components", []):
            if isinstance(comp, dict):
                components.append(comp.get("name", ""))

        fix_versions = []
        for fv in fields.get("fixVersions", []):
            if isinstance(fv, dict):
                fix_versions.append(fv.get("name", ""))

        story_points = None
        sp = self._get_field_value(fields, STORY_POINT_FIELDS)
        if sp is not None:
            try:
                story_points = float(sp)
            except (ValueError, TypeError):
                pass

        sprint = None
        sprint_data = fields.get(SPRINT_FIELD)
        if isinstance(sprint_data, dict):
            sprint = sprint_data.get("name")

        epic_key = None
        epic_name = None
        epic_link = fields.get(EPIC_LINK_FIELD)
        if epic_link:
            epic_key = epic_link

        issue_type = fields.get("issuetype", {})
        issue_type_name = issue_type.get("name", "Task") if isinstance(issue_type, dict) else "Task"

        created_str = fields.get("created")
        updated_str = fields.get("updated")
        resolved_str = fields.get("resolutiondate")

        created_at = datetime.now()
        if created_str:
            try:
                created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        updated_at = datetime.now()
        if updated_str:
            try:
                updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        resolved_at = None
        if resolved_str:
            try:
                resolved_at = datetime.fromisoformat(resolved_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        description = self._parse_description(issue_data)

        comments = []
        if include_comments and client:
            comments = self._parse_comments(key, client)

        return JiraIssue(
            issue_key=key,
            issue_id=issue_id,
            issue_type=issue_type_name,
            title=fields.get("summary", ""),
            description=description,
            comments=comments,
            status=status_name,
            priority=priority,
            assignee=assignee,
            reporter=reporter,
            labels=labels,
            components=components,
            fix_versions=fix_versions,
            sprint=sprint,
            epic_key=epic_key,
            epic_name=epic_name,
            story_points=story_points,
            created_at=created_at,
            updated_at=updated_at,
            resolved_at=resolved_at,
            url=f"{base_url}/browse/{key}",
        )

    async def _fetch_issue(
        self,
        issue_key: str,
        base_url: str,
        include_comments: bool = True,
        **kwargs: Any,
    ) -> JiraIssue:
        client = self._get_client(base_url)
        issue_data = client.issue(issue_key)
        return self._build_issue(issue_data, base_url, include_comments, client)

    async def _fetch_project(
        self,
        project_key: str,
        base_url: str,
        max_issues: int = 100,
        include_comments: bool = False,
        include_subtasks: bool = False,
        status_filter: Optional[list[str]] = None,
        label_filter: Optional[list[str]] = None,
        jql: Optional[str] = None,
        **kwargs: Any,
    ) -> JiraProject:
        client = self._get_client(base_url)

        project_data = client.project(project_key)
        if not project_data:
            raise SourceNotFoundError(f"Project not found: {project_key}")

        project_id = project_data.get("id", "")
        project_name = project_data.get("name", "")
        project_type = project_data.get("projectTypeKey", "software")

        lead = None
        lead_data = project_data.get("lead")
        if lead_data and isinstance(lead_data, dict):
            lead = lead_data.get("displayName")

        description = None
        desc_text = project_data.get("description")
        if desc_text:
            description = TextDocument(
                source_uri="",
                content=str(desc_text),
                format=TextFormat.PLAIN,
            )

        if jql:
            search_jql = jql
        else:
            filters = [f"project = {project_key}"]
            if not include_subtasks:
                filters.append("issuetype NOT IN (Subtask)")
            if status_filter:
                status_list = ", ".join([f'"{s}"' for s in status_filter])
                filters.append(f"status IN ({status_list})")
            if label_filter:
                label_list = ", ".join([f'"{label}"' for label in label_filter])
                filters.append(f"labels IN ({label_list})")
            search_jql = " AND ".join(filters) + " ORDER BY created DESC"

        issues = []
        start_at = 0
        while len(issues) < max_issues:
            chunk = client.jql(
                search_jql,
                start=start_at,
                limit=min(100, max_issues - len(issues)),
                fields=[
                    "summary",
                    "status",
                    "issuetype",
                    "priority",
                    "assignee",
                    "reporter",
                    "labels",
                    "components",
                    "fixVersions",
                    "created",
                    "updated",
                    "resolutiondate",
                    "description",
                    "comment",
                ],
            )

            for issue in chunk.get("issues", []):
                issues.append(self._build_issue(issue, base_url, include_comments=False))

            if not chunk.get("issues") or len(chunk["issues"]) < 100:
                break
            start_at += 100

        return JiraProject(
            project_key=project_key,
            project_id=project_id,
            name=project_name,
            description=description,
            project_type=project_type,
            lead=lead,
            items=issues,
            issue_count=len(issues),
            url=f"{base_url}/projects/{project_key}",
            source_uri=f"jira://project/{project_key}",
            source_name=self.name,
            item_count=len(issues),
            fetched_fully=len(issues) < max_issues,
            next_page_token=None,
        )

    async def _fetch_sprint(
        self,
        sprint_id: int,
        base_url: str,
        max_issues: int = 100,
        include_comments: bool = False,
        **kwargs: Any,
    ) -> JiraSprint:
        client = self._get_client(base_url)

        sprint_data = client.sprint(sprint_id)
        if not sprint_data:
            raise SourceNotFoundError(f"Sprint not found: {sprint_id}")

        name = sprint_data.get("name", f"Sprint {sprint_id}")
        state = sprint_data.get("state", "active")
        goal = None
        goal_text = sprint_data.get("goal")
        if goal_text:
            goal = TextDocument(
                source_uri="",
                content=goal_text,
                format=TextFormat.PLAIN,
            )

        board_id = sprint_data.get("boardId")

        start_date = None
        start_str = sprint_data.get("startDate")
        if start_str:
            try:
                start_date = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        end_date = None
        end_str = sprint_data.get("endDate")
        if end_str:
            try:
                end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        complete_date = None
        complete_str = sprint_data.get("completeDate")
        if complete_str:
            try:
                complete_date = datetime.fromisoformat(complete_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        issues = []
        issues_data = sprint_data.get("issues", [])
        for issue in issues_data[:max_issues]:
            issues.append(self._build_issue(issue, base_url, include_comments=False))

        return JiraSprint(
            sprint_id=sprint_id,
            name=name,
            state=state,
            goal=goal,
            board_id=board_id,
            start_date=start_date,
            end_date=end_date,
            complete_date=complete_date,
            items=issues,
            item_count=len(issues),
            source_uri=f"jira://sprint/{sprint_id}",
            source_name=self.name,
            fetched_fully=len(issues) < max_issues,
            next_page_token=None,
        )

    async def _fetch_epic(
        self,
        epic_key: str,
        base_url: str,
        max_issues: int = 100,
        include_comments: bool = False,
        **kwargs: Any,
    ) -> JiraEpic:
        client = self._get_client(base_url)

        issue_data = client.issue(epic_key)
        fields = issue_data.get("fields", {})

        issue_type = fields.get("issuetype", {})
        issue_type_name = issue_type.get("name", "") if isinstance(issue_type, dict) else ""
        if issue_type_name.lower() != "epic":
            raise SourceNotFoundError(f"Not an epic: {epic_key}")

        epic_id = issue_data.get("id", "")
        name = fields.get("summary", "")
        status = fields.get("status", {})
        status_name = status.get("name", "Unknown") if isinstance(status, dict) else str(status)

        assignee = None
        assignee_data = fields.get("assignee")
        if isinstance(assignee_data, dict):
            assignee = assignee_data.get("displayName")

        description = self._parse_description(issue_data)

        jql = f'"Epic Link" = {epic_key} ORDER BY created DESC'
        issues = []
        start_at = 0
        while len(issues) < max_issues:
            chunk = client.jql(
                jql,
                start=start_at,
                limit=min(100, max_issues - len(issues)),
                fields=[
                    "summary",
                    "status",
                    "issuetype",
                    "priority",
                    "assignee",
                    "reporter",
                    "labels",
                    "components",
                    "fixVersions",
                    "created",
                    "updated",
                    "resolutiondate",
                    "description",
                    "comment",
                ],
            )

            for issue in chunk.get("issues", []):
                issues.append(self._build_issue(issue, base_url, include_comments=False))

            if not chunk.get("issues") or len(chunk["issues"]) < 100:
                break
            start_at += 100

        return JiraEpic(
            epic_key=epic_key,
            epic_id=epic_id,
            name=name,
            summary=description,
            status=status_name,
            assignee=assignee,
            issues=issues,
            issue_count=len(issues),
            url=f"{base_url}/browse/{epic_key}",
        )

    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        route = parse_jira_uri(uri)

        base_url = kwargs.pop("base_url", "https://api.atlassian.com/ex/jira")

        if route.type == "issue":
            if not route.issue_key:
                raise SourceNotFoundError(f"Invalid issue URI: {uri}")
            include_comments = kwargs.get("include_comments", True)
            return await self._fetch_issue(route.issue_key, base_url, include_comments, **kwargs)
        elif route.type == "project":
            if not route.project_key:
                raise SourceNotFoundError(f"Invalid project URI: {uri}")
            return await self._fetch_project(route.project_key, base_url, **kwargs)
        elif route.type == "sprint":
            if not route.sprint_id:
                raise SourceNotFoundError(f"Invalid sprint URI: {uri}")
            return await self._fetch_sprint(route.sprint_id, base_url, **kwargs)
        elif route.type == "epic":
            if not route.epic_key:
                raise SourceNotFoundError(f"Invalid epic URI: {uri}")
            return await self._fetch_epic(route.epic_key, base_url, **kwargs)

        raise SourceNotFoundError(f"Unsupported Jira URI: {uri}")
