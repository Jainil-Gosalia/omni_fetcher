"""GitHub schemas for OmniFetcher."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from typing_extensions import Self

from pydantic import BaseModel, Field, model_validator

from omni_fetcher.schemas.atomics import TextDocument


EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".r": "r",
    ".lua": "lua",
    ".sh": "shell",
    ".bash": "bash",
    ".zsh": "zsh",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".log": "log",
    ".csv": "csv",
    ".tsv": "tsv",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".fs": "fsharp",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".vue": "vue",
    ".svelte": "svelte",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".dockerfile": "dockerfile",
    ".makefile": "makefile",
    "makefile": "makefile",
}


def detect_language(path: str) -> Optional[str]:
    """Detect programming language from file path."""
    lower_path = path.lower()
    for ext, lang in EXTENSION_TO_LANGUAGE.items():
        if lower_path.endswith(ext):
            return lang
    return None


class GitHubFile(BaseModel):
    """GitHub file representation."""

    path: str = Field(..., description="File path in repository")
    name: str = Field(..., description="File name")
    sha: str = Field(..., description="Git SHA of the file")
    size_bytes: int = Field(..., description="File size in bytes")
    branch: str = Field(default="main", description="Branch containing the file")
    content: TextDocument = Field(..., description="File content as TextDocument")
    language: Optional[str] = Field(None, description="Detected programming language")
    url: str = Field(..., description="Raw GitHub URL")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["github", "code", "file"]
        if self.language:
            tags.append(self.language)
        self.tags = tags


class GitHubIssue(BaseModel):
    """GitHub issue representation."""

    number: int = Field(..., description="Issue number")
    title: str = Field(..., description="Issue title")
    body: Optional[TextDocument] = Field(None, description="Issue body as TextDocument")
    state: str = Field(..., description="Issue state: open, closed")
    author: str = Field(..., description="Issue author username")
    labels: list[str] = Field(default_factory=list, description="Issue labels")
    comments: list[TextDocument] = Field(
        default_factory=list, description="Issue comments as TextDocuments"
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    url: str = Field(..., description="GitHub API URL for this issue")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["github", "issue", self.state]
        self.tags = tags


class GitHubPR(BaseModel):
    """GitHub pull request representation."""

    number: int = Field(..., description="PR number")
    title: str = Field(..., description="PR title")
    body: Optional[TextDocument] = Field(None, description="PR body as TextDocument")
    state: str = Field(..., description="PR state: open, closed, merged")
    author: str = Field(..., description="PR author username")
    base_branch: str = Field(..., description="Base branch name")
    head_branch: str = Field(..., description="Head branch name")
    labels: list[str] = Field(default_factory=list, description="PR labels")
    comments: list[TextDocument] = Field(
        default_factory=list, description="PR comments as TextDocuments"
    )
    diff: Optional[TextDocument] = Field(None, description="Unified diff as TextDocument")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    url: str = Field(..., description="GitHub API URL for this PR")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["github", "pull_request", self.state]
        self.tags = tags


class GitHubRelease(BaseModel):
    """GitHub release representation."""

    tag_name: str = Field(..., description="Release tag name")
    name: Optional[str] = Field(None, description="Release name")
    body: Optional[TextDocument] = Field(None, description="Release notes as TextDocument")
    author: str = Field(..., description="Release author username")
    draft: bool = Field(..., description="Whether this is a draft release")
    prerelease: bool = Field(..., description="Whether this is a prerelease")
    created_at: datetime = Field(..., description="Release creation timestamp")
    url: str = Field(..., description="GitHub API URL for this release")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._build_tags()

    def _build_tags(self) -> None:
        tags = ["github", "release"]
        self.tags = tags


class GitHubRepo(BaseModel):
    """GitHub repository container."""

    owner: str = Field(..., description="Repository owner")
    name: str = Field(..., description="Repository name")
    full_name: str = Field(..., description="Full name: owner/repo")
    description: Optional[str] = Field(None, description="Repository description")
    default_branch: str = Field(default="main", description="Default branch name")
    stars: int = Field(..., description="Star count")
    forks: int = Field(..., description="Fork count")
    language: Optional[str] = Field(None, description="Primary language")
    topics: list[str] = Field(default_factory=list, description="Repository topics")
    readme: Optional[TextDocument] = Field(None, description="README as TextDocument")
    files: list[GitHubFile] = Field(default_factory=list, description="Repository files")
    url: str = Field(..., description="GitHub URL for this repository")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from all items in repository."""
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["github", "repo"])
        if self.language:
            all_tags.add(self.language.lower())
        if self.readme:
            all_tags.update(self.readme.tags)
        for file in self.files:
            if file.tags:
                all_tags.update(file.tags)
        self.tags = sorted(all_tags)
        return self


class GitHubIssueContainer(BaseModel):
    """GitHub issues container."""

    repo: str = Field(..., description="Repository full name")
    state: str = Field(default="open", description="Issue state filter: open, closed, all")
    items: list[GitHubIssue] = Field(default_factory=list, description="List of issues")
    source_uri: str = Field(default="", description="Original URI")
    fetched_at: datetime = Field(default_factory=datetime.now, description="Fetch timestamp")
    source_name: str = Field(default="github", description="Source name")
    item_count: int = Field(0, ge=0, description="Total issue count")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from all issues."""
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["github", "issues"])
        for issue in self.items:
            if issue.tags:
                all_tags.update(issue.tags)
        self.tags = sorted(all_tags)
        return self


class GitHubReleaseContainer(BaseModel):
    """GitHub releases container."""

    repo: str = Field(..., description="Repository full name")
    items: list[GitHubRelease] = Field(default_factory=list, description="List of releases")
    source_uri: str = Field(default="", description="Original URI")
    fetched_at: datetime = Field(default_factory=datetime.now, description="Fetch timestamp")
    source_name: str = Field(default="github", description="Source name")
    item_count: int = Field(0, ge=0, description="Total release count")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from all releases."""
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["github", "releases"])
        for release in self.items:
            if release.tags:
                all_tags.update(release.tags)
        self.tags = sorted(all_tags)
        return self


class GitHubPRContainer(BaseModel):
    """GitHub pull requests container."""

    repo: str = Field(..., description="Repository full name")
    state: str = Field(default="open", description="PR state filter: open, closed, all")
    items: list[GitHubPR] = Field(default_factory=list, description="List of PRs")
    source_uri: str = Field(default="", description="Original URI")
    fetched_at: datetime = Field(default_factory=datetime.now, description="Fetch timestamp")
    source_name: str = Field(default="github", description="Source name")
    item_count: int = Field(0, ge=0, description="Total PR count")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    @model_validator(mode="after")
    def merge_tags(self) -> Self:
        """Merge tags from all PRs."""
        all_tags: set[str] = set(self.tags) if self.tags else set()
        all_tags.update(["github", "pull_requests"])
        for pr in self.items:
            if pr.tags:
                all_tags.update(pr.tags)
        self.tags = sorted(all_tags)
        return self
