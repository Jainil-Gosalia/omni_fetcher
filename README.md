# OmniFetcher

[![PyPI Version](https://img.shields.io/pypi/v/omni-fetcher.svg)](https://pypi.org/project/omni-fetcher/)
[![Python Versions](https://img.shields.io/pypi/pyversions/omni-fetcher.svg)](https://pypi.org/project/omni-fetcher/)
[![CI](https://github.com/Jainil-Gosalia/omni_fetcher/actions/workflows/ci.yml/badge.svg)](https://github.com/Jainil-Gosalia/omni_fetcher/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Fetch anything — a Jira issue, a PDF, an S3 object, a Slack thread, a web page —
and get back **the same typed shape every time**.

As of v1.0, every connector emits one canonical contract: a `Result` envelope
(`Success` / `Partial` / `Error`) wrapping a `CompositionNode` tree of typed
content atoms (`Text`, `Image`, `Audio`, `Video`, `Table`), a uniform metadata
core, and source-specific fields tucked into a namespaced `source_extra`. Code
that walks a GitHub issue walks a Confluence page unchanged.

```
Result
├── Success ── tree: CompositionNode
├── Partial ── tree + gaps: list[Gap]        # partial data, typed holes
└── Error ──── kind: ErrorKind + message     # returned, never raised
                     │
CompositionNode ─────┘
├── metadata: Metadata      # id, kind, tags, created, updated, author,
│                           # source_url, ... + source_extra["<source>"]
├── atoms: [Text | Image | Audio | Video | Table]   # content only
└── children: [CompositionNode, ...]                 # recursive
```

## Installation

```bash
pip install omni-fetcher
```

Some connectors need an extra:

| Extra | Enables | Install |
|-------|---------|---------|
| `office` | DOCX / PPTX parsing | `pip install "omni-fetcher[office]"` |
| `jira`, `confluence` | Atlassian API client | `pip install "omni-fetcher[jira,confluence]"` |
| `gdrive` | Google service-account auth (legacy fetcher) | `pip install "omni-fetcher[gdrive]"` |
| `web` | Better article extraction (legacy fetcher) | `pip install "omni-fetcher[web]"` |
| `dev` | Tests, lint, type checking | `pip install "omni-fetcher[dev]"` |

Everything else (HTTP, JSON, GraphQL, RSS, S3, PDF, CSV, YouTube, Slack,
Notion, SharePoint, Linear, GitHub, Google Drive REST, local files) works with
the core install.

## 60 seconds

Fetch a local file — no credentials, no network:

```python
import asyncio

from omni_fetcher.v1 import AtomKind, Success
from omni_fetcher.v1.connectors.local_file import LocalFileFetcher


async def main() -> None:
    result = await LocalFileFetcher().fetch("README.md")

    if isinstance(result, Success):
        node = result.tree                          # CompositionNode
        print(node.metadata.kind)                   # advisory label, e.g. "file"
        for atom in node.find_atoms(AtomKind.TEXT):
            print(atom.content[:100])


asyncio.run(main())
```

Every other connector returns the same shape, so everything below this point
is the same three moves: **check the result state, walk the tree, read the
atoms**.

## Examples

### Handle all three result states

Expected failures are returned as values, never raised. A feed that parsed
with warnings comes back `Partial` — the data it *did* get, plus typed gaps:

```python
from omni_fetcher.v1 import Error, Partial, Success
from omni_fetcher.v1.connectors.rss import RSSConnector

result = await RSSConnector().fetch("https://blog.python.org/feeds/posts/default?alt=rss")

if isinstance(result, Success):
    items = result.tree.find_by_kind("feed_item")
    print(f"{len(items)} items")
elif isinstance(result, Partial):
    print(f"partial: {len(result.tree.find_by_kind('feed_item'))} items, "
          f"{len(result.gaps)} gaps ({result.gaps[0].kind})")
elif isinstance(result, Error):
    print(f"{result.kind}: {result.message}")       # e.g. NOT_FOUND, AUTH_FAILED
```

### Authenticate per call — credentials are never stored

Connectors hold no state; you pass the credential with each call. This is what
makes one process safe for many tenants:

```python
from omni_fetcher.v1 import BasicAuth, BearerAuth, Success
from omni_fetcher.v1.connectors.jira import JiraConnector
from omni_fetcher.v1.connectors.slack import SlackConnector

# Jira Cloud: email + API token
result = await JiraConnector().fetch(
    "jira://issue/PROJ-1",
    auth=BasicAuth(username="dev@acme.io", password="api-token"),
)

# Slack: bot token
result = await SlackConnector().fetch(
    "slack://channel/C0123456789",
    auth=BearerAuth(token="xoxb-your-bot-token"),
)
```

Credential shapes: `BearerAuth(token=...)`, `ApiKeyAuth(api_key=..., header=...)`,
`BasicAuth(username=..., password=...)`, `OAuth2Auth(access_token=...)`,
`AwsAuth(access_key_id=..., secret_access_key=...)`.

### Source specifics live in `source_extra` — the core stays uniform

Descriptive fields every source shares (id, author, created, updated, tags,
source_url) are typed on `Metadata`. Fields only one source has live under its
namespace in `source_extra`, so they can't collide and can't leak into your
generic code:

```python
result = await JiraConnector().fetch("jira://issue/PROJ-1", auth=auth)
assert isinstance(result, Success)
node = result.tree

# Uniform core — identical for every source
print(node.metadata.id)            # "PROJ-1"
print(node.metadata.author)        # reporter's display name
print(node.metadata.created)       # datetime

# Jira-only fields, namespaced
extra = node.metadata.source_extra["jira"]
print(extra["status"], extra["priority"], extra["story_points"])
```

### Tabular sources produce `Table` atoms

```python
from omni_fetcher.v1 import AtomKind, Success
from omni_fetcher.v1.connectors.csv import CSVConnector

result = await CSVConnector().fetch("data/sales.csv")

assert isinstance(result, Success)
table = result.tree.find_atoms(AtomKind.TABLE)[0]
print(table.headers)               # ["region", "units", ...] or None
print(table.rows[:3])
```

### Wire the orchestrator once, serve every tenant

For hosts routing arbitrary URIs, wire a single stateless orchestrator with
all built-in connectors in one line and share it across threads and event
loops. Each call gets a fresh connector instance and its own credentials —
nothing is cached or shared between calls (proven by the concurrency suite
in `tests/v1/test_isolation.py`):

```python
from omni_fetcher.v1 import BearerAuth, OmniFetcher, builtin_registry

omni = OmniFetcher(builtin_registry())  # every built-in connector, wired once

# Per request — tenant A and tenant B can run concurrently on this instance
result = await omni.fetch(
    "https://github.com/psf/requests/issues/42",
    auth=BearerAuth(token=tenant_a_token),
    tags=["tenant-a"],
)
```

`builtin_registry()` resolves connector modules lazily, skips sources whose
optional extra isn't installed, and stays immutable after construction. An
unrouted URI returns `error(ErrorKind.NOT_FOUND)` — as a value. For a custom
routing table, build your own with `RegistryBuilder` + `SourceDefinition`
(both importable from `omni_fetcher.v1`).

### Zoom: pick the semantic depth

Zoom selects how deeply a source's natural structure is expanded, per atom
type — semantic tree depth, never token windowing:

```python
from omni_fetcher.v1 import AtomKind, DepthLevel, ZoomSpec

spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.PARAGRAPH})
result = await omni.fetch("notes.md", zoom=spec)
# one "paragraph" child node per text block; the pieces concatenate
# exactly to the natural content
```

Coarser-than-natural levels (`WHOLE`, `SECTION`) work for every connector;
finer text levels decompose in the text-bearing ones (pptx maps `SECTION`
onto its slides). A finer level explicitly requested for an atom kind that
can't decompose records an honest gap instead of a silent no-op.

### Retry transient failures — as values, not exceptions

Connectors classify failures uniformly (429 → `RATE_LIMITED`, 5xx/timeouts →
`TRANSIENT`), so retrying is a one-liner host decision:

```python
from omni_fetcher.v1 import RetryPolicy, fetch_with_retry

policy = RetryPolicy(max_attempts=4, initial_delay=0.5, jitter=0.2)
result = await fetch_with_retry(omni, uri, policy=policy, auth=auth)
```

The frozen policy is safe to share across tenants; delivered data
(`Success`/`Partial`) is never retried.

### Or skip Python entirely

```bash
omni-fetcher v1 fetch README.md
omni-fetcher v1 fetch "jira://issue/PROJ-1" \
  --auth-type basic --username-env JIRA_USER --password-env JIRA_TOKEN
omni-fetcher v1 fetch notes.md --zoom text=paragraph --json
```

Credentials are environment-variable *names* — no secret touches `argv`.
Exit codes: 0 success/partial, 1 typed error, 2 usage.

### Write your own connector

Subclass `BaseFetcher`, override `stream()`, build canonical nodes with the
mapping helper. `fetch()` comes for free (it collects the stream):

```python
from typing import AsyncIterator, Optional

from omni_fetcher.v1 import BaseFetcher, Text
from omni_fetcher.v1.auth import AuthCredential
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.result import Result, success
from omni_fetcher.v1.zoom import ZoomSpec


class HelloConnector(BaseFetcher):
    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom: Optional[ZoomSpec] = None,
    ) -> AsyncIterator[Result]:
        node = build_node(
            kind="greeting",                       # advisory label
            atoms=[Text(content=f"hello, {uri}")],
            source_namespace="hello",              # source_extra["hello"]
            source_fields={"lang": "en"},
        )
        yield success(node)


result = await HelloConnector().fetch("hello://world")
```

Register it with a `SourceDefinition` (see the orchestrator example) and it
routes like any built-in.

### Opt-in content fingerprints

Merkle content hashes are computed only when you ask:

```python
node.populate_hashes()             # bottom-up over the whole subtree
print(node.metadata.content_hash)  # stable fingerprint of content + children
```

## Connectors

| Connector (`omni_fetcher.v1.connectors.*`) | URI shapes | Auth | Extra |
|---|---|---|---|
| `local_file.LocalFileFetcher` | `/path/to/file`, `file://...` | — | — |
| `http_url.HTTPURLConnector` | `https://...` pages | — | — |
| `http_json.HTTPJSONConnector` | JSON APIs | optional `BearerAuth` | — |
| `http_auth.HTTPAuthConnector` | authenticated HTTP | `Bearer`/`ApiKey`/`Basic` | — |
| `graphql.GraphQLConnector` | GraphQL endpoints | optional | — |
| `rss.RSSConnector` | feed URLs | optional `BearerAuth` | — |
| `csv.CSVConnector` | `.csv` paths/URLs | — | — |
| `pdf.PDFConnector` | `.pdf` paths/URLs | — | — |
| `docx.DocxConnector` | `.docx` paths/URLs | — | `office` |
| `pptx.PptxConnector` | `.pptx` paths/URLs | — | `office` |
| `audio.AudioConnector` | audio paths/URLs | — | — |
| `youtube.YouTubeConnector` | `youtube.com`, `youtu.be` | — | — |
| `s3.S3Fetcher` | `s3://bucket/key` | `AwsAuth` | — |
| `github.GitHubConnector` | `github.com/owner/repo[/issues/N, /pull/N, ...]` | optional `BearerAuth` | — |
| `google_drive.GoogleDriveFetcher` | `drive.google.com`, `docs.google.com` | `OAuth2Auth` | — |
| `notion.NotionConnector` | Notion pages/databases | `BearerAuth` (integration token) | — |
| `jira.JiraConnector` | `jira://issue/KEY`, `jira://project/KEY`, `jira://sprint/N`, `jira://epic/KEY` | `BasicAuth` (Cloud) / `BearerAuth` (Server) | `jira` |
| `confluence.ConfluenceConnector` | Confluence pages/spaces | `BasicAuth` / `BearerAuth` | `confluence` |
| `slack.SlackConnector` | `slack://channel/ID`, threads, DMs | `BearerAuth` (bot token) | — |
| `sharepoint.SharePointConnector` | `sharepoint://site[/Library[/file]]` | `OAuth2Auth` (Graph token) | — |
| `linear.LinearConnector` | Linear issues/teams/projects | `BearerAuth` / `ApiKeyAuth` | — |
| `elasticsearch.ElasticsearchFetcher` | `es://host[:port]/index?q=&size=&scroll=&user=&password=&api_key=` | via URI query | `elasticsearch` |

`ElasticsearchFetcher` is bounded, not streaming: `fetch()` drives the scroll
API internally (page size capped, `?size=` bounds the total) and returns one
`Result` whose tree is a `search_results` container node with one
`json_document` child per matching document:

```python
result = await omni.fetch("es://search.example.com/logs?q=level:error&size=500")
if isinstance(result, Success):
    for doc in result.tree.children:
        print(doc.find_atoms(AtomKind.TEXT)[0].content)
```

A query matching zero documents is `Error(NOT_FOUND)`; a scroll failure
partway through returns a `Partial` with the documents collected so far.

### Streaming (unbounded) connectors

These emit an item per line/message forever — consume them with `stream()`,
not `fetch()` (which returns `UNSUPPORTED`):

| Connector | URI shapes | Auth | Extra |
|---|---|---|---|
| `tail.TailConnector` | `tail://<path>?from=end\|start\|<byte>&poll=<s>` | — | — |
| `kafka.KafkaConnector` | `kafka://host[:port]/topic?offset=latest\|earliest\|<n>&group=<id>` | — | `kafka` |
| `redis.RedisConnector` | `redis://host[:port]/stream-key?offset=$\|0\|<id>&group=<id>` | — | — |
| `websocket.WebSocketConnector` | `ws://host[:port]/path?token=&auth=&sequence=<n>` | via URI query | `websockets` |
| `sse.SSEConnector` | `sse://host[:port]/path?token=&auth=&sequence=<n>` | via URI query | `websockets` |
| `postgres_cdc.PostgresCDCConnector` | `postgres-cdc://host[:port]/database?slot=&user=&password=` | via URI query | `postgres` |

```python
from omni_fetcher.v1 import AtomKind, OmniFetcher, Success, builtin_registry

omni = OmniFetcher(builtin_registry())

async for item in omni.stream("tail:///var/log/app.log?from=end"):
    if isinstance(item, Success):
        line = item.tree.find_atoms(AtomKind.TEXT)[0].content
        offset = item.tree.metadata.source_extra["tail"]["byte_offset"]
        print(offset, line)
```

Each item carries its resume position in `source_extra` (tail `byte_offset`,
kafka `partition`/`offset`, websocket/sse `sequence`, postgres `slot`). A dropped stream
(rotated file, broker blip, closed socket) ends with a typed
`Error(TRANSIENT)`; `stream_with_restart` resumes it from the last position:

```python
from omni_fetcher.v1 import RetryPolicy, stream_with_restart

async for item in stream_with_restart(
    omni, "tail:///var/log/app.log?from=end", policy=RetryPolicy(max_attempts=5)
):
    ...  # seamless across restarts; the resume ?from=/​?offsets= is derived for you
```

Kafka is stateless by default (no consumer group, no commits — start from
`?offset=` and resume via the per-message positions); `?group=<id>` opts into
committing consumer-group semantics that the host owns.

WebSocket/SSE messages are always plain `Text` (no JSON parsing — that's a
host-side concern); auth travels as `?token=<value>` or `?auth=Bearer+<token>`
in the URI, and `?sequence=<n>` seeds/resumes numbering since these sources
are ephemeral (a message lost while disconnected cannot be recovered, but
resume prevents duplicates).

Postgres CDC (`postgres-cdc://`) streams row-level INSERT/UPDATE/DELETE
changes via logical replication — each change is one `Result` whose `Text`
atom is JSON `{op, table, new, old, lsn, timestamp, xid}`. The connector
creates and drops its replication slot itself (`?slot=` to name it); after a
transport failure the slot is kept so `stream_with_restart` resumes from its
`confirmed_flush_lsn` with no change loss. Requires `wal_level=logical` and
the `postgres` extra (`pip install "omni-fetcher[postgres]"`):

```bash
omni-fetcher v1 stream "tail:///var/log/app.log?from=end" --max-items 100
omni-fetcher v1 stream "kafka://localhost:9092/events?offset=earliest" --json
omni-fetcher v1 stream "ws://live.example.com/events?token=abc" --json
omni-fetcher v1 stream "sse://events.example.com/live?auth=Bearer+tok" --json
omni-fetcher v1 stream "postgres-cdc://db.example.com/mydb?user=repl&password=…" --max-items 5
```

## Agent skill

If you drive OmniFetcher from Claude Code or another agentic system, install the
bundled skill and the agent knows the contract — result states, per-call auth,
`source_extra`, streaming vs. bounded connectors — without you re-explaining it:

```bash
cp -r .claude/skills/omni-fetcher ~/.claude/skills/
```

See [.claude/skills/omni-fetcher/README.md](.claude/skills/omni-fetcher/README.md)
for project-scoped and non-Claude-Code installs.

## Design guarantees

- **Stateless everywhere.** Connectors, registry, and orchestrator hold no
  credentials and no fetched data between calls. Fresh connector instance per
  call; immutable registry; per-call auth injection.
- **Multi-tenant proven.** `tests/v1/test_isolation.py` drives one shared
  orchestrator from 16 threads and 64 interleaved coroutines with distinct
  tenant credentials and asserts zero cross-tenant leakage.
- **Expected failures are values.** Missing resources, bad credentials, and
  parse failures come back as typed `Error` / `Partial` results with an
  `ErrorKind`; exceptions are reserved for bugs.
- **Advisory, not rigid.** `metadata.kind` labels ("issue", "feed_item",
  "page") are hints for humans and heuristics — the tree shape is the
  contract.

## Legacy API (v0.x)

The pre-1.0 fetcher layer still ships and works unchanged:

```python
from omni_fetcher import OmniFetcher

fetcher = OmniFetcher()
result = await fetcher.fetch("https://api.example.com/data.json")
```

What changed in 1.0: the ~50 source-specific public schema classes
(`GitHubIssue`, `NotionPage`, `JiraIssue`, ...) are no longer exported from
`omni_fetcher` — consumers move to the canonical contract above.
[docs/migration-v1.md](docs/migration-v1.md) maps every removed schema family
onto atoms + metadata + `source_extra`, field by field.

The `examples/` directory covers the legacy API end to end (auth methods,
custom fetchers, per-source walkthroughs).

## Development

```bash
git clone https://github.com/Jainil-Gosalia/omni_fetcher
cd omni_fetcher
pip install -e ".[dev,office,web,gdrive,confluence,slack,jira]"

pytest --ignore=tests/integration   # unit + contract suites (~1400 tests)
ruff check . && ruff format --check .
mypy omni_fetcher/
```

CI runs the same four gates on Python 3.10–3.12. `tests/integration` hits live
services and needs real credentials; it is opt-in and excluded from CI.
Releases are tag-driven: publishing a GitHub Release `vX.Y.Z` builds, gates,
and publishes to PyPI via trusted publishing.

## License

[MIT](LICENSE) © 2024–2026 OmniFetcher Contributors
