"""GitHub connector example for OmniFetcher."""

import asyncio
import os
from datetime import datetime

from omni_fetcher import OmniFetcher
from omni_fetcher.schemas.github import (
    GitHubFile,
    GitHubIssue,
    GitHubRepo,
)
from omni_fetcher.schemas.atomics import TextDocument, TextFormat


async def main():
    print("=" * 60)
    print("GitHub Connector Examples")
    print("=" * 60)

    token = os.environ.get("GITHUB_TOKEN")
    if token:
        fetcher = OmniFetcher(auth={"github": {"type": "bearer", "token_env": "GITHUB_TOKEN"}})
        print("Using GITHUB_TOKEN for authenticated requests")
    else:
        fetcher = OmniFetcher()
        print("No GITHUB_TOKEN found - using rate-limited requests")

    print("\n1. Fetch repository metadata (lightweight)")
    print("-" * 40)

    try:
        repo = await fetcher.fetch("https://github.com/Jainil-Gosalia/omni_fetcher")
        print(f"Repository: {repo.full_name}")
        print(f"Description: {repo.description}")
        print(f"Stars: {repo.stars}")
        print(f"Forks: {repo.forks}")
        print(f"Language: {repo.language}")
        print(f"Topics: {repo.topics}")
        print(f"Tags: {repo.tags}")
        if repo.readme:
            print(f"README: {repo.readme.char_count} chars")
    except Exception as e:
        print(f"Error: {e}")

    print("\n2. Fetch repository releases")
    print("-" * 40)

    try:
        releases = await fetcher.fetch("https://github.com/Jainil-Gosalia/omni_fetcher/releases")
        print(f"Total releases: {releases.item_count}")
        for release in releases.items[:3]:
            print(f"  - {release.tag_name}: {release.name or 'Unnamed'}")
            print(f"    Author: {release.author}")
            print(f"    Draft: {release.draft}, Prerelease: {release.prerelease}")
            if release.body:
                print(f"    Notes: {release.body.content[:100]}...")
    except Exception as e:
        print(f"Error: {e}")

    print("\n3. Fetch file from repository")
    print("-" * 40)

    try:
        file = await fetcher.fetch(
            "https://github.com/Jainil-Gosalia/omni_fetcher/blob/master/README.md"
        )
        print(f"File: {file.name}")
        print(f"Path: {file.path}")
        print(f"Branch: {file.branch}")
        print(f"Size: {file.size_bytes} bytes")
        print(f"Language: {file.language}")
        print(f"SHA: {file.sha}")
        print(f"Tags: {file.tags}")
        print(f"Content preview: {file.content.content[:200]}...")
    except Exception as e:
        print(f"Error: {e}")

    print("\n4. Fetch Python file with language detection")
    print("-" * 40)

    try:
        py_file = await fetcher.fetch(
            "https://github.com/Jainil-Gosalia/omni_fetcher/blob/master/omni_fetcher/__init__.py"
        )
        print(f"File: {py_file.name}")
        print(f"Detected language: {py_file.language}")
        print(f"Content format: {py_file.content.format}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n5. Schema examples (without API calls)")
    print("-" * 40)

    file_content = TextDocument(
        source_uri="https://github.com/owner/repo/blob/main/main.py",
        content="def hello():\n    print('Hello, World!')",
        format=TextFormat.CODE,
        language="python",
    )
    gh_file = GitHubFile(
        path="main.py",
        name="main.py",
        sha="abc123def456",
        size_bytes=45,
        branch="main",
        content=file_content,
        language="python",
        url="https://github.com/owner/repo/blob/main/main.py",
    )
    print(f"GitHubFile tags: {gh_file.tags}")

    issue_body = TextDocument(
        source_uri="https://github.com/owner/repo/issues/1",
        content="This is a bug in the login flow",
        format=TextFormat.PLAIN,
    )
    gh_issue = GitHubIssue(
        number=1,
        title="Login bug",
        body=issue_body,
        state="open",
        author="bugfinder",
        labels=["bug", "urgent"],
        comments=[],
        created_at=datetime.now(),
        updated_at=datetime.now(),
        url="https://github.com/owner/repo/issues/1",
    )
    print(f"GitHubIssue tags: {gh_issue.tags}")

    gh_repo = GitHubRepo(
        owner="owner",
        name="myrepo",
        full_name="owner/myrepo",
        description="A cool project",
        default_branch="main",
        stars=150,
        forks=25,
        language="Python",
        topics=["python", "api"],
        readme=TextDocument(
            source_uri="https://github.com/owner/myrepo/blob/main/README.md",
            content="# My Repo",
            format=TextFormat.MARKDOWN,
        ),
        files=[gh_file],
        url="https://github.com/owner/myrepo",
    )
    print(f"GitHubRepo tags (merged): {gh_repo.tags}")

    print("\n" + "=" * 60)
    print("GitHub Connector supports:")
    print("  - Repositories (metadata, README, files)")
    print("  - Issues (single, list)")
    print("  - Pull Requests (single, list)")
    print("  - Releases")
    print("  - Files with language detection")
    print("  - Bearer token auth via GITHUB_TOKEN")


if __name__ == "__main__":
    asyncio.run(main())
