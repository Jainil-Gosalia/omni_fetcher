"""Jira connector example for OmniFetcher."""

import asyncio
import os
from datetime import datetime

from omni_fetcher import OmniFetcher
from omni_fetcher.schemas.jira import (
    JiraEpic,
    JiraIssue,
    JiraProject,
    JiraSprint,
)
from omni_fetcher.schemas.atomics import TextDocument, TextFormat


async def main():
    print("=" * 60)
    print("Jira Connector Examples")
    print("=" * 60)

    token = os.environ.get("JIRA_TOKEN")
    if token:
        fetcher = OmniFetcher(auth={"jira": {"type": "bearer", "token_env": "JIRA_TOKEN"}})
        print("Using JIRA_TOKEN for authenticated requests")
    else:
        print("No JIRA_TOKEN found - examples will show schema structure only")
        fetcher = OmniFetcher()

    print("\n1. Fetch issue by URL")
    print("-" * 40)

    try:
        issue = await fetcher.fetch(
            "https://mycompany.atlassian.net/browse/ENG-123",
            base_url="https://mycompany.atlassian.com/ex/jira",
        )
        print(f"Issue Key: {issue.issue_key}")
        print(f"Issue Type: {issue.issue_type}")
        print(f"Title: {issue.title}")
        print(f"Status: {issue.status}")
        print(f"Priority: {issue.priority}")
        print(f"Assignee: {issue.assignee}")
        print(f"Reporter: {issue.reporter}")
        print(f"Story Points: {issue.story_points}")
        print(f"Labels: {issue.labels}")
        print(f"Tags: {issue.tags}")
        if issue.description:
            print(f"Description: {issue.description.content[:100]}...")
        print(f"Comments: {len(issue.comments)}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n2. Fetch issue by jira:// URI")
    print("-" * 40)

    try:
        issue = await fetcher.fetch(
            "jira://issue/ENG-456",
            base_url="https://mycompany.atlassian.com/ex/jira",
        )
        print(f"Issue Key: {issue.issue_key}")
        print(f"Status: {issue.status}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n3. Fetch project")
    print("-" * 40)

    try:
        project = await fetcher.fetch(
            "jira://project/ENG",
            base_url="https://mycompany.atlassian.com/ex/jira",
            max_issues=50,
        )
        print(f"Project Key: {project.project_key}")
        print(f"Project Name: {project.name}")
        print(f"Project Type: {project.project_type}")
        print(f"Lead: {project.lead}")
        print(f"Issue Count: {project.issue_count}")
        print(f"Tags: {project.tags}")
        for issue in project.items[:5]:
            print(f"  - {issue.issue_key}: {issue.title[:40]} ({issue.status})")
    except Exception as e:
        print(f"Error: {e}")

    print("\n4. Fetch project with JQL")
    print("-" * 40)

    try:
        project = await fetcher.fetch(
            "jira://project/ENG",
            base_url="https://mycompany.atlassian.com/ex/jira",
            jql="project = ENG AND issuetype = Bug AND priority = High ORDER BY created DESC",
            max_issues=20,
        )
        print(f"High priority bugs: {project.issue_count}")
        for issue in project.items:
            print(f"  - {issue.issue_key}: {issue.priority}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n5. Fetch sprint")
    print("-" * 40)

    try:
        sprint = await fetcher.fetch(
            "jira://sprint/42",
            base_url="https://mycompany.atlassian.com/ex/jira",
        )
        print(f"Sprint Name: {sprint.name}")
        print(f"State: {sprint.state}")
        if sprint.goal:
            print(f"Goal: {sprint.goal.content}")
        print(f"Issue Count: {sprint.item_count}")
        print(f"Tags: {sprint.tags}")
        in_progress = [i for i in sprint.items if "in_progress" in i.tags]
        print(f"In Progress: {len(in_progress)}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n6. Fetch epic")
    print("-" * 40)

    try:
        epic = await fetcher.fetch(
            "jira://epic/ENG-10",
            base_url="https://mycompany.atlassian.com/ex/jira",
        )
        print(f"Epic Key: {epic.epic_key}")
        print(f"Epic Name: {epic.name}")
        print(f"Status: {epic.status}")
        print(f"Assignee: {epic.assignee}")
        print(f"Issue Count: {epic.issue_count}")
        print(f"Tags: {epic.tags}")
        for issue in epic.issues[:5]:
            print(f"  - {issue.issue_key}: {issue.title[:40]} ({issue.story_points} pts)")
    except Exception as e:
        print(f"Error: {e}")

    print("\n7. Schema examples (without API calls)")
    print("-" * 40)

    issue_desc = TextDocument(
        source_uri="",
        content="This is a **critical** bug in the authentication flow",
        format=TextFormat.MARKDOWN,
    )
    jira_issue = JiraIssue(
        issue_key="ENG-123",
        issue_id="12345",
        issue_type="Bug",
        title="Login fails with OAuth provider",
        description=issue_desc,
        comments=[],
        status="In Progress",
        priority="Highest",
        assignee="Jane Doe",
        reporter="John Smith",
        labels=["security", "backend"],
        components=["auth"],
        fix_versions=["v2.1.0"],
        sprint="Sprint 42",
        epic_key="ENG-10",
        epic_name="User Authentication",
        story_points=5.0,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        resolved_at=None,
        url="https://mycompany.atlassian.net/browse/ENG-123",
    )
    print(f"JiraIssue tags: {jira_issue.tags}")

    jira_project = JiraProject(
        project_key="ENG",
        project_id="10000",
        name="Engineering",
        description=TextDocument(
            source_uri="",
            content="Engineering team projects",
            format=TextFormat.PLAIN,
        ),
        project_type="software",
        lead="Jane Doe",
        items=[jira_issue],
        issue_count=1,
        url="https://mycompany.atlassian.net/projects/ENG",
    )
    print(f"JiraProject tags: {jira_project.tags}")

    jira_sprint = JiraSprint(
        sprint_id=42,
        name="Sprint 42",
        state="active",
        goal=TextDocument(
            source_uri="",
            content="Complete authentication overhaul",
            format=TextFormat.PLAIN,
        ),
        board_id=1,
        start_date=datetime.now(),
        end_date=datetime.now(),
        complete_date=None,
        items=[jira_issue],
        item_count=1,
    )
    print(f"JiraSprint tags: {jira_sprint.tags}")

    jira_epic = JiraEpic(
        epic_key="ENG-10",
        epic_id="10010",
        name="User Authentication",
        summary=TextDocument(
            source_uri="",
            content="Epic to improve user authentication",
            format=TextFormat.PLAIN,
        ),
        status="In Progress",
        assignee="Jane Doe",
        issues=[jira_issue],
        issue_count=1,
        url="https://mycompany.atlassian.net/browse/ENG-10",
    )
    print(f"JiraEpic tags: {jira_epic.tags}")

    print("\n" + "=" * 60)
    print("Jira Connector supports:")
    print("  - Issues (by URL or jira:// URI)")
    print("  - Projects")
    print("  - Sprints")
    print("  - Epics")
    print("  - JQL queries")
    print("  - Status/label filtering")
    print("  - Bearer token auth via JIRA_TOKEN")
    print("")
    print("Auth options:")
    print("  Cloud: basic auth (email + API token)")
    print("  Self-hosted: bearer token + base_url")
    print("")
    print("Install: pip install omni_fetcher[jira]")


if __name__ == "__main__":
    asyncio.run(main())
