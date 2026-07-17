---
name: omni-fetcher
description: Fetch content from any source (Jira, Slack, GitHub, Confluence, Notion, Linear, S3, PDF/DOCX/PPTX/CSV, HTTP/JSON/GraphQL/RSS, YouTube, Elasticsearch, Kafka, log tails, WebSocket/SSE) via the omni-fetcher Python library or its CLI, and get one typed shape back every time. Use when writing code that pulls data from an external system or document, when the user names any of those sources, when they mention omni-fetcher / omnifetcher / OmniFetcher, or when building ingestion, RAG, or agent-context pipelines that need many sources behind one interface. Also use when writing a custom connector for a source omni-fetcher does not yet cover.
---

# OmniFetcher

Fetch anything — a Jira issue, a PDF, an S3 object, a Slack thread, a web page —
and get back the same typed shape every time. Code that walks a GitHub issue
walks a Confluence page unchanged.

## Install

```bash
pip install omni-fetcher
```

Core install covers HTTP, JSON, GraphQL, RSS, S3, PDF, CSV, YouTube, Slack,
Notion, SharePoint, Linear, GitHub, Google Drive, local files, tail, redis.
Extras: `office` (DOCX/PPTX), `jira`, `confluence`, `elasticsearch`, `kafka`,
`websockets` (WebSocket/SSE), `postgres` (Postgres CDC), `gdrive`, `web`.

Import from `omni_fetcher.v1` — never from `omni_fetcher` directly, which is the
frozen legacy v0.x API.

## The contract

Every connector returns a `Result`, one of three states:

```
Result
├── Success ── tree: CompositionNode
├── Partial ── tree + gaps: list[Gap]     # partial data, typed holes
└── Error ──── kind: ErrorKind + message  # returned as a value, never raised
                     │
CompositionNode ─────┘  exactly two fields:
├── metadata: Metadata        # id, kind, tags, created, updated, author,
│                             #   source_url + source_extra["<source>"]
└── children: list[NodeChild] # MIXED list, in document order:
                              #   CompositionNode | Text | Image | Audio | Video | Table
```

A node's content and its sub-nodes share one `children` list. Read that
carefully, because two plausible-looking guesses are wrong:

- **There is no `node.atoms`.** It does not exist; touching it is an
  `AttributeError`. Atoms live in `children`.
- **`children` is not a list of nodes.** A leaf's children are `Text`/`Image`/…
  atoms, which have no `.find_atoms()`, no `.children`, no `.metadata`. Never
  iterate `children` calling node methods without an `isinstance` check.

Use the accessors instead of touching `children` directly — they handle the
mixed list for you:

```python
node.find_atoms(AtomKind.TEXT)   # every text atom in the subtree, document order
node.find_by_kind("feed_item")   # descendant NODES with that advisory kind
node.iter_children()             # direct children — mixed, needs isinstance
```

Using the result is always the same three moves: **check the result state, walk
the tree, read the atoms.**

```python
import asyncio

from omni_fetcher.v1 import AtomKind, Success
from omni_fetcher.v1.connectors.local_file import LocalFileFetcher


async def main() -> None:
    result = await LocalFileFetcher().fetch("README.md")
    if isinstance(result, Success):
        for atom in result.tree.find_atoms(AtomKind.TEXT):
            print(atom.content[:100])


asyncio.run(main())
```

## Rules that matter

**Handle all three states.** Expected failures — missing resource, bad
credential, parse failure — come back as `Error` values; exceptions are reserved
for bugs. Never wrap a fetch in `try/except` to catch them, and never assume
`Success`. `isinstance` narrows the type for the checker:

```python
from omni_fetcher.v1 import Error, Partial, Success

result = await connector.fetch(uri)
if isinstance(result, Success):
    ...                                  # result.tree
elif isinstance(result, Partial):
    ...                                  # result.tree + result.gaps
elif isinstance(result, Error):
    ...                                  # result.kind, result.message
```

`ErrorKind`: `AUTH_FAILED`, `PERMISSION_DENIED`, `NOT_FOUND`, `UNSUPPORTED`,
`RATE_LIMITED`, `TRANSIENT`, `PARSE_ERROR`, `INVALID_INPUT`.

Don't discard `Partial` — it carries the data that *was* retrieved plus typed
`Gap`s naming what's missing. Treating it as failure throws away good data.

**Pass auth per call; never construct a connector with credentials.**
Connectors are stateless — that is what makes one process safe for many tenants.

```python
from omni_fetcher.v1 import BasicAuth, BearerAuth

await JiraConnector().fetch(
    "jira://issue/PROJ-1",
    auth=BasicAuth(username="dev@acme.io", password="api-token"),
)
await SlackConnector().fetch(
    "slack://channel/C0123456789",
    auth=BearerAuth(token="xoxb-..."),
)
```

Shapes: `BearerAuth(token=)`, `ApiKeyAuth(api_key=, header=)`,
`BasicAuth(username=, password=)`, `OAuth2Auth(access_token=)`,
`AwsAuth(access_key_id=, secret_access_key=)`. Read secrets from the
environment; never hardcode one and never log a credential.

**Read source-specific fields from `source_extra`, not from the core.** Fields
every source shares are typed on `Metadata` (`id`, `kind`, `tags`, `created`,
`updated`, `author`, `source_url`). Anything only one source has is namespaced:

```python
node.metadata.id                          # "PROJ-1"        — uniform
node.metadata.author                      # reporter name   — uniform
node.metadata.source_extra["jira"]["status"]   # Jira-only  — namespaced
```

**`metadata.kind` is advisory.** Labels like `"issue"`, `"page"`, `"feed_item"`
are hints for humans and heuristics. The tree shape is the contract — don't
branch program logic on `kind` where walking the tree would do.

**Route many URIs through one orchestrator.** Wire it once and share it across
threads and event loops; each call gets a fresh connector and its own auth.

```python
from omni_fetcher.v1 import BearerAuth, OmniFetcher, builtin_registry

omni = OmniFetcher(builtin_registry())    # every built-in, wired once

result = await omni.fetch(
    "https://github.com/psf/requests/issues/42",
    auth=BearerAuth(token=token),
    tags=["tenant-a"],
)
```

`builtin_registry()` resolves lazily and skips sources whose extra isn't
installed. An unrouted URI returns `Error(NOT_FOUND)` as a value. Reach for a
single connector class directly only when the source is fixed and known.

**Use `stream()` for unbounded sources, `fetch()` for everything else.**
`tail`, `kafka`, `redis`, `websocket`, `sse`, and `postgres_cdc` emit items
forever; calling `fetch()` on them returns `UNSUPPORTED`. Elasticsearch is the
reverse — it is bounded, so use `fetch()`.

```python
async for item in omni.stream("tail:///var/log/app.log?from=end"):
    if isinstance(item, Success):
        line = item.tree.find_atoms(AtomKind.TEXT)[0].content
        offset = item.tree.metadata.source_extra["tail"]["byte_offset"]
```

## Walking the tree

- `node.find_atoms(AtomKind.TEXT)` → atoms of one kind in the subtree
- `node.find_by_kind("feed_item")` → descendant nodes with that advisory kind
- `node.iter_descendants()` → nodes only; `node.iter_atoms()` → atoms only;
  `node.iter_children()` → direct children, mixed
- `node.merged_tags()` → tags inherited down the tree
- `node.populate_hashes()` → opt-in Merkle content hashes, then read
  `node.metadata.content_hash`

`AtomKind`: `TEXT`, `IMAGE`, `AUDIO`, `VIDEO`, `TABLE`. `Text` has `.content`;
`Table` has `.headers` and `.rows`.

### Getting the whole text out

The library has no tree→text helper — you write the join. It is one line, and
`find_atoms` already returns the whole subtree in document order:

```python
text = "\n\n".join(a.content for a in node.find_atoms(AtomKind.TEXT))
```

**Do not write `find_atoms(AtomKind.TEXT)[0].content` to "get the text."** It
returns the *first* atom, not the content. On a real README that is 15 of 18,022
characters — 0.1% — and it fails silently, with no error and plausible-looking
output. The `[0]` you see in docs is only ever valid for a one-atom-per-item
source, such as a `tail` or WebSocket message.

This matters most when feeding an LLM: a Jira issue's description and every
comment are separate atoms, so `[0]` hands the model the description alone and
the summary comes back confidently wrong.

## Zoom, retry, custom connectors, CLI

`ZoomSpec` picks semantic tree depth per atom type — it is structural
decomposition, never token windowing:

```python
from omni_fetcher.v1 import AtomKind, DepthLevel, ZoomSpec

spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.PARAGRAPH})
result = await omni.fetch("notes.md", zoom=spec)
```

`DepthLevel`: `NATURAL` (default), `WHOLE`, `SECTION`, `PARAGRAPH`, `SENTENCE`,
`MAX`. Coarser-than-natural always works; a finer level on an atom kind that
can't decompose records an honest gap rather than silently doing nothing.

Retry is a host decision, expressed over the typed errors —
`RATE_LIMITED`/`TRANSIENT` are what's worth retrying, and delivered data
(`Success`/`Partial`) is never retried:

```python
from omni_fetcher.v1 import RetryPolicy, fetch_with_retry, stream_with_restart

policy = RetryPolicy(max_attempts=4, initial_delay=0.5, jitter=0.2)
result = await fetch_with_retry(omni, uri, policy=policy, auth=auth)

async for item in stream_with_restart(omni, uri, policy=policy):
    ...       # resumes from each item's position in source_extra
```

CLI — credentials are passed as environment-variable *names*, so no secret
touches `argv`. Exit codes: 0 success/partial, 1 typed error, 2 usage.

```bash
omni-fetcher v1 fetch README.md
omni-fetcher v1 fetch "jira://issue/PROJ-1" \
  --auth-type basic --username-env JIRA_USER --password-env JIRA_TOKEN
omni-fetcher v1 fetch notes.md --zoom text=paragraph --json
omni-fetcher v1 stream "kafka://localhost:9092/events?offset=earliest" --json --max-items 100
```

For the connector table (URI shapes, auth, required extra), writing a custom
connector, and the v0.x → v1 migration, read `references/connectors.md`.
