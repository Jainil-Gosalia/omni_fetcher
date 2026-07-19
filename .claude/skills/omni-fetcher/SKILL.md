---
name: omni-fetcher
description: Fetch content from any source (Jira, Slack, GitHub, Confluence, Notion, Linear, SharePoint, S3, Google Drive, PDF/DOCX/PPTX/CSV/audio, HTTP/JSON/GraphQL/RSS, YouTube, Elasticsearch, Kafka, Redis, log tails, WebSocket/SSE, and SQL databases via postgres://, mysql://, sqlite:// query connectors) through the omni-fetcher library, its CLI, or its MCP server, and get one typed shape back every time. Use when writing code that pulls data from an external system, database, or document, when the user names any such source, when they mention omni-fetcher / omnifetcher / OmniFetcher or its MCP server, or when building ingestion, RAG, or agent-context pipelines that need many sources behind one interface. Also use when writing a custom connector, or when running a read query against a database. Discover the exact source set of the installed version at runtime (see below) rather than assuming a fixed list.
---

# OmniFetcher

Fetch anything — a Jira issue, a PDF, an S3 object, a Slack thread, a web page, a
row from a `postgres://` query — and get back the same typed shape every time.
Code that walks a GitHub issue walks a Confluence page unchanged.

Two things about this skill are split on purpose:

- **The contract is stable — learn it here.** The `Result` envelope, the atom
  vocabulary, zoom, per-call auth, `source_extra`: these rarely change and are
  the mental model everything else rests on.
- **The source set is not — discover it.** Which connectors exist, which are
  installed, which stream, what the MCP server exposes: this grows every
  release and depends on the environment. Never hardcode it from memory or from
  a doc; ask the installed package at the moment you need to know. The commands
  are below.

## Discover what THIS install has

The library is the source of truth for its own surface, and it is always current
with the installed version. Before assuming a source exists, is bounded, or
takes a given URI, discover it.

**What sources can this install route, and which are streams?**

```bash
python -c "
from omni_fetcher.v1 import builtin_registry
from omni_fetcher.v1.fetcher import BaseFetcher
for d in sorted(builtin_registry().definitions(), key=lambda d: d.name):
    stream = type(d.fetcher_class()).fetch is not BaseFetcher.fetch
    print(f'{d.name:16s} {\"stream\" if stream else \"bounded\"}  {d.uri_patterns[0]}')
"
```

`builtin_registry()` skips any source whose optional extra is not installed, so
this lists exactly what *this* environment can fetch — not a wishlist. A source
that overrides `fetch` is unbounded (use `stream()` / the MCP `sample` tool); the
rest are bounded (use `fetch()`).

**How do I use a specific source — its URI shape, auth, and options?** Read the
connector module's own docstring. It ships with the code, so it never drifts:

```bash
python -c "from omni_fetcher.v1.connectors import mysql; print(mysql.__doc__)"
python -c "from omni_fetcher.v1.connectors import jira; print(jira.__doc__)"
```

**What version, and which extras are installed?**

```bash
pip show omni_fetcher              # version + location
python -c "import importlib.metadata as m; print(m.version('omni_fetcher'))"
```

**What does the MCP server expose?** If connected, call its `list_sources` tool
(name, URI patterns, and a bounded/unbounded flag per source) and read the
`fetch` / `sample` tool schemas. That is the same discovery, over the protocol.

Reach for these whenever the task hinges on "does this install support X?" — the
answer is one command away and correct for the environment you are actually in.

## Install

```bash
pip install omni-fetcher            # core sources
pip install "omni-fetcher[mysql]"   # a source behind an optional extra
pip install "omni-fetcher[mcp]"     # the MCP server (omni-fetcher-mcp)
```

Core install covers the no-extra sources; others (`office`, `jira`,
`confluence`, `elasticsearch`, `kafka`, `websockets`, `postgres`, `mysql`,
`gdrive`, `web`, `mcp`) gate a source or capability. Which extras a source needs,
and which are present, is discoverable (above) — don't trust a memorized list.

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
```

Shapes: `BearerAuth(token=)`, `ApiKeyAuth(api_key=, header=)`,
`BasicAuth(username=, password=)`, `OAuth2Auth(access_token=)`,
`AwsAuth(access_key_id=, secret_access_key=)`. Which shape a source wants is in
its docstring. Read secrets from the environment; never hardcode or log one.

**Read source-specific fields from `source_extra`, not from the core.** Fields
every source shares are typed on `Metadata` (`id`, `kind`, `tags`, `created`,
`updated`, `author`, `source_url`). Anything only one source has is namespaced:

```python
node.metadata.id                               # "PROJ-1"       — uniform
node.metadata.author                           # reporter name  — uniform
node.metadata.source_extra["jira"]["status"]   # Jira-only      — namespaced
```

**`metadata.kind` is advisory.** Labels like `"issue"`, `"page"`,
`"query_result"` are hints for humans and heuristics. The tree shape is the
contract — don't branch program logic on `kind` where walking the tree would do.

**Route many URIs through one orchestrator.** Wire it once and share it across
threads and event loops; each call gets a fresh connector and its own auth.

```python
from omni_fetcher.v1 import BearerAuth, OmniFetcher, builtin_registry

omni = OmniFetcher(builtin_registry())    # every available built-in, wired once
result = await omni.fetch("https://github.com/psf/requests/issues/42",
                          auth=BearerAuth(token=token), tags=["tenant-a"])
```

`builtin_registry()` resolves lazily and skips sources whose extra isn't
installed. An unrouted URI returns `Error(NOT_FOUND)` as a value.

**Bounded vs. unbounded: let the contract tell you, don't guess.** A bounded
source (a document, a database query, an Elasticsearch search) uses `fetch()`; an
unbounded one (a log tail, a Kafka topic, a CDC stream) emits items forever and
uses `stream()` — calling `fetch()` on it returns `UNSUPPORTED`. Which is which
is discoverable (the `stream`/`bounded` flag above, the MCP `list_sources` tool,
or simply the typed `UNSUPPORTED` a stream returns from `fetch`). Do not memorize
a scheme list — it changes.

```python
async for item in omni.stream("tail:///var/log/app.log?from=end"):
    if isinstance(item, Success):
        line = item.tree.find_atoms(AtomKind.TEXT)[0].content
```

## Walking the tree

- `node.find_atoms(AtomKind.TEXT)` → atoms of one kind in the subtree
- `node.find_by_kind("feed_item")` → descendant nodes with that advisory kind
- `node.iter_descendants()` → nodes only; `node.iter_atoms()` → atoms only;
  `node.iter_children()` → direct children, mixed
- `node.merged_tags()` → tags inherited down the tree

`AtomKind`: `TEXT`, `IMAGE`, `AUDIO`, `VIDEO`, `TABLE`. `Text` has `.content`;
`Table` has `.headers` and `.rows` (a SQL query returns one `Table`).

### Getting the whole text out

The library has no tree→text helper — you write the join. It is one line, and
`find_atoms` already returns the whole subtree in document order:

```python
text = "\n\n".join(a.content for a in node.find_atoms(AtomKind.TEXT))
```

**Do not write `find_atoms(AtomKind.TEXT)[0].content` to "get the text."** It
returns the *first* atom, not the content. On a real README that is 15 of 18,022
characters — 0.1% — and it fails silently, with plausible-looking output. The
`[0]` you see in docs is only ever valid for a one-atom-per-item source, such as
a `tail` or WebSocket message. Feeding an LLM `[0]` hands it a Jira issue's
description alone, without the comments, and the summary comes back confidently
wrong.

## Zoom, retry, CLI

`ZoomSpec` picks semantic tree depth per atom type — structural decomposition,
never token windowing:

```python
from omni_fetcher.v1 import AtomKind, DepthLevel, ZoomSpec

spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.PARAGRAPH})
result = await omni.fetch("notes.md", zoom=spec)
```

`DepthLevel`: `NATURAL` (default), `WHOLE`, `SECTION`, `PARAGRAPH`, `SENTENCE`,
`MAX`. Zoom applies centrally to every source. A finer level on content that
cannot decompose to it (code, opaque bytes, a table) records an honest gap
rather than splitting into fragments that misrepresent their format.

Retry is a host decision over the typed errors — `RATE_LIMITED`/`TRANSIENT` are
worth retrying; delivered data (`Success`/`Partial`) is never retried:

```python
from omni_fetcher.v1 import RetryPolicy, fetch_with_retry, stream_with_restart

policy = RetryPolicy(max_attempts=4, initial_delay=0.5, jitter=0.2)
result = await fetch_with_retry(omni, uri, policy=policy, auth=auth)
```

CLI — credentials are passed as environment-variable *names*, so no secret
touches `argv`. Exit codes: 0 success/partial, 1 typed error, 2 usage.

```bash
omni-fetcher v1 fetch README.md --json
omni-fetcher v1 fetch "jira://issue/PROJ-1" \
  --auth-type basic --username-env JIRA_USER --password-env JIRA_TOKEN
omni-fetcher v1 fetch "sqlite:///app.db?query=SELECT%20*%20FROM%20users" --json
omni-fetcher v1 stream "kafka://localhost:9092/events?offset=earliest" --json --max-items 100
```

## The MCP server (for agents)

`pip install "omni-fetcher[mcp]"` provides `omni-fetcher-mcp`, an stdio MCP server
wrapping `OmniFetcher(builtin_registry())`. Wire it into an MCP client (e.g. add
`{"command": "omni-fetcher-mcp"}` to the client config) to give a model three
tools, no glue code:

- **`fetch(uri, zoom?, tags?)`** — the canonical `Result` as JSON. Bounded
  sources only; an unbounded source returns typed `unsupported`.
- **`sample(uri, max_items?, timeout_seconds?, zoom?, tags?)`** — a bounded
  window over an unbounded stream (kafka/tail/…): up to `max_items` items, or
  whatever arrives in the time budget, folded into one collection result.
- **`list_sources()`** — the discovery call: every routable source, its URI
  patterns, and a bounded/unbounded flag.

Credentials are host-configured, never model-supplied: the server reads them from
`OMNI_FETCHER_<SOURCE>_<FIELD>` env vars (e.g. `OMNI_FETCHER_GITHUB_TOKEN`,
`OMNI_FETCHER_POSTGRES_USERNAME` + `_PASSWORD`) and injects them per call, so a
token never enters the model's context. A source needing a credential that isn't
configured returns a typed `auth_failed` naming the env var to set.

## More

For the custom-connector pattern (subclass `BaseFetcher`, override `stream()`,
build nodes with `build_node`, register with `RegistryBuilder`) and the v0.x → v1
migration, read `references/connectors.md`. For the exact URI/auth/options of any
specific source, read that connector's docstring (the discovery command above) —
it is authoritative and current for the installed version.
