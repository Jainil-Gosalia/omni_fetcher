# Connector reference

All connectors live under `omni_fetcher.v1.connectors.*` and return the same
`Result` / `CompositionNode` contract. Prefer routing through
`OmniFetcher(builtin_registry())` unless the source is fixed and known.

## Bounded connectors — use `fetch()`

| Connector | URI shapes | Auth | Extra |
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

### Elasticsearch

Bounded, not streaming — `fetch()` drives the scroll API internally (page size
capped, `?size=` bounds the total) and returns one `Result` whose tree is a
`search_results` container with one `json_document` child per hit.

```python
result = await omni.fetch("es://search.example.com/logs?q=level:error&size=500")
if isinstance(result, Success):
    for doc in result.tree.find_by_kind("json_document"):
        print("\n".join(a.content for a in doc.find_atoms(AtomKind.TEXT)))
```

Prefer `find_by_kind("json_document")` over iterating `result.tree.children`:
`children` is a mixed node/atom list, so calling node methods on it is only safe
behind an `isinstance` check.

Zero matches is `Error(NOT_FOUND)`. A scroll that fails partway returns
`Partial` with the documents collected so far.

## Streaming (unbounded) connectors — use `stream()`

`fetch()` on these returns `Error(UNSUPPORTED)`.

| Connector | URI shapes | Auth | Extra |
|---|---|---|---|
| `tail.TailConnector` | `tail://<path>?from=end\|start\|<byte>&poll=<s>` | — | — |
| `kafka.KafkaConnector` | `kafka://host[:port]/topic?offset=latest\|earliest\|<n>&group=<id>` | — | `kafka` |
| `redis.RedisConnector` | `redis://host[:port]/stream-key?offset=$\|0\|<id>&group=<id>` | — | — |
| `websocket.WebSocketConnector` | `ws://host[:port]/path?token=&auth=&sequence=<n>` | via URI query | `websockets` |
| `sse.SSEConnector` | `sse://host[:port]/path?token=&auth=&sequence=<n>` | via URI query | `websockets` |
| `postgres_cdc.PostgresCDCConnector` | `postgres-cdc://host[:port]/database?slot=&user=&password=` | via URI query | `postgres` |

Each item carries its resume position in `source_extra`: tail `byte_offset`,
kafka `partition`/`offset`, websocket/sse `sequence`, postgres `slot`. A
dropped stream (rotated file, broker blip, closed socket) ends with
`Error(TRANSIENT)`; `stream_with_restart` derives the resume `?from=` /
`?offsets=` / `?slot=` and continues.

Kafka is stateless by default — no consumer group, no commits; start from
`?offset=` and resume via per-message positions. `?group=<id>` opts into
committing consumer-group semantics the host then owns.

WebSocket/SSE messages are always plain `Text` — no JSON parsing, that's a
host-side concern. Auth travels as `?token=<value>` or `?auth=Bearer+<token>`.
`?sequence=<n>` seeds/resumes numbering; these sources are ephemeral, so a
message lost while disconnected can't be recovered, but resume prevents
duplicates.

Postgres CDC streams row-level INSERT/UPDATE/DELETE via logical replication
(`wal_level=logical` required): each change is a `kind="change"` node whose
`Text` atom is JSON `{op, table, new, old, lsn, timestamp, xid}`. The
connector creates and drops its replication slot itself; after a transport
failure the slot is kept so a restart resumes from its `confirmed_flush_lsn`
with no change loss. No initial snapshot — the stream starts at the current
WAL position.

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

`RegistryBuilder.source(name, uri_patterns=..., priority=...)` is the equivalent
decorator form, applied to the fetcher class.

## Legacy v0.x API

`from omni_fetcher import OmniFetcher` (no `.v1`) is the pre-1.0 layer. It still
ships and works unchanged, but it is not the contract above — don't mix them,
and use v1 for new code.

The ~50 source-specific schema classes (`GitHubIssue`, `NotionPage`,
`JiraIssue`, …) are no longer exported from `omni_fetcher` as of 1.0.
`docs/migration-v1.md` in the repo maps every removed schema family onto
atoms + metadata + `source_extra`, field by field. The `examples/` directory
covers the legacy API end to end.
