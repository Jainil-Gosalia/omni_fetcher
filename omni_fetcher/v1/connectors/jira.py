"""Jira connector for the OmniFetcher v1 canonical contract.

A Jira resource (an issue, an epic, a sprint, or a project) is fetched and
emitted as one canonical ``CompositionNode`` whose advisory ``kind`` is the
semantic label of the resource (``"issue"`` / ``"epic"`` / ``"sprint"`` /
``"project"``). The former ``JiraIssue`` / ``JiraEpic`` / ``JiraSprint`` /
``JiraProject`` shapes are gone: their *content* (description, comments,
sprint goal, project description) becomes ``Text`` atoms, while everything
*descriptive* (status, assignee, reporter, priority, sprint, story points,
timestamps, url, key, ...) lives in the metadata common core plus the
namespaced ``source_extra["jira"]`` mapping -- never inline on an atom.

The connector overrides only ``stream()``; the bounded ``fetch()`` sugar is
inherited from :class:`BaseFetcher`. A list resource (project / sprint / epic)
is emitted as a container node whose child item nodes are the individual
issues, so a single ``CompositionNode`` tree describes the whole resource.

Auth is resolved per call from the injected ``auth`` credential (a
``BasicAuth`` of ``email``:``api_token`` for Jira Cloud, or a ``BearerAuth``):
nothing is read from the ambient environment and nothing is stored on the
instance. The connector is deterministic and read-only.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

from omni_fetcher.v1.atoms import Text, TextFormat
from omni_fetcher.v1.auth import (
    AuthCredential,
    BasicAuth,
    BearerAuth,
    NormalizedAuthResolver,
)
from omni_fetcher.v1.errors import ErrorKind
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.result import (
    Result,
    error,
    from_exception,
    gap,
    partial,
    success,
)
from omni_fetcher.v1.zoom import ZoomSpec

try:
    from atlassian import Jira as AtlassianJira

    ATLASSIAN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the optional dep
    AtlassianJira = None  # type: ignore[assignment,misc]
    ATLASSIAN_AVAILABLE = False


# Source namespace for descriptive fields placed in ``source_extra``.
JIRA_NAMESPACE = "jira"

# Advisory semantic ``kind`` per resource.
ISSUE_KIND = "issue"
EPIC_KIND = "epic"
SPRINT_KIND = "sprint"
PROJECT_KIND = "project"

# Default Jira Cloud API base used when the caller supplies none.
JIRA_CLOUD_URL = "https://api.atlassian.com"

# Custom-field ids carrying story points across common Jira configurations.
STORY_POINT_FIELDS = [
    "story_points",
    "customfield_10016",
    "customfield_10028",
    "customfield_10026",
]
SPRINT_FIELD = "customfield_10020"
EPIC_LINK_FIELD = "customfield_10014"

# Default page size for paginated JQL searches.
_PAGE_SIZE = 100
_DEFAULT_MAX_ISSUES = 100


class JiraRoute:
    """
    A parsed Jira URI route
    ===============================================
    The decoded intent of a Jira URI: which resource kind it names and the
    key/id identifying it. Produced by :func:`parse_jira_uri`.
    ===============================================

    Attributes
    ----------
        type:
            One of ``"issue"`` / ``"project"`` / ``"sprint"`` / ``"epic"``.
        issue_key:
            The issue key (e.g. ``"PROJ-1"``) for an issue route.
        project_key:
            The project key for a project route.
        sprint_id:
            The numeric sprint id for a sprint route.
        epic_key:
            The epic key for an epic route.
    """

    def __init__(
        self,
        type: str,
        *,
        issue_key: Optional[str] = None,
        project_key: Optional[str] = None,
        sprint_id: Optional[int] = None,
        epic_key: Optional[str] = None,
    ) -> None:
        self.type = type
        self.issue_key = issue_key
        self.project_key = project_key
        self.sprint_id = sprint_id
        self.epic_key = epic_key


def parse_jira_uri(uri: str) -> JiraRoute:
    """
    Parse a Jira URI into a typed route

    Recognises both the ``jira://`` scheme (``jira://issue/PROJ-1``,
    ``jira://project/PROJ``, ``jira://sprint/42``, ``jira://epic/PROJ-9``)
    and Atlassian web URLs (``.../browse/PROJ-1``, ``.../projects/PROJ``).

    NOTE:
        1. Raises ``ValueError`` for a URI this connector cannot route; the
           caller maps that onto an ``INVALID_INPUT`` error value.

    Parameters
    ----------
        uri:
            The source URI to parse.

    Return
    ------
        route:
            The decoded :class:`JiraRoute`.
    """
    if uri.startswith("jira://"):
        path = uri[len("jira://"):].strip("/")
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


def convert_adf_to_markdown(adf_content: Any) -> str:
    """
    Convert Atlassian Document Format (ADF) content to markdown text

    Parameters
    ----------
        adf_content:
            An ADF document (``dict``) or an already-plain string.

    Return
    ------
        markdown:
            The rendered markdown text (empty for falsy input).
    """
    if not adf_content:
        return ""
    if isinstance(adf_content, str):
        return adf_content
    result = []
    for node in adf_content.get("content", []):
        result.append(_convert_adf_node(node))
    return "\n".join(result)


def _convert_adf_node(node: dict[str, Any]) -> str:
    """Convert a single ADF node into a markdown fragment."""
    node_type = node.get("type", "")
    content = node.get("content", [])

    if node_type == "paragraph":
        return "".join(_get_text_from_nodes(content))
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
        url = node.get("attrs", {}).get("url", "")
        if "browse" in url:
            match = re.search(r"/browse/([A-Z]+-\d+)", url)
            if match:
                return f"[{match.group(1)}]({url})"
        return f"[{url}]({url})"
    if node_type == "mention":
        return f"@{node.get('attrs', {}).get('displayName', '')}"
    if node_type == "emoji":
        return f":{node.get('attrs', {}).get('shortName', '')}:"
    if node_type == "hardBreak":
        return "\n"
    if node_type == "text":
        return _apply_marks(node.get("text", ""), node.get("marks", []))

    return "".join(_get_text_from_nodes(content))


def _apply_marks(text: str, marks: list[dict[str, Any]]) -> str:
    """Apply ADF text marks (strong/em/code/link) to a text fragment."""
    for mark in marks:
        mark_type = mark.get("type")
        if mark_type == "strong":
            text = f"**{text}**"
        elif mark_type == "em":
            text = f"_{text}_"
        elif mark_type == "code":
            text = f"`{text}`"
        elif mark_type == "link":
            href = mark.get("attrs", {}).get("href", "")
            text = f"[{text}]({href})"
    return text


def _get_text_from_nodes(nodes: list[dict[str, Any]]) -> list[str]:
    """Extract markdown text fragments from a list of ADF nodes."""
    result = []
    for node in nodes:
        node_type = node.get("type")
        if node_type == "text":
            result.append(_apply_marks(node.get("text", ""), node.get("marks", [])))
        elif node_type == "mention":
            result.append(f"@{node.get('attrs', {}).get('displayName', '')}")
        elif node_type == "emoji":
            result.append(f":{node.get('attrs', {}).get('shortName', '')}:")
        else:
            result.append(_convert_adf_node(node))
    return result


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse a Jira ISO-8601 timestamp into a datetime, or ``None``."""
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        # Jira emits RFC-822 style offsets (2026-01-15T09:00:00.000+0000);
        # fromisoformat only accepts the colon-less form from Python 3.11,
        # so insert the colon.
        if len(text) >= 5 and text[-5] in "+-" and text[-4:].isdigit():
            text = f"{text[:-2]}:{text[-2:]}"
        return datetime.fromisoformat(text)
    except (ValueError, AttributeError):
        return None


# Exceptions raised by the atlassian client / requests whose names we sniff
# for an HTTP status, since the optional dependency may not be importable for
# isinstance checks. We map on the status code carried on the exception.
def _status_of(exc: BaseException) -> Optional[int]:
    """Best-effort extraction of an HTTP status code from an exception."""
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    return None


def _kind_for_status(status: int) -> ErrorKind:
    """Map an HTTP status code onto the typed error taxonomy."""
    if status == 401:
        return ErrorKind.AUTH_FAILED
    if status == 403:
        return ErrorKind.PERMISSION_DENIED
    if status == 404:
        return ErrorKind.NOT_FOUND
    if status == 429:
        return ErrorKind.RATE_LIMITED
    if 500 <= status < 600:
        return ErrorKind.TRANSIENT
    return ErrorKind.TRANSIENT


class JiraConnector(BaseFetcher):
    """
    Jira connector for the v1 contract
    ===============================================
    Fetches a Jira issue, epic, sprint, or project and emits it as one
    canonical ``CompositionNode`` (advisory ``kind`` ``"issue"`` / ``"epic"``
    / ``"sprint"`` / ``"project"``). Description and comments become ``Text``
    atoms; descriptive fields live in the metadata core and
    ``source_extra["jira"]``. List resources are emitted as a container node
    with one child item node per issue.
    ===============================================
    NOTE:
        1. Expected failures are returned as typed ``Result`` values; HTTP
           statuses map onto the error taxonomy (401 -> auth, 403 ->
           permission, 404 -> not-found, 429 -> rate-limited, 5xx ->
           transient). A list whose individual issues partly failed is a
           ``partial`` carrying gaps, never a silent success.
        2. Credentials are resolved per call from ``auth`` and used
           transiently; nothing is read from the ambient environment and
           nothing is stored on the instance.
        3. Read-only and deterministic.

    Methods
    -------
        can_handle:
        stream:
    """

    def __init__(self, timeout: float = 30.0) -> None:
        """
        Create a Jira connector

        Parameters
        ----------
            timeout:
                Per-request timeout in seconds for the underlying client.
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
                ``True`` if the URI is a Jira ``jira://`` URI or an Atlassian
                browse/projects URL.
        """
        if not uri:
            return False
        lowered = uri.lower()
        return (
            "atlassian.net/browse" in lowered
            or "atlassian.net/jira" in lowered
            or "atlassian.net/projects" in lowered
            or lowered.startswith("jira://")
        )

    def _get_client(
        self,
        auth: Optional[AuthCredential],
        base_url: str,
    ) -> Any:
        """Build a per-call Jira client from the injected credential.

        Auth is resolved transiently from ``auth`` (a ``BasicAuth`` of
        ``email``:``api_token`` or a ``BearerAuth``); nothing is read from
        the ambient environment. Raises ``ValueError`` when no usable
        credential is supplied, and ``NotImplementedError`` when the optional
        ``atlassian`` dependency is missing -- both are caught and mapped to
        typed errors by ``stream()``.
        """
        if not ATLASSIAN_AVAILABLE:
            raise NotImplementedError(
                "atlassian-python-api is not installed. "
                "Install with: pip install atlassian-python-api"
            )
        # Resolve headers via the canonical resolver so behaviour matches the
        # rest of v1 (and to assert per-call auth in tests), then map onto the
        # atlassian client's own auth parameters.
        NormalizedAuthResolver().resolve_headers(auth)

        if isinstance(auth, BasicAuth):
            return AtlassianJira(
                url=base_url,
                username=auth.username,
                password=auth.password,
                api_version="3",
                cloud=True,
                timeout=self.timeout,
            )
        if isinstance(auth, BearerAuth):
            return AtlassianJira(
                url=base_url,
                token=auth.token,
                api_version="3",
                cloud=True,
                timeout=self.timeout,
            )
        raise ValueError(
            "Jira requires per-call auth (BasicAuth email:api_token or BearerAuth)."
        )

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        """
        Stream the canonical result for a Jira URI (the primitive)

        Routes the URI to an issue, epic, sprint, or project fetch and yields
        exactly one ``Result``: a ``success`` carrying the canonical node, a
        ``partial`` when a list resource's individual issues partly failed,
        or a typed ``error`` for a bad URI, missing/invalid credentials, or
        an HTTP failure mapped onto the taxonomy.

        NOTE:
            1. The stream is bounded: it yields one item and terminates, so
               the inherited ``fetch()`` returns that item directly.

        Parameters
        ----------
            uri:
                The Jira source URI (``jira://...`` or an Atlassian URL).
            auth:
                The per-call credential (``BasicAuth`` or ``BearerAuth``).
            zoom:
                Accepted for contract compatibility; the natural granularity
                is used.

        Return
        ------
            results:
                An async iterator yielding a single ``Result``.
        """
        base_url = f"{JIRA_CLOUD_URL}/ex/jira"

        try:
            route = parse_jira_uri(uri)
        except ValueError as exc:
            yield from_exception(
                exc,
                kind=ErrorKind.INVALID_INPUT,
                message="invalid Jira URI",
                locator=uri,
            )
            return

        try:
            client = self._get_client(auth, base_url)
        except NotImplementedError as exc:
            yield from_exception(
                exc, kind=ErrorKind.UNSUPPORTED, locator=uri
            )
            return
        except ValueError as exc:
            yield from_exception(
                exc, kind=ErrorKind.AUTH_FAILED, locator=uri
            )
            return

        try:
            yield self._dispatch(route, client, uri)
        except Exception as exc:  # noqa: BLE001 - mapped onto the taxonomy
            yield self._error_from(exc, uri)

    def _dispatch(self, route: JiraRoute, client: Any, uri: str) -> Result:
        """Route a parsed URI to the matching builder and return a Result."""
        if route.type == "issue":
            return self._fetch_issue(route.issue_key or "", client, uri)
        if route.type == "epic":
            return self._fetch_epic(route.epic_key or "", client, uri)
        if route.type == "sprint":
            return self._fetch_sprint(route.sprint_id or 0, client, uri)
        if route.type == "project":
            return self._fetch_project(route.project_key or "", client, uri)
        return error(
            kind=ErrorKind.INVALID_INPUT,
            message=f"Unsupported Jira URI: {uri}",
            locator=uri,
        )

    def _error_from(self, exc: BaseException, locator: str) -> Result:
        """Map a caught client exception onto a typed error value."""
        status = _status_of(exc)
        if status is not None:
            return from_exception(
                exc, kind=_kind_for_status(status), locator=locator
            )
        return from_exception(exc, kind=ErrorKind.TRANSIENT, locator=locator)

    # ------------------------------------------------------------------
    # Resource builders

    def _fetch_issue(self, issue_key: str, client: Any, uri: str) -> Result:
        """Fetch a single issue and build its canonical node."""
        issue_data = client.issue(issue_key)
        if not issue_data:
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"Issue not found: {issue_key}",
                locator=uri,
            )
        node = self._build_issue_node(issue_data, client, include_comments=True)
        return success(node)

    def _fetch_epic(self, epic_key: str, client: Any, uri: str) -> Result:
        """Fetch an epic and its child issues as a container node."""
        issue_data = client.issue(epic_key)
        if not issue_data:
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"Epic not found: {epic_key}",
                locator=uri,
            )
        fields = issue_data.get("fields", {})
        issue_type = fields.get("issuetype", {})
        type_name = issue_type.get("name", "") if isinstance(issue_type, dict) else ""
        if type_name.lower() != "epic":
            return error(
                kind=ErrorKind.INVALID_INPUT,
                message=f"Not an epic: {epic_key}",
                locator=uri,
            )

        jql = f'"Epic Link" = {epic_key} ORDER BY created DESC'
        children, gaps = self._search_children(client, jql)

        source_fields = self._issue_source_fields(issue_data)
        source_fields["url"] = f"{client.url}/browse/{epic_key}"
        source_fields["issue_count"] = len(children)

        node = build_node(
            kind=EPIC_KIND,
            atoms=self._content_atoms(issue_data, client, include_comments=False),
            children=children,
            id=epic_key,
            created=_parse_dt(fields.get("created")),
            updated=_parse_dt(fields.get("updated")),
            author=self._display_name(fields.get("reporter")),
            source_url=f"{client.url}/browse/{epic_key}",
            source_namespace=JIRA_NAMESPACE,
            source_fields=source_fields,
        )
        if gaps:
            return partial(node, gaps)
        return success(node)

    def _fetch_sprint(self, sprint_id: int, client: Any, uri: str) -> Result:
        """Fetch a sprint and its issues as a container node."""
        sprint_data = client.sprint(sprint_id)
        if not sprint_data:
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"Sprint not found: {sprint_id}",
                locator=uri,
            )

        children: list[CompositionNode] = []
        gaps = []
        for issue in sprint_data.get("issues", [])[:_DEFAULT_MAX_ISSUES]:
            try:
                children.append(
                    self._build_issue_node(issue, client, include_comments=False)
                )
            except Exception as exc:  # noqa: BLE001 - per-item gap
                gaps.append(
                    gap(
                        kind=ErrorKind.PARSE_ERROR,
                        locator=issue.get("key") if isinstance(issue, dict) else None,
                        detail=str(exc),
                    )
                )

        goal = sprint_data.get("goal")
        atoms = []
        if goal:
            atoms.append(Text(content=str(goal), format=TextFormat.PLAIN))

        source_fields = {
            "sprint_id": sprint_id,
            "name": sprint_data.get("name", f"Sprint {sprint_id}"),
            "state": sprint_data.get("state", "active"),
            "board_id": sprint_data.get("boardId"),
            "start_date": sprint_data.get("startDate"),
            "end_date": sprint_data.get("endDate"),
            "complete_date": sprint_data.get("completeDate"),
            "issue_count": len(children),
        }

        node = build_node(
            kind=SPRINT_KIND,
            atoms=atoms,
            children=children,
            id=str(sprint_id),
            created=_parse_dt(sprint_data.get("startDate")),
            updated=_parse_dt(sprint_data.get("completeDate")),
            source_namespace=JIRA_NAMESPACE,
            source_fields=source_fields,
        )
        if gaps:
            return partial(node, gaps)
        return success(node)

    def _fetch_project(self, project_key: str, client: Any, uri: str) -> Result:
        """Fetch a project and its issues as a container node."""
        project_data = client.project(project_key)
        if not project_data:
            return error(
                kind=ErrorKind.NOT_FOUND,
                message=f"Project not found: {project_key}",
                locator=uri,
            )

        jql = (
            f"project = {project_key} AND issuetype NOT IN (Subtask) "
            "ORDER BY created DESC"
        )
        children, gaps = self._search_children(client, jql)

        atoms = []
        desc = project_data.get("description")
        if desc:
            atoms.append(Text(content=str(desc), format=TextFormat.PLAIN))

        source_fields = {
            "project_key": project_key,
            "project_id": project_data.get("id", ""),
            "name": project_data.get("name", ""),
            "project_type": project_data.get("projectTypeKey", "software"),
            "lead": self._display_name(project_data.get("lead")),
            "url": f"{client.url}/projects/{project_key}",
            "issue_count": len(children),
        }

        node = build_node(
            kind=PROJECT_KIND,
            atoms=atoms,
            children=children,
            id=project_key,
            author=self._display_name(project_data.get("lead")),
            source_url=f"{client.url}/projects/{project_key}",
            source_namespace=JIRA_NAMESPACE,
            source_fields=source_fields,
        )
        if gaps:
            return partial(node, gaps)
        return success(node)

    def _search_children(
        self, client: Any, jql: str
    ) -> tuple[list[CompositionNode], list]:
        """Page a JQL search into child issue nodes, collecting any gaps."""
        children: list[CompositionNode] = []
        gaps: list = []
        start_at = 0
        while len(children) < _DEFAULT_MAX_ISSUES:
            chunk = client.jql(
                jql,
                start=start_at,
                limit=min(_PAGE_SIZE, _DEFAULT_MAX_ISSUES - len(children)),
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
                ],
            )
            issues = chunk.get("issues", []) if chunk else []
            for issue in issues:
                try:
                    children.append(
                        self._build_issue_node(
                            issue, client, include_comments=False
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - per-item gap
                    gaps.append(
                        gap(
                            kind=ErrorKind.PARSE_ERROR,
                            locator=(
                                issue.get("key")
                                if isinstance(issue, dict)
                                else None
                            ),
                            detail=str(exc),
                        )
                    )
            if not issues or len(issues) < _PAGE_SIZE:
                break
            start_at += _PAGE_SIZE
        return children, gaps

    # ------------------------------------------------------------------
    # Node assembly helpers

    def _build_issue_node(
        self,
        issue_data: dict[str, Any],
        client: Any,
        *,
        include_comments: bool,
    ) -> CompositionNode:
        """Assemble one canonical ``"issue"`` node from raw issue data."""
        fields = issue_data.get("fields", {})
        key = issue_data.get("key", "")

        source_fields = self._issue_source_fields(issue_data)
        source_fields["url"] = f"{client.url}/browse/{key}"

        return build_node(
            kind=ISSUE_KIND,
            atoms=self._content_atoms(
                issue_data, client, include_comments=include_comments
            ),
            id=key,
            created=_parse_dt(fields.get("created")),
            updated=_parse_dt(fields.get("updated")),
            author=self._display_name(fields.get("reporter")),
            source_url=f"{client.url}/browse/{key}",
            source_namespace=JIRA_NAMESPACE,
            source_fields=source_fields,
        )

    def _content_atoms(
        self,
        issue_data: dict[str, Any],
        client: Any,
        *,
        include_comments: bool,
    ) -> list[Text]:
        """Build the content ``Text`` atoms (description, then comments)."""
        atoms: list[Text] = []
        description = self._description_text(issue_data)
        if description is not None:
            atoms.append(description)
        if include_comments:
            key = issue_data.get("key", "")
            atoms.extend(self._comment_texts(key, client))
        return atoms

    @staticmethod
    def _description_text(issue_data: dict[str, Any]) -> Optional[Text]:
        """Render the issue description into a markdown ``Text`` atom."""
        rendered = issue_data.get("renderedFields", {})
        desc = rendered.get("description") if isinstance(rendered, dict) else None
        if not desc:
            desc = issue_data.get("fields", {}).get("description")
        if not desc:
            return None
        if isinstance(desc, str):
            content = desc
        elif isinstance(desc, dict):
            content = convert_adf_to_markdown(desc)
        else:
            return None
        return Text(content=content, format=TextFormat.MARKDOWN)

    @staticmethod
    def _comment_texts(issue_key: str, client: Any) -> list[Text]:
        """Render issue comments into markdown ``Text`` atoms."""
        try:
            comments = client.get_comments(issue_key)
        except Exception:  # noqa: BLE001 - comments are best-effort content
            return []
        result: list[Text] = []
        for comment in comments.get("comments", []):
            body = comment.get("body", {})
            if isinstance(body, str):
                content = body
            elif isinstance(body, dict):
                content = convert_adf_to_markdown(body)
            else:
                content = ""
            result.append(Text(content=content, format=TextFormat.MARKDOWN))
        return result

    @classmethod
    def _issue_source_fields(cls, issue_data: dict[str, Any]) -> dict[str, Any]:
        """Collect an issue's descriptive fields for ``source_extra``."""
        fields = issue_data.get("fields", {})

        status = fields.get("status", {})
        status_name = (
            status.get("name", "Unknown")
            if isinstance(status, dict)
            else str(status)
        )

        issue_type = fields.get("issuetype", {})
        type_name = (
            issue_type.get("name", "Task")
            if isinstance(issue_type, dict)
            else "Task"
        )

        components = [
            comp.get("name", "")
            for comp in fields.get("components", [])
            if isinstance(comp, dict)
        ]
        fix_versions = [
            fv.get("name", "")
            for fv in fields.get("fixVersions", [])
            if isinstance(fv, dict)
        ]

        sprint = None
        sprint_data = fields.get(SPRINT_FIELD)
        if isinstance(sprint_data, dict):
            sprint = sprint_data.get("name")

        return {
            "key": issue_data.get("key", ""),
            "issue_id": issue_data.get("id", ""),
            "issue_type": type_name,
            "title": fields.get("summary", ""),
            "status": status_name,
            "priority": cls._display_name(fields.get("priority"), attr="name"),
            "assignee": cls._display_name(fields.get("assignee")),
            "reporter": cls._display_name(fields.get("reporter")),
            "labels": fields.get("labels", []),
            "components": components,
            "fix_versions": fix_versions,
            "sprint": sprint,
            "epic_key": fields.get(EPIC_LINK_FIELD),
            "story_points": cls._story_points(fields),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "resolved": fields.get("resolutiondate"),
        }

    @staticmethod
    def _display_name(data: Any, *, attr: str = "displayName") -> Optional[str]:
        """Pull a display name (or other attr) off a Jira account/object dict."""
        if isinstance(data, dict):
            return data.get(attr)
        return None

    @staticmethod
    def _story_points(fields: dict[str, Any]) -> Optional[float]:
        """Extract a story-point value across known custom-field ids."""
        for name in STORY_POINT_FIELDS:
            value = fields.get(name)
            if value is not None:
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return None
        return None
