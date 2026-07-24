# Connector reference

All connectors live under `omni_fetcher.v1.connectors.*` and return the same
`Result` / `CompositionNode` contract. Prefer routing through
`OmniFetcher(builtin_registry())` unless the source is fixed and known.

The set of connectors grows every release and depends on which optional extras
are installed, so this file does **not** enumerate them — a static list would be
wrong the moment a connector is added or an extra is missing. Discover the live
set instead; the details of any one connector live in its own docstring, which
ships with the code and never drifts.

## Discovering connectors

**The routable sources in this install, and which are streams:**

```bash
omni-fetcher v1 sources          # table: each source, bounded-or-stream, its URI pattern
omni-fetcher v1 sources --json   # the same as JSON
```

Only sources whose extra is installed appear, so this is the true set for the
environment you are in — not a wishlist. The same data is available in Python via
`builtin_registry().definitions()`.

**A specific connector's URI shape, auth, options, and behaviour** — read its
module docstring (authoritative and current):

```bash
python -c "from omni_fetcher.v1.connectors import postgres_query; print(postgres_query.__doc__)"
python -c "from omni_fetcher.v1.connectors import kafka; print(kafka.__doc__)"
```

Over the MCP server, the same discovery is the `list_sources` tool.

## Bounded vs. unbounded — the stable rule

The one thing that does not change is *how* the two kinds behave:

- **Bounded** sources (documents, an Elasticsearch search, a SQL query) terminate.
  Use `fetch()`; it returns one `Result`.
- **Unbounded** sources (a log tail, a Kafka/Redis stream, a CDC feed, a
  WebSocket/SSE socket) emit items forever. Use `stream()`; `fetch()` on them
  returns `Error(UNSUPPORTED)`. Over MCP, use the `sample` tool for a bounded
  window.

A stream never silently ends: a dropped connection (rotated file, broker blip,
closed socket) comes back as `Error(TRANSIENT)`, and each yielded item carries
its resume position in `source_extra` (a byte offset, a partition/offset, a
sequence, a replication slot). `stream_with_restart` reads that position and
reconnects where it left off:

```python
from omni_fetcher.v1 import RetryPolicy, stream_with_restart

async for item in stream_with_restart(omni, uri, policy=RetryPolicy(max_attempts=4)):
    ...   # resumes from each item's position after a TRANSIENT drop
```

The per-connector specifics — a stream's exact query params, a database
connector's read-only guarantee, an SQL connector's `?table=`/`?query=` inputs —
are in each connector's docstring. Read it rather than guessing.

## SQL query connectors

The `postgres://`, `mysql://` (with a `mariadb://` alias), and `sqlite://`
connectors are bounded: they run one read query and return a
`kind="query_result"` node carrying a single `Table` atom (columns as
`headers`, rows as `rows`). Three inputs, exactly one per call:

```
?table=<schema.table>       # SELECT * under a row cap (identifier-quoted, injection-safe)
?query=<url-encoded SELECT> # an arbitrary read query
?query_env=<ENV_NAME>       # read the SQL from an environment variable
```

Read-only is enforced by the engine, not by string parsing (a `READ ONLY`
transaction on Postgres/MySQL, `mode=ro` on SQLite), so a write is refused. A
result over the row cap (default 1000, `?limit=` to raise) comes back as
`Partial` with a typed gap rather than a silent truncation. `sqlite://` needs no
extra; `postgres`/`mysql` do. Full URI/auth/type-coercion detail is in each
connector's docstring.

## Writing a custom connector

Subclass `BaseFetcher` and override `stream()` only — `fetch()` is provided by
the base and collects the stream. Build nodes with `build_node` rather than
constructing `CompositionNode` by hand.

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
            kind="greeting",                  # advisory label
            atoms=[Text(content=f"hello, {uri}")],
            source_namespace="hello",         # → source_extra["hello"]
            source_fields={"lang": "en"},
        )
        yield success(node)
```

Hold to the contract the built-ins hold to:

- Return expected failures as `error(ErrorKind.X)` values; raise only on bugs.
- Map source-only fields into `source_fields` under your namespace — never onto
  the core `Metadata`.
- Yield `partial(node, gaps=[...])` with typed gaps when you get some of the
  data; don't silently truncate.
- Keep the connector stateless — no credentials, no fetched data on `self`.
- For an unbounded source, override `fetch` to return `error(ErrorKind.UNSUPPORTED)`
  (this is also what marks it a stream to discovery and to the MCP server).

Register it to route like a built-in:

```python
from omni_fetcher.v1 import OmniFetcher, RegistryBuilder, SourceDefinition

registry = (
    RegistryBuilder()
    .add(
        SourceDefinition(
            name="hello",                     # unique source name
            fetcher_class=HelloConnector,     # the class, not an instance
            uri_patterns=("hello://*",),      # URIs this source claims
            priority=100,                     # lower wins when several match
        )
    )
    .build()                                  # immutable from here on
)
omni = OmniFetcher(registry)
```

## Legacy v0.x API

`from omni_fetcher import OmniFetcher` (no `.v1`) is the pre-1.0 layer. It still
ships and works unchanged, but it is not the contract above — don't mix them, and
use v1 for new code.

The ~50 source-specific schema classes (`GitHubIssue`, `NotionPage`,
`JiraIssue`, …) are no longer exported from `omni_fetcher` as of 1.0.
`docs/migration-v1.md` in the repo maps every removed schema family onto
atoms + metadata + `source_extra`, field by field.
