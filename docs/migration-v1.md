# Migration guide: source-specific schemas → canonical contract (v1.0)

As of **v1.0**, OmniFetcher no longer exposes source-specific public schema
classes (`GitHubIssue`, `NotionPage`, `JiraIssue`, `SlackMessage`, …). Every
connector now emits a single **canonical contract**: a `CompositionNode` tree of
typed **atoms**, wrapped in a `Result`, described by a uniform `FetchMetadata`
core plus a namespaced `source_extra` mapping for the long tail of
source-specific fields.

This guide maps each removed schema family onto that contract.

## The canonical contract

| Concept | Where it lives | Notes |
| --- | --- | --- |
| Content atoms | `omni_fetcher.v1.atoms` — `Text`, `Image`, `Audio`, `Video`, `Table` | The only content types. Leaf data. |
| Composition | `omni_fetcher.v1.node.CompositionNode` | A node holds atoms and/or child nodes + metadata. |
| Metadata core | `omni_fetcher.v1.metadata.FetchMetadata` | `id`, `kind`, `tags`, `created`, `updated`, `author`, `source_url`, `permissions`, `temporal`, `content_hash`, `prev_hash`. |
| Source-specific fields | `FetchMetadata.source_extra` | Namespaced: `{"github": {...}}`. Not content. |
| Semantic label | `FetchMetadata.kind` | Advisory string, e.g. `"issue"`, `"page"`, `"message"`, `"file"`. Never a distinct class. |
| Envelope | `omni_fetcher.v1.result.Result` (`Success` / partial / error) | Every fetch returns this. |

**Rule of thumb:** human-readable body → an atom; structured descriptive
attributes → `FetchMetadata` core (when they fit a core field) or
`source_extra["<source>"]` (everything else); the artifact's semantic type →
`kind`.

## Removed families → canonical mapping

Each removed class becomes a `CompositionNode` with a `kind` and its former
fields split between the metadata core and `source_extra["<source>"]`.

### GitHub (`GitHubIssue`, `GitHubPR`, `GitHubRelease`, `GitHubRepo`, `GitHubFile`, `*Container`)

| Old field | Canonical location |
| --- | --- |
| body / file contents | `Text` atom (code files: `Text(format=TextFormat.MARKDOWN/PLAIN)`) |
| title | `kind` + `source_extra["github"]["title"]` |
| number, state, labels, assignees, base/head refs | `source_extra["github"]` |
| author / user | `metadata.author` |
| html_url | `metadata.source_url` |
| created_at / updated_at | `metadata.created` / `metadata.updated` |
| `*Container` (issues, PRs, releases) | a parent `CompositionNode` with one child node per item; `kind="issue_list"` etc. |

`kind`: `"issue"`, `"pull_request"`, `"release"`, `"repo"`, `"file"`.

### Google (`GoogleDriveFile`, `GoogleDriveFolder`, `GoogleDriveContainer`, `GoogleSheetsSpreadsheet`, `GoogleDocsDocument`, `GoogleSlidesPresentation`)

| Old field | Canonical location |
| --- | --- |
| doc body | `Text` atom |
| sheet data | `Table` atom(s) |
| slides | child `CompositionNode` per slide, `Text` atoms inside |
| folder → children | parent `CompositionNode` with child nodes |
| mime_type, file id, owners, sharing | `source_extra["google"]` |
| web view link | `metadata.source_url` |

`kind`: `"file"`, `"folder"`, `"spreadsheet"`, `"document"`, `"presentation"`.

### Notion (`NotionPage`, `NotionDatabase`, `NotionBlock`, `NotionRichText`, `NotionUser`, `NotionProperty`)

| Old field | Canonical location |
| --- | --- |
| block content / rich text | `Text` atoms (blocks → child nodes when nested) |
| database rows | `Table` atom or child nodes |
| properties | `source_extra["notion"]["properties"]` |
| page id, parent, icon | `source_extra["notion"]` |
| created/last_edited time | `metadata.created` / `metadata.updated` |
| created_by / last_edited_by | `metadata.author` |

`kind`: `"page"`, `"database"`.

### Confluence (`ConfluencePage`, `ConfluenceSpace`, `ConfluenceAttachment`, `ConfluenceUser`, `ConfluenceComment`)

| Old field | Canonical location |
| --- | --- |
| page body (HTML→markdown) | `Text(format=TextFormat.MARKDOWN)` atom |
| attachments | child nodes with `Image`/binary atoms |
| comments | child nodes, `kind="comment"` |
| space → pages | parent `CompositionNode` |
| version, space key, ancestors | `source_extra["confluence"]` |

`kind`: `"page"`, `"space"`, `"attachment"`, `"comment"`.

### Slack (`SlackMessage`, `SlackThread`, `SlackChannel`, `SlackDM`)

| Old field | Canonical location |
| --- | --- |
| message text | `Text` atom |
| thread / channel → messages | parent `CompositionNode` with a child node per message |
| user | `metadata.author` |
| ts (timestamp) | `metadata.created` and `metadata.temporal` |
| reactions, channel id, thread_ts | `source_extra["slack"]` |

`kind`: `"message"`, `"thread"`, `"channel"`, `"dm"`.

### Jira (`JiraIssue`, `JiraEpic`, `JiraSprint`, `JiraProject`)

| Old field | Canonical location |
| --- | --- |
| description (ADF→text) | `Text` atom |
| summary | `kind` + `source_extra["jira"]["summary"]` |
| status, priority, issuetype, labels, story points | `source_extra["jira"]` |
| reporter / assignee | `metadata.author` + `source_extra["jira"]` |
| epic/sprint → issues | parent `CompositionNode` |

`kind`: `"issue"`, `"epic"`, `"sprint"`, `"project"`.

### Linear & SharePoint

Same pattern: body → atom, descriptive attributes → `source_extra["linear"]` /
`source_extra["sharepoint"]`, container types → parent `CompositionNode`,
semantic type → `kind`.

## Accessing your data now

```python
result = await omni.fetch("https://github.com/org/repo/issues/42", auth=...)
if isinstance(result, Success):
    node = result.tree
    kind = node.metadata.kind                 # "issue"
    title = node.metadata.source_extra["github"]["title"]
    body = "".join(a.content for a in node.iter_atoms())
    url = node.metadata.source_url
```

## Notes

- **Internal modules still exist.** The `omni_fetcher.schemas.<source>` modules
  remain on disk for the legacy fetchers' internal use, but they are **no longer
  part of the public API** and should not be imported by new code.
- **`content_hash`** is an opt-in Merkle content fingerprint — call
  `node.populate_hashes()` to fill it. `prev_hash` is reserved and never
  auto-populated. No verification logic ships in v1.
