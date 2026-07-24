# OmniFetcher

[![PyPI Version](https://img.shields.io/pypi/v/omni-fetcher.svg)](https://pypi.org/project/omni-fetcher/)
[![Python Versions](https://img.shields.io/pypi/pyversions/omni-fetcher.svg)](https://pypi.org/project/omni-fetcher/)
[![CI](https://github.com/Jainil-Gosalia/omni_fetcher/actions/workflows/ci.yml/badge.svg)](https://github.com/Jainil-Gosalia/omni_fetcher/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

### One typed shape for every data source.

Jira, Postgres, PDFs, S3, Slack, Kafka, GitHub, Confluence — **30 sources, one
contract.** The code that reads one reads them all. Ships an MCP server, so your
LLM agents can fetch from any of them through a single tool.

```bash
pip install omni-fetcher
```

---

## The problem, in one screen

Pulling from four sources today means four SDKs, four auth models, four response
shapes, and four ways to fail:

```python
# GitHub — PyGithub
issue = Github(token).get_repo("psf/requests").get_issue(42)
text  = issue.body                                    # comments? separate call, separate shape

# Jira — atlassian-python-api
data  = Jira(url, username=u, password=t).issue("PROJ-1")
text  = data["fields"]["description"]                 # dict-diving, and it raises on 404

# A PDF — pypdf
text  = "\n".join(p.extract_text() for p in PdfReader("report.pdf").pages)

# Postgres — asyncpg
rows  = await (await asyncpg.connect(dsn)).fetch("SELECT id, email FROM users LIMIT 10")
text  = ...                                           # rows aren't text. and read-only? that's on you.
```

Now write the error handling. Four times. Now add a fifth source.

**With OmniFetcher, it's one loop:**

```python
from omni_fetcher.v1 import AtomKind, OmniFetcher, Success, builtin_registry

omni = OmniFetcher(builtin_registry())

for uri in [
    "https://github.com/psf/requests/issues/42",
    "jira://issue/PROJ-1",
    "report.pdf",
    "postgres://db.internal/app?query=SELECT%20id,email%20FROM%20users%20LIMIT%2010",
]:
    result = await omni.fetch(uri, auth=creds_for(uri))   # your credential lookup
    if isinstance(result, Success):                       # -> one typed Result, every source
        text = "\n".join(a.content for a in result.tree.find_atoms(AtomKind.TEXT))
        print(uri, "→", len(text), "chars")
```

Same call. Same result type. Same three moves every time: **check the state,
walk the tree, read the atoms.** No `if source == "jira"`. Ever.

## Who it's for

- **Building RAG or agent context** — you need many heterogeneous sources in one
  shape, with provenance and honest failures, not thirty bespoke parsers.
- **Internal tools and glue services** — one dependency instead of a dozen SDKs,
  and one error model instead of a dozen exception hierarchies.
- **Anyone wiring an LLM to real data** — the MCP server does it with no glue
  code at all.

## For agents: one MCP server, every source

```bash
pip install "omni-fetcher[mcp]"
```

`omni-fetcher-mcp` is a stdio [MCP](https://modelcontextprotocol.io) server that
hands a model three tools:

| Tool | What it does |
|---|---|
| `fetch(uri)` | The canonical result as JSON, for any bounded source |
| `sample(uri, max_items)` | A bounded window over an unbounded stream (Kafka, a log tail, CDC) — peek without hanging |
| `list_sources()` | What this install can reach, and which are streams |

Credentials are host-configured (`OMNI_FETCHER_GITHUB_TOKEN`,
`OMNI_FETCHER_POSTGRES_PASSWORD`, …) and injected per call — **a token never
enters the model's context.** Point Claude Desktop, Claude Code, or any MCP
client at it and the model pulls a Jira ticket, a database row, and a PDF into
context through one interface.

Two [agent skills](.claude/skills/) ship too: one teaches an agent the contract,
one assembles context from a set of sources.

## How it compares

Honest positioning — these tools are good at what they do; they solve a
differently-shaped problem:

| | Great at | Where OmniFetcher differs |
|---|---|---|
| **LangChain / LlamaIndex loaders** | Enormous loader breadth, deep framework integration | Each loader returns its own document/metadata shape and raises on failure. OmniFetcher emits **one typed contract** across every source, returns failures **as values**, and is framework-free — no chain, no index, no agent runtime to adopt |
| **unstructured.io** | Best-in-class *document* parsing into elements | Parsing files is one slice. OmniFetcher spans APIs, SaaS, databases, and live streams under the same shape (and will happily hand a PDF to a parser like theirs) |
| **Airbyte / Singer** | Production ELT — scheduled syncs into a warehouse | That's deployed infrastructure and batch pipelines. OmniFetcher is an **in-process library** for on-demand, request-time fetches: an agent's context, a RAG build, an internal endpoint |
| **Just calling each SDK** | Total control, zero abstraction | Perfect for one or two sources. The cost is N auth models, N response shapes, and N failure modes — to learn, and to maintain |

## Why it's built this way

The connectors are interchangeable plumbing. The typed shape they emit is the
point — and these are the bets behind it, all tested:

- **Errors are values, not exceptions.** A missing resource, a bad credential, a
  parse failure comes back as a typed `Error(kind)` you branch on — not a
  `try/except` you forgot to write. Partial data comes back as `Partial` with
  typed gaps naming exactly what's missing, so you never silently lose the half
  that worked.
- **Stateless, multi-tenant by construction.** Connectors hold no credentials and
  no data between calls; you pass auth per call. One shared orchestrator serves
  every tenant concurrently — proven by a test that drives it from 16 threads and
  64 interleaved coroutines and asserts zero cross-tenant leakage.
- **Read-only means read-only — enforced by the engine, not a regex.** The SQL
  connectors run every query inside a `READ ONLY` transaction (Postgres/MySQL) or
  a `mode=ro` open (SQLite). A write is refused by the *database*, not by us
  trying to parse your SQL. Verified against real Postgres, MySQL, and MariaDB.
- **Zoom is semantic, not token-chopping.** Ask for text at paragraph or sentence
  depth and you get the source's *own* structure, split losslessly — never an
  arbitrary character window. Ask for a depth a source can't provide and you get
  an honest gap, not a silent no-op.

The full rationale — including what OmniFetcher deliberately refuses to be — is
in [PHILOSOPHY.md](PHILOSOPHY.md).

## The contract

Every fetch returns a `Result`. That's the whole surface you branch on:

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
your generic code and can't leak into it.

## 30 sources — and counting

<details>
<summary><b>Documents & files</b> — PDF, DOCX, PPTX, CSV, audio, local files, any URL</summary>

Local paths and `file://`, `.pdf`, `.docx`/`.pptx` (the `office` extra), `.csv`,
audio, plus any `https://` page or JSON/GraphQL/RSS endpoint.
</details>

<details>
<summary><b>SaaS & collaboration</b> — Jira, Confluence, Slack, Notion, Linear, GitHub, Google Drive, SharePoint, YouTube</summary>

Each with its own URI scheme (`jira://issue/KEY`, `slack://channel/ID`, …) and
per-call auth. The Atlassian ones need the `jira` / `confluence` extra.
</details>

<details>
<summary><b>Databases</b> — Postgres, MySQL/MariaDB, SQLite, Postgres CDC</summary>

`postgres://`, `mysql://`/`mariadb://`, `sqlite://` run a read query and return a
`Table` atom, read-only enforced by the engine. `postgres-cdc://` streams
row-level INSERT/UPDATE/DELETE via logical replication. SQLite needs no extra.
</details>

<details>
<summary><b>Search & storage</b> — Elasticsearch, S3</summary>

`es://…` drives the scroll API and returns one document per hit;
`s3://bucket/key` with `AwsAuth`.
</details>

<details>
<summary><b>Streams</b> — Kafka, Redis Streams, log tails, WebSocket, SSE</summary>

Unbounded sources consumed with `stream()` (or the MCP `sample` tool). Each item
carries its resume position, and `stream_with_restart` reconnects across a
dropped connection without loss.
</details>

Don't trust a list in a README — ask the installed package what it can actually
reach (only sources whose extras are installed show up):

```bash
omni-fetcher v1 sources          # a table of every routable source + bounded/stream flag
omni-fetcher v1 sources --json   # the same, as JSON
```

## 60 seconds

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

Optional extras gate a source or capability: `office`, `jira`, `confluence`,
`elasticsearch`, `kafka`, `websockets`, `postgres`, `mysql`, `gdrive`, `web`,
`mcp`. Everything else works on the core install.

## A few more moves

**Handle all three states** — expected failures are values:

```python
result = await connector.fetch(uri)
if isinstance(result, Success):
    ...                       # result.tree
elif isinstance(result, Partial):
    ...                       # result.tree + result.gaps  (don't throw this away)
elif isinstance(result, Error):
    ...                       # result.kind, result.message
```

**Auth per call** — nothing is stored, which is what makes it multi-tenant-safe:

```python
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

**Or skip Python** — the CLI takes credentials as env-var *names*, so no secret
touches `argv`:

```bash
omni-fetcher v1 fetch "sqlite:///app.db?query=SELECT%20*%20FROM%20users" --json
omni-fetcher v1 fetch "jira://issue/PROJ-1" \
  --auth-type basic --username-env JIRA_USER --password-env JIRA_TOKEN
omni-fetcher v1 stream "kafka://localhost:9092/events?offset=earliest" --json --max-items 100
```

**Write a connector** — subclass `BaseFetcher`, override `stream()`, and
`fetch()` comes free. Any built-in's exact URI/auth/options live in its module
docstring (`python -c "from omni_fetcher.v1.connectors import mysql; print(mysql.__doc__)"`),
which never drifts from the code.

## Status

v1.10, MIT, ~1,300 tests, Python 3.10–3.12. The v1 contract is stable and
additive — new connectors slot in without changing the shape you code against.
Issues and connector contributions welcome; if a source you need is missing, the
custom-connector path above is about twenty lines.

## Development

```bash
git clone https://github.com/Jainil-Gosalia/omni_fetcher
cd omni_fetcher
pip install -e ".[dev,office,web,gdrive,confluence,slack,jira,kafka,mcp,mysql]"

pytest --ignore=tests/integration     # unit + contract suites
ruff check . && ruff format --check . && mypy omni_fetcher/
```

CI runs the same gates on Python 3.10–3.12. Releases are tag-driven: a GitHub
Release `vX.Y.Z` builds, gates, and publishes to PyPI via trusted publishing.
The pre-1.0 API still ships unchanged; [docs/migration-v1.md](docs/migration-v1.md)
maps it onto the contract above.

## License

[MIT](LICENSE) © 2024–2026 OmniFetcher Contributors
