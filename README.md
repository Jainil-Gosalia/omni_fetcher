# OmniFetcher

[![PyPI Version](https://img.shields.io/pypi/v/omni-fetcher.svg)](https://pypi.org/project/omni-fetcher/)
[![Python Versions](https://img.shields.io/pypi/pyversions/omni-fetcher.svg)](https://pypi.org/project/omni-fetcher/)
[![CI](https://github.com/Jainil-Gosalia/omni_fetcher/actions/workflows/ci.yml/badge.svg)](https://github.com/Jainil-Gosalia/omni_fetcher/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**One typed shape for every data source.**

Fetch a Jira ticket, a Postgres query, a PDF, an S3 object, or a live Kafka
topic — and get back the *same typed result* every time. The code that reads one
reads them all. There's also an MCP server, so your LLM agents can fetch from any
of them through a single tool.

30 sources. One contract. The connectors are interchangeable plumbing — the
typed shape they all emit is the point.

```python
from omni_fetcher.v1 import AtomKind, OmniFetcher, Success, builtin_registry

omni = OmniFetcher(builtin_registry())

# A GitHub issue, a PDF, or a live SQL query — identical code, every time.
for uri in [
    "https://github.com/psf/requests/issues/42",
    "report.pdf",
    "postgres://db.internal/app?query=SELECT%20id,email%20FROM%20users%20LIMIT%2010",
]:
    result = await omni.fetch(uri)                    # -> one typed Result
    if isinstance(result, Success):
        text = "\n".join(a.content for a in result.tree.find_atoms(AtomKind.TEXT))
        print(uri, "→", len(text), "chars")
```

No per-source SDKs. No per-source response shapes. No `if source == "jira"`.

---

## Why this exists

Every data source ships its own SDK, its own auth dance, and its own response
shape. The moment you need *many* of them — for a RAG pipeline, an agent's
context, an ingestion job — you drown in glue code that's different for each one,
and you re-learn "how do I get the text out of *this* thing" thirty times.

OmniFetcher makes that problem disappear by refusing to be interesting at the
edges. Every connector — a web page, a Confluence space, a SQL database, a log
tail — maps onto **one canonical contract**: a `Result` you branch on, a tree of
typed content atoms you walk, and a uniform metadata core. Learn it once; it
never changes shape on you.

The design bets, all defensible and all tested:

- **Errors are values, not exceptions.** A missing resource, a bad credential, a
  parse failure comes back as a typed `Error(kind)` you branch on — not a
  `try/except` you forgot to write. Partial data comes back as `Partial` with
  typed gaps naming exactly what's missing, so you never silently lose the half
  that worked.
- **Stateless, multi-tenant by construction.** Connectors hold no credentials
  and no data between calls; you pass auth per call. One shared orchestrator
  serves every tenant concurrently — proven by a test that drives it from 16
  threads and 64 interleaved coroutines and asserts zero cross-tenant leakage.
- **Read-only means read-only — enforced by the engine, not a regex.** The SQL
  connectors run every query inside a `READ ONLY` transaction (Postgres/MySQL) or
  a `mode=ro` open (SQLite). A write is refused by the *database*, not by us
  trying to parse your SQL. Verified against real Postgres, MySQL, and MariaDB.
- **Zoom is semantic, not token-chopping.** Ask for text at paragraph or
  sentence depth and you get the source's *own* structure, split losslessly —
  never an arbitrary character window. Ask for a depth a source can't provide and
  you get an honest gap, not a silent no-op.

The full rationale — what OmniFetcher refuses to be, and why — is in
[PHILOSOPHY.md](PHILOSOPHY.md).

## For agents: one MCP server, every source

```bash
pip install "omni-fetcher[mcp]"
```

`omni-fetcher-mcp` is a stdio [MCP](https://modelcontextprotocol.io) server that
puts every source in front of a model as three tools:

- **`fetch(uri)`** — the canonical result as JSON, for any bounded source.
- **`sample(uri, max_items)`** — a bounded window over an unbounded stream
  (Kafka, a log tail, CDC), so an agent can peek without hanging.
- **`list_sources()`** — what this install can reach, and which are streams.

Credentials are host-configured (`OMNI_FETCHER_GITHUB_TOKEN`,
`OMNI_FETCHER_POSTGRES_PASSWORD`, …) and injected per call — **a token never
enters the model's context.** Point Claude Desktop, Claude Code, or any MCP
client at it and the model can pull a Jira ticket, a database row, and a PDF into
its context through one interface, with no glue code.

There are also bundled [agent skills](.claude/skills/) — one that teaches an
agent the contract, one that assembles context from a set of sources — for
Claude Code and similar.

## The contract

Every fetch returns a `Result`. That's the whole API surface you branch on:

```
Result
├── Success ── tree: CompositionNode
├── Partial ── tree + gaps: list[Gap]        # what came back, plus typed holes
└── Error ──── kind: ErrorKind + message     # returned as a value, never raised

CompositionNode          # exactly two fields — no surprises
├── metadata: Metadata   # id, kind, tags, created, updated, author, source_url
│                        #   + source_extra["<source>"] for the source-only fields
└── children: [ ... ]    # a MIXED list, in document order:
                         #   CompositionNode | Text | Image | Audio | Video | Table
```

Content and metadata are separate on purpose: fields *every* source has (id,
author, timestamps, `source_url`) are typed on `Metadata`; anything only one
source has is namespaced under `source_extra["jira"]`, so it can't collide with
your generic code and can't leak into it. Walk the tree with accessors —
`node.find_atoms(AtomKind.TEXT)`, `node.find_by_kind("feed_item")` — and it's
the same three moves regardless of source: **check the state, walk the tree, read
the atoms.**

## 30 sources — and counting

<details>
<summary><b>Documents & files</b> — PDF, DOCX, PPTX, CSV, audio, local files</summary>

Local paths and `file://`, `.pdf`, `.docx`/`.pptx` (the `office` extra), `.csv`,
audio, and any `https://` page or JSON/GraphQL/RSS endpoint.
</details>

<details>
<summary><b>SaaS & collaboration</b> — Jira, Confluence, Slack, Notion, Linear, GitHub, Google Drive, SharePoint, YouTube</summary>

Each with its own URI scheme (`jira://issue/KEY`, `slack://channel/ID`, …) and
per-call auth. Atlassian ones need the `jira` / `confluence` extra.
</details>

<details>
<summary><b>Databases</b> — Postgres, MySQL/MariaDB, SQLite (read queries), Postgres CDC (change stream)</summary>

`postgres://`, `mysql://`/`mariadb://`, `sqlite://` run a read query and return a
`Table` atom; read-only is enforced by the engine. `postgres-cdc://` streams
row-level INSERT/UPDATE/DELETE via logical replication. SQLite needs no extra.
</details>

<details>
<summary><b>Search & storage</b> — Elasticsearch, S3</summary>

`es://…` drives the scroll API and returns one document per hit; `s3://bucket/key`
with `AwsAuth`.
</details>

<details>
<summary><b>Streams</b> — Kafka, Redis Streams, log tails, WebSocket, SSE</summary>

Unbounded sources you consume with `stream()` (or the MCP `sample` tool). Each
item carries its resume position, and `stream_with_restart` reconnects across a
dropped connection with no loss.
</details>

You don't have to trust a list in a README — ask the installed package what it
can actually reach (only sources whose extras are installed show up):

```bash
python -c "
from omni_fetcher.v1 import builtin_registry
from omni_fetcher.v1.fetcher import BaseFetcher
for d in sorted(builtin_registry().definitions(), key=lambda d: d.name):
    stream = type(d.fetcher_class()).fetch is not BaseFetcher.fetch
    print(f'{d.name:16s} {\"stream\" if stream else \"bounded\"}  {d.uri_patterns[0]}')
"
```

## Install & 60 seconds

```bash
pip install omni-fetcher
```

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

Optional extras gate a source or capability: `office` (DOCX/PPTX), `jira`,
`confluence`, `elasticsearch`, `kafka`, `websockets`, `postgres`, `mysql`,
`gdrive`, `web`, `mcp`. Everything else works on the core install.

## A few more moves

**Handle all three states** — expected failures are values:

```python
from omni_fetcher.v1 import Error, Partial, Success

result = await connector.fetch(uri)
if isinstance(result, Success):
    ...                              # result.tree
elif isinstance(result, Partial):
    ...                              # result.tree + result.gaps  (don't throw this away)
elif isinstance(result, Error):
    ...                              # result.kind, result.message
```

**Auth per call** — nothing is stored, which is what makes it multi-tenant-safe:

```python
from omni_fetcher.v1 import BasicAuth, BearerAuth

await JiraConnector().fetch("jira://issue/PROJ-1",
                            auth=BasicAuth(username="dev@acme.io", password="api-token"))
await SlackConnector().fetch("slack://channel/C0123456789",
                             auth=BearerAuth(token="xoxb-..."))
```

**Consume a stream** — resumable across drops:

```python
async for item in omni.stream("tail:///var/log/app.log?from=end"):
    if isinstance(item, Success):
        print(item.tree.find_atoms(AtomKind.TEXT)[0].content)
```

**Or skip Python** — the CLI passes credentials as env-var *names*, so no secret
touches `argv`:

```bash
omni-fetcher v1 fetch "sqlite:///app.db?query=SELECT%20*%20FROM%20users" --json
omni-fetcher v1 fetch "jira://issue/PROJ-1" \
  --auth-type basic --username-env JIRA_USER --password-env JIRA_TOKEN
omni-fetcher v1 stream "kafka://localhost:9092/events?offset=earliest" --json --max-items 100
```

**Write a connector** — subclass `BaseFetcher`, override `stream()`, and `fetch()`
comes free. The exact URI/auth/options of any built-in source is in its module
docstring (`python -c "from omni_fetcher.v1.connectors import mysql; print(mysql.__doc__)"`),
which never drifts from the code.

## Development

```bash
git clone https://github.com/Jainil-Gosalia/omni_fetcher
cd omni_fetcher
pip install -e ".[dev,office,web,gdrive,confluence,slack,jira,kafka,mcp,mysql]"

pytest --ignore=tests/integration     # ~1,300 unit + contract tests
ruff check . && ruff format --check . && mypy omni_fetcher/
```

CI runs the same gates on Python 3.10–3.12. Releases are tag-driven: a GitHub
Release `vX.Y.Z` builds, gates, and publishes to PyPI via trusted publishing.
The pre-1.0 API still ships unchanged; [docs/migration-v1.md](docs/migration-v1.md)
maps it onto the contract above.

## License

[MIT](LICENSE) © 2024–2026 OmniFetcher Contributors
