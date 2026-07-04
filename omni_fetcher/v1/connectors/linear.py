"""Linear connector for the OmniFetcher v1 canonical contract.

Linear is queried over GraphQL. Each resource (an issue, a team, a project,
a cycle) is re-expressed as a single canonical ``CompositionNode`` whose
advisory ``kind`` is the semantic label (``"issue"``, ``"team"``,
``"project"``, ``"cycle"``). The connector overrides only ``stream()``;
``fetch()`` sugar is inherited from :class:`BaseFetcher`.

The former ``LinearIssue`` / ``LinearTeam`` / ``LinearProject`` /
``LinearCycle`` types are gone. The split is:

- *Content* (an issue/team/project description, an issue's comments) becomes
  ``Text`` atoms attached to the node -- content only.
- *Descriptive* fields (state, state_type, priority, priority_label, team,
  project, cycle, assignee, creator, labels, estimate, timestamps, url,
  identifier, progress, ...) live in the metadata common core plus the
  namespaced ``source_extra["linear"]`` mapping -- never inline on an atom.

A list resource (a team, project or cycle and the issues it contains) maps
onto a *container* node whose child item nodes are the individual issues.

GraphQL is unusual: a successful HTTP 200 can still carry an ``errors``
array. The contract never hides them:

- ``errors`` with no ``data`` -> a typed ``error`` result.
- ``data`` present *and* ``errors`` present -> a ``partial`` whose tree
  carries the partial data and whose ``gaps`` record every GraphQL error.
- ``data`` present with no ``errors`` -> a ``success``.

HTTP transport failures map onto the typed taxonomy (401 -> AUTH_FAILED,
403 -> PERMISSION_DENIED, 404 -> NOT_FOUND, 429 -> RATE_LIMITED, 5xx ->
TRANSIENT). Credentials are injected per call via ``auth`` and resolved with
``NormalizedAuthResolver``; nothing is read from the ambient environment and
nothing is stored on the instance. The connector is deterministic and
read-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

import httpx

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import AuthCredential, NormalizedAuthResolver
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Error,
    Gap,
    Result,
    error,
    from_exception,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

# The GraphQL endpoint every Linear query is POSTed to.
LINEAR_API_URL = "https://api.linear.app/graphql"

# Source namespace for descriptive fields placed in ``source_extra``.
LINEAR_NAMESPACE = "linear"

# Advisory semantic ``kind`` per Linear resource.
KIND_ISSUE = "issue"
KIND_TEAM = "team"
KIND_PROJECT = "project"
KIND_CYCLE = "cycle"

# Cap on the number of issues pulled into a list (team/project/cycle) node.
_MAX_ISSUES = 100

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

ISSUE_IDENTIFIER_PATTERN = re.compile(r"^[A-Z]+-\d+$")

# Linear's integer priority -> human label.
_PRIORITY_LABELS = {
    0: "No priority",
    1: "Urgent",
    2: "High",
    3: "Medium",
    4: "Low",
}


def _parse_datetime(value: Any) -> Optional[datetime]:
    """Parse an ISO datetime string into a ``datetime``, else ``None``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_date(value: Any) -> Optional[date]:
    """Parse an ISO date string into a ``date``, else ``None``."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, AttributeError):
        return None


def _get_nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely read a nested mapping value, returning ``default`` if absent."""
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current if current is not None else default


@dataclass
class LinearRoute:
    """Parsed Linear URI route.

    Attributes
    ----------
        type:
            The resource type (``issue`` / ``team`` / ``project`` /
            ``cycle``).
        identifier:
            The issue id or identifier, when ``type`` is ``issue``.
        team_key:
            The team key, when carried by an issue URL.
        key:
            The team key, when ``type`` is ``team``.
        project_id:
            The project id, when ``type`` is ``project``.
        cycle_id:
            The cycle id, when ``type`` is ``cycle``.
    """

    type: str
    identifier: Optional[str] = None
    team_key: Optional[str] = None
    key: Optional[str] = None
    project_id: Optional[str] = None
    cycle_id: Optional[str] = None


def parse_linear_uri(uri: str) -> LinearRoute:
    """
    Parse a Linear URI into a typed route

    Recognises both the ``linear://`` custom scheme and ``linear.app`` web
    URLs, classifying the URI as an issue, team, project or cycle.

    Parameters
    ----------
        uri:
            The Linear URI to classify.

    Return
    ------
        route:
            The parsed :class:`LinearRoute`.
    """
    if uri.startswith("linear://"):
        return _parse_scheme_uri(uri)
    if "linear.app" not in uri.lower():
        raise ValueError(f"Invalid Linear URI: {uri}")
    return _parse_web_uri(uri)


def _parse_scheme_uri(uri: str) -> LinearRoute:
    """Parse a ``linear://`` custom-scheme URI."""
    path = uri[len("linear://") :].strip("/")
    parts = path.split("/")

    if len(parts) >= 2:
        head, ident = parts[0], parts[1]
        if head == "issue":
            return LinearRoute(type=KIND_ISSUE, identifier=ident)
        if head == "team":
            return LinearRoute(type=KIND_TEAM, key=ident)
        if head == "project":
            return LinearRoute(type=KIND_PROJECT, project_id=ident)
        if head == "cycle":
            return LinearRoute(type=KIND_CYCLE, cycle_id=ident)

    if len(parts) == 1 and parts[0]:
        token = parts[0]
        if UUID_PATTERN.match(token):
            return LinearRoute(type=KIND_ISSUE, identifier=token)
        if ISSUE_IDENTIFIER_PATTERN.match(token.upper()):
            team_key = token.rsplit("-", 1)[0]
            return LinearRoute(type=KIND_ISSUE, identifier=token, team_key=team_key)
        return LinearRoute(type=KIND_TEAM, key=token)

    raise ValueError(f"Invalid linear:// URI: {uri}")


def _parse_web_uri(uri: str) -> LinearRoute:
    """Parse an ``https://linear.app/...`` web URL."""
    if "/issue/" in uri:
        issue_part = uri.split("/issue/", 1)[1]
        identifier = issue_part.split("/")[0]
        team_key = None
        if "/team/" in uri:
            team_part = uri.split("/team/", 1)[1]
            team_key = team_part.split("/")[0]
        return LinearRoute(type=KIND_ISSUE, identifier=identifier, team_key=team_key)

    parts = urlparse(uri).path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "team":
        return LinearRoute(type=KIND_TEAM, key=parts[1])
    if len(parts) >= 2 and parts[0] == "project":
        return LinearRoute(type=KIND_PROJECT, project_id=parts[1])
    if len(parts) >= 2 and parts[0] == "cycle":
        return LinearRoute(type=KIND_CYCLE, cycle_id=parts[1])

    raise ValueError(f"Invalid Linear URI: {uri}")


# GraphQL query fragments. The issue-detail query pulls description + comments
# (the content surface); the list queries pull lighter issue rows.
_ISSUE_QUERY = """
    query Issue($id: String!) {
        issue(id: $id) {
            id identifier title description url priority estimate
            dueDate completedAt createdAt updatedAt
            parent { id }
            state { name type }
            team { id key name }
            assignee { name }
            creator { name }
            project { name }
            cycle { name }
            labels { nodes { name } }
            comments { nodes { body url } }
        }
    }
"""

_TEAM_QUERY = """
    query Team($id: String!) {
        team(id: $id) {
            id key name description
            issues(first: 100, orderBy: updatedAt) {
                nodes {
                    id identifier title url priority estimate
                    dueDate completedAt createdAt updatedAt
                    state { name type }
                    assignee { name }
                    creator { name }
                    project { name }
                    cycle { name }
                }
            }
        }
    }
"""

_PROJECT_QUERY = """
    query Project($id: String!) {
        project(id: $id) {
            id name description state progress url targetDate
            lead { name }
            issues(first: 100, orderBy: updatedAt) {
                nodes {
                    id identifier title url priority estimate
                    dueDate completedAt createdAt updatedAt
                    state { name type }
                    assignee { name }
                    creator { name }
                    team { id key name }
                    cycle { name }
                }
            }
        }
    }
"""

_CYCLE_QUERY = """
    query Cycle($id: String!) {
        cycle(id: $id) {
            id number name state progress startsAt endsAt completedAt
            team { name }
            issues(first: 100, orderBy: updatedAt) {
                nodes {
                    id identifier title url priority estimate
                    dueDate completedAt createdAt updatedAt
                    state { name type }
                    assignee { name }
                    creator { name }
                    team { id key name }
                    project { name }
                }
            }
        }
    }
"""


class LinearConnector(BaseFetcher):
    """
    Linear connector for the v1 contract
    ===============================================
    Fetches a Linear issue, team, project or cycle over GraphQL and emits it
    as a single canonical ``CompositionNode`` with the matching advisory
    ``kind``. Content (descriptions, comments) becomes ``Text`` atoms;
    descriptive fields live in the metadata core and
    ``source_extra["linear"]``. A list resource maps onto a container node
    with one child node per issue.
    ===============================================
    NOTE:
        1. A GraphQL ``errors`` array is never hidden: errors with no data
           become an ``error``; data alongside errors becomes a ``partial``
           with one gap per error.
        2. HTTP status failures map onto the typed taxonomy (auth,
           permission, not-found, rate-limit, transient).
        3. Credentials are injected per call via ``auth`` and used
           transiently; nothing is read from the environment or stored.

    Methods
    -------
        can_handle:
        stream:
    """

    def __init__(self, timeout: float = 30.0) -> None:
        """
        Create a Linear connector

        Parameters
        ----------
            timeout:
                Per-request HTTP timeout in seconds (default ``30.0``).
        """
        self.timeout = timeout

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """
        Report whether this connector handles a URI

        Parameters
        ----------
            uri:
                The source URI to test.

        Return
        ------
            handled:
                ``True`` if the URI is a Linear web URL or ``linear://`` URI.
        """
        if not uri:
            return False
        lowered = uri.lower()
        return "linear.app" in lowered or lowered.startswith("linear://")

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical result for a Linear URI

        Classifies the URI, executes one GraphQL query, and yields exactly
        one ``Result``: a ``success`` for clean data, a ``partial`` when data
        coexists with GraphQL errors, or a typed ``error`` for GraphQL errors
        without data, a missing resource, or an HTTP/transport failure.

        NOTE:
            1. The stream is bounded: it yields one item and terminates, so
               the inherited ``fetch()`` returns that item directly.

        Parameters
        ----------
            uri:
                The Linear URI (web URL or ``linear://``).
            auth:
                The per-call credential (a Linear API key as ``BearerAuth``
                or ``ApiKeyAuth``), or ``None``.
            zoom:
                Optional zoom spec; the natural granularity is used and
                ``zoom`` is accepted but not subdivided.

        Return
        ------
            results:
                An async iterator yielding a single ``Result``.
        """
        try:
            route = parse_linear_uri(uri)
        except ValueError as exc:
            yield from_exception(
                exc,
                kind=ErrorKind.INVALID_INPUT,
                message="invalid Linear URI",
                locator=uri,
            )
            return

        query, variables = self._query_for(route)
        if query is None:
            yield error(
                kind=ErrorKind.UNSUPPORTED,
                message=f"unsupported Linear route type: {route.type}",
                locator=uri,
            )
            return

        headers = {"Content-Type": "application/json"}
        headers.update(NormalizedAuthResolver().resolve_headers(auth))

        try:
            response = await self._post(query, variables, headers)
        except httpx.HTTPError as exc:
            yield from_exception(
                exc,
                kind=ErrorKind.TRANSIENT,
                message="Linear request failed",
                locator=uri,
            )
            return

        status_error = self._status_error(response, uri)
        if status_error is not None:
            yield status_error
            return

        yield self._build_result(route, uri, response)

    @staticmethod
    def _query_for(route: LinearRoute) -> tuple[Optional[str], dict[str, Any]]:
        """Pick the GraphQL query + variables for a parsed route."""
        if route.type == KIND_ISSUE:
            return _ISSUE_QUERY, {"id": route.identifier or ""}
        if route.type == KIND_TEAM:
            return _TEAM_QUERY, {"id": route.key or ""}
        if route.type == KIND_PROJECT:
            return _PROJECT_QUERY, {"id": route.project_id or ""}
        if route.type == KIND_CYCLE:
            return _CYCLE_QUERY, {"id": route.cycle_id or ""}
        return None, {}

    async def _post(
        self,
        query: str,
        variables: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        """Execute the single GraphQL POST and return the raw response."""
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            return await client.post(
                LINEAR_API_URL,
                json={"query": query, "variables": variables},
                headers=headers,
            )

    @staticmethod
    def _status_error(response: httpx.Response, locator: str) -> Optional[Error]:
        """Map a non-2xx HTTP status onto a typed error, else ``None``."""
        status = response.status_code
        if 200 <= status < 300:
            return None
        if status == 401:
            kind = ErrorKind.AUTH_FAILED
        elif status == 403:
            kind = ErrorKind.PERMISSION_DENIED
        elif status == 404:
            kind = ErrorKind.NOT_FOUND
        elif status == 429:
            kind = ErrorKind.RATE_LIMITED
        else:
            kind = ErrorKind.TRANSIENT
        return error(
            kind=kind,
            message=f"Linear API returned HTTP {status}",
            locator=locator,
        )

    def _build_result(
        self,
        route: LinearRoute,
        uri: str,
        response: httpx.Response,
    ) -> Result:
        """Map a 2xx GraphQL response body onto a canonical ``Result``.

        Routes on the presence of ``data`` / ``errors``: clean data is a
        ``success``, data-with-errors is a ``partial`` (one gap per GraphQL
        error), and errors-without-data is a typed ``error``. A missing
        resource node is ``NOT_FOUND``; an undecodable body is a parse error.
        """
        try:
            body = response.json()
        except (ValueError, TypeError) as exc:
            return from_exception(
                exc,
                kind=ErrorKind.PARSE_ERROR,
                message="Linear response was not valid JSON",
                locator=uri,
            )

        if not isinstance(body, dict):
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message="Linear response was not a JSON object",
                locator=uri,
            )

        data = body.get("data")
        errors = body.get("errors")
        has_errors = bool(errors)

        if has_errors and not data:
            return error(
                kind=ErrorKind.PARSE_ERROR,
                message=self._summarise_errors(errors),
                locator=uri,
            )

        node = self._node_for(route, uri, data if isinstance(data, dict) else {})
        if node is None:
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"Linear resource not found: {route.type}",
                locator=uri,
            )

        if has_errors:
            return partial(node, self._error_gaps(errors, uri))
        return success(node)

    def _node_for(
        self,
        route: LinearRoute,
        uri: str,
        data: dict[str, Any],
    ) -> Optional[CompositionNode]:
        """Build the canonical node for a resource, or ``None`` if absent."""
        if route.type == KIND_ISSUE:
            issue = data.get("issue")
            if not issue:
                return None
            return self._issue_node(issue, uri)
        if route.type == KIND_TEAM:
            team = data.get("team")
            if not team:
                return None
            return self._team_node(team, uri)
        if route.type == KIND_PROJECT:
            project = data.get("project")
            if not project:
                return None
            return self._project_node(project, uri)
        if route.type == KIND_CYCLE:
            cycle = data.get("cycle")
            if not cycle:
                return None
            return self._cycle_node(cycle, uri)
        return None

    @classmethod
    def _issue_node(
        cls,
        issue: dict[str, Any],
        uri: str,
        *,
        include_content: bool = True,
    ) -> CompositionNode:
        """Build the canonical node for a single issue.

        Description and comments become ``Text`` atoms (content); everything
        descriptive goes into the metadata core and ``source_extra["linear"]``.
        ``include_content`` is ``False`` for the lighter list-row issues which
        carry no description/comments in the list queries.
        """
        url = issue.get("url", "")
        atoms: list[Text] = []
        if include_content and issue.get("description"):
            atoms.append(Text(content=issue["description"], format=TextFormat.MARKDOWN))
        if include_content:
            for comment in _get_nested(issue, "comments", "nodes", default=[]):
                if comment and comment.get("body"):
                    atoms.append(
                        Text(
                            content=comment["body"],
                            format=TextFormat.PLAIN,
                        )
                    )

        priority = issue.get("priority", 0)
        labels = [
            label.get("name", "")
            for label in _get_nested(issue, "labels", "nodes", default=[])
            if label
        ]

        source_fields: dict[str, Any] = {
            "identifier": issue.get("identifier"),
            "title": issue.get("title"),
            "state": _get_nested(issue, "state", "name", default="Unknown"),
            "state_type": _get_nested(issue, "state", "type", default="unknown").lower(),
            "priority": priority,
            "priority_label": _PRIORITY_LABELS.get(priority, "Unknown"),
            "assignee": _get_nested(issue, "assignee", "name"),
            "team": _get_nested(issue, "team", "name"),
            "team_key": _get_nested(issue, "team", "key"),
            "project": _get_nested(issue, "project", "name"),
            "cycle": _get_nested(issue, "cycle", "name"),
            "labels": labels,
            "estimate": issue.get("estimate"),
            "due_date": cls._iso(_parse_date(issue.get("dueDate"))),
            "completed_at": cls._iso(_parse_datetime(issue.get("completedAt"))),
            "parent_id": _get_nested(issue, "parent", "id"),
            "url": url,
        }

        return build_node(
            kind=KIND_ISSUE,
            atoms=atoms,
            id=issue.get("id"),
            created=_parse_datetime(issue.get("createdAt")),
            updated=_parse_datetime(issue.get("updatedAt")),
            author=_get_nested(issue, "creator", "name"),
            source_url=url or uri,
            source_namespace=LINEAR_NAMESPACE,
            source_fields=cls._clean(source_fields),
        )

    @classmethod
    def _team_node(cls, team: dict[str, Any], uri: str) -> CompositionNode:
        """Build a container node for a team and its issues."""
        atoms: list[Text] = []
        if team.get("description"):
            atoms.append(Text(content=team["description"], format=TextFormat.PLAIN))
        issue_rows = _get_nested(team, "issues", "nodes", default=[])
        children = cls._issue_children(issue_rows, uri)

        source_fields = {
            "key": team.get("key"),
            "name": team.get("name"),
            "issue_count": len(issue_rows),
            "item_count": len(children),
        }
        return build_node(
            kind=KIND_TEAM,
            atoms=atoms,
            children=children,
            id=team.get("id"),
            source_url=uri,
            source_namespace=LINEAR_NAMESPACE,
            source_fields=cls._clean(source_fields),
        )

    @classmethod
    def _project_node(cls, project: dict[str, Any], uri: str) -> CompositionNode:
        """Build a container node for a project and its issues."""
        atoms: list[Text] = []
        if project.get("description"):
            atoms.append(Text(content=project["description"], format=TextFormat.PLAIN))
        issue_rows = _get_nested(project, "issues", "nodes", default=[])
        children = cls._issue_children(issue_rows, uri)
        completed = sum(
            1
            for row in issue_rows
            if row and _get_nested(row, "state", "type", default="").lower() == "completed"
        )

        source_fields = {
            "name": project.get("name"),
            "state": project.get("state", "unknown"),
            "progress": project.get("progress", 0.0),
            "lead": _get_nested(project, "lead", "name"),
            "target_date": cls._iso(_parse_date(project.get("targetDate"))),
            "issue_count": len(issue_rows),
            "completed_count": completed,
            "item_count": len(children),
            "url": project.get("url"),
        }
        return build_node(
            kind=KIND_PROJECT,
            atoms=atoms,
            children=children,
            id=project.get("id"),
            source_url=project.get("url") or uri,
            source_namespace=LINEAR_NAMESPACE,
            source_fields=cls._clean(source_fields),
        )

    @classmethod
    def _cycle_node(cls, cycle: dict[str, Any], uri: str) -> CompositionNode:
        """Build a container node for a cycle and its issues."""
        issue_rows = _get_nested(cycle, "issues", "nodes", default=[])
        children = cls._issue_children(issue_rows, uri)

        source_fields = {
            "number": cycle.get("number"),
            "name": cycle.get("name"),
            "team": _get_nested(cycle, "team", "name"),
            "state": cycle.get("state", "unknown"),
            "progress": cycle.get("progress", 0.0),
            "starts_at": cls._iso(_parse_datetime(cycle.get("startsAt"))),
            "ends_at": cls._iso(_parse_datetime(cycle.get("endsAt"))),
            "completed_at": cls._iso(_parse_datetime(cycle.get("completedAt"))),
            "item_count": len(children),
        }
        return build_node(
            kind=KIND_CYCLE,
            children=children,
            id=cycle.get("id"),
            source_url=uri,
            source_namespace=LINEAR_NAMESPACE,
            source_fields=cls._clean(source_fields),
        )

    @classmethod
    def _issue_children(cls, rows: list[Any], uri: str) -> list[CompositionNode]:
        """Build child issue nodes for a list resource, capped at the max."""
        children: list[CompositionNode] = []
        for row in rows[:_MAX_ISSUES]:
            if row:
                children.append(cls._issue_node(row, uri, include_content=False))
        return children

    @staticmethod
    def _iso(value: Optional[Any]) -> Optional[str]:
        """Render a date/datetime as an ISO string for ``source_extra``."""
        if value is None:
            return None
        return value.isoformat()

    @staticmethod
    def _clean(fields: dict[str, Any]) -> dict[str, Any]:
        """Drop ``None`` descriptive fields so source_extra stays tidy."""
        return {key: value for key, value in fields.items() if value is not None}

    @staticmethod
    def _summarise_errors(errors: Any) -> str:
        """Render a GraphQL ``errors`` array into one human-readable line."""
        if not isinstance(errors, list):
            return "Linear response carried errors"
        messages: list[str] = []
        for item in errors:
            if isinstance(item, dict):
                message = item.get("message")
                messages.append(str(message) if message else "unknown error")
            else:
                messages.append(str(item))
        joined = "; ".join(messages) if messages else "unknown error"
        return f"GraphQL errors: {joined}"

    @classmethod
    def _error_gaps(cls, errors: Any, locator: str) -> list[Gap]:
        """Map each GraphQL error into a typed ``Gap`` for a partial tree."""
        if not isinstance(errors, list) or not errors:
            return [
                gap(
                    kind=ErrorKind.PARSE_ERROR,
                    locator=locator,
                    detail=cls._summarise_errors(errors),
                )
            ]
        gaps: list[Gap] = []
        for item in errors:
            if isinstance(item, dict):
                message = item.get("message")
                detail = str(message) if message else "unknown error"
            else:
                detail = str(item)
            gaps.append(
                gap(
                    kind=ErrorKind.PARSE_ERROR,
                    locator=locator,
                    detail=detail,
                )
            )
        return gaps
