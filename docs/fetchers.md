# Connectors

Every v1 connector emits the same canonical contract. Fetch through the
orchestrator (routes any URI) or instantiate a connector directly.

```python
from omni_fetcher.v1 import OmniFetcher, builtin_registry

omni = OmniFetcher(builtin_registry())
result = await omni.fetch("jira://issue/PROJ-1", auth=credential)
```

`builtin_registry()` registers all built-in connectors lazily (heavy
dependencies load only when routed to) and skips sources whose optional
extra is missing. An unrouted URI returns `error(ErrorKind.NOT_FOUND)` — as
a value, never a raise.

## The built-in matrix

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
| `audio.AudioConnector` | audio paths | — | — |
| `youtube.YouTubeConnector` | `youtube.com`, `youtu.be` | — | — |
| `s3.S3Fetcher` | `s3://bucket/key` | `AwsAuth` | — |
| `github.GitHubConnector` | `github.com/owner/repo[/issues/N, ...]` | optional `BearerAuth` | — |
| `google_drive.GoogleDriveFetcher` | `drive.google.com`, `docs.google.com` | `OAuth2Auth` | — |
| `notion.NotionConnector` | `notion.so` pages, `notion://database/<id>` | `BearerAuth` | — |
| `jira.JiraConnector` | `jira://issue/KEY`, `jira://project/KEY`, ... | `BasicAuth` / `BearerAuth` | `jira` |
| `confluence.ConfluenceConnector` | Confluence pages/spaces | `BasicAuth` / `BearerAuth` | `confluence` |
| `slack.SlackConnector` | `slack://channel/ID`, threads, DMs | `BearerAuth` | — |
| `sharepoint.SharePointConnector` | `sharepoint://site[/Library[/file]]` | `OAuth2Auth` | — |
| `linear.LinearConnector` | `linear://issue/ABC-1`, `linear.app` URLs | `Bearer`/`ApiKeyAuth` | — |
| `elasticsearch.ElasticsearchFetcher` | `es://host[:port]/index?q=&size=&scroll=&user=&password=&api_key=` | via URI query | `elasticsearch` |

`ElasticsearchFetcher` is bounded (D1): `fetch()` drives Elasticsearch's
scroll API internally and returns one `Result` — a `search_results`
container node whose children are `json_document` nodes (one per matching
document, up to `?size=`, default 100). Each document's `_source` is
preserved losslessly as a `Text` atom (`format=CODE`); there is no
`JSONData` atom in v1's atom vocabulary (a closed set — `Text`/`Image`/
`Audio`/`Video`/`Table`), so JSON bodies are serialised the same way
`http_json` already does. Query-level facts (`index`, `query`, `doc_count`,
`total_hits`, `took_ms`) live on the container's `source_extra`; per-document
facts (`doc_id`, `index`, `score`) live on each document node's own
`source_extra` — mirroring `confluence`'s space/page split.

```python
result = await omni.fetch("es://search.example.com/logs?q=level:error&size=500")
```

A query matching zero documents is `Error(NOT_FOUND)` — never a silent empty
success. A scroll failure after some documents were already collected
returns a `Partial` (the documents built so far, plus a typed gap) instead
of discarding progress. `?user=&password=` (basic auth) or `?api_key=`
authenticate; scroll cursors are always cleared on cleanup.

## Streaming (unbounded) sources

Some sources never end. `tail` and `kafka` follow a file or a topic
indefinitely, emitting **one `Result` per line/message** through the same
canonical contract as every bounded source — consume them with `stream()`:

```python
async for item in omni.stream("tail:///var/log/app.log?from=end"):
    if isinstance(item, Success):
        print(item.tree.find_atoms(AtomKind.TEXT)[0].content)
```

| Connector | URI | Item kind | Positions in `source_extra` |
|---|---|---|---|
| `tail.TailConnector` | `tail://<path>?from=end\|start\|<byte>&poll=<s>` | `log_line` | `path`, `byte_offset`, `line_number` |
| `kafka.KafkaConnector` | `kafka://host[:port]/topic?offset=…&offsets=p:o,…&group=id` | `message` | `topic`, `partition`, `offset`, `key`, `timestamp` |
| `redis.RedisConnector` | `redis://host[:port]/stream-key?offset=$\|0\|<id>&group=id` | `message` | `entry_id`, `timestamp`, `stream` |
| `websocket.WebSocketConnector` | `ws(s)://host[:port]/path?token=&auth=&sequence=<n>` | `message` | `url`, `handshake_timestamp`, `sequence`, `close_code` |
| `sse.SSEConnector` | `sse(s)://host[:port]/path?token=&auth=&sequence=<n>` | `message` | `url`, `handshake_timestamp`, `sequence`, `close_code` |

Configuration lives in the URI query (the only channel that survives the
orchestrator). Key semantics:

- **`fetch()` is `UNSUPPORTED`** on a streaming source — an unbounded stream
  cannot be collected into one tree.
- **Failures are terminal + typed.** A rotated-away file or a dropped broker
  connection ends the stream with a single `Error(TRANSIENT)`; the tail
  connector also follows in-place truncation and rotation onto the new file.
- **Positions enable resume.** Each item's `source_extra` carries the exact
  offset to restart from.
- **Kafka is stateless by default** (assign+seek, no commits); `?group=<id>`
  opts into a committing consumer group whose server-side state the host
  owns — the one deliberate exception to read-only determinism.
- **WebSocket/SSE are ephemeral.** Every message is a plain `Text` atom (no
  JSON parsing — a host-side concern); auth travels as `?token=<value>` or
  `?auth=Bearer+<token>` in the URI. Unlike Kafka/Redis, a message lost while
  disconnected cannot be recovered — `?sequence=<n>` only prevents
  duplicates on resume, it does not replay history.

### Resuming a dropped stream

`stream_with_restart` wraps a stream with `RetryPolicy`, swallowing retryable
ends and reopening from the last item's position (tail `byte_offset` →
`?from=`, kafka accumulated offsets → `?offsets=p:o+1`, redis `entry_id` →
`?offset=`, websocket/sse `sequence` → `?sequence=<n+1>`):

```python
from omni_fetcher.v1 import RetryPolicy, stream_with_restart

async for item in stream_with_restart(
    omni, "kafka://localhost:9092/events?offset=earliest",
    policy=RetryPolicy(max_attempts=10, initial_delay=1.0),
):
    ...  # spans broker reconnects without losing position
```

Pass a `resume=(uri, last_item) -> uri` callable to override the built-in
derivation. Delivered data (`Success`/`Partial`) is never retried; a
non-retryable terminal error passes straight through.

### On the command line

```bash
omni-fetcher v1 stream "tail:///var/log/app.log?from=end" --max-items 100
omni-fetcher v1 stream "kafka://localhost:9092/events?offset=earliest" --json
omni-fetcher v1 stream "ws://live.example.com/events?token=abc" --json
omni-fetcher v1 stream "sse://events.example.com/live?auth=Bearer+tok" --json
```

NDJSON out (one `Result` per line), `--max-items` to bound a run, Ctrl-C to
stop cleanly (exit 130). Kafka needs the `kafka` extra
(`pip install "omni-fetcher[kafka]"`); WebSocket/SSE need the `websockets`
extra (`pip install "omni-fetcher[websockets]"`).

## Zoom: semantic decomposition depth

Zoom selects how deeply a source's natural structure is expanded, per atom
type — semantic tree depth, never token windowing:

```python
from omni_fetcher.v1 import AtomKind, DepthLevel, ZoomSpec

spec = ZoomSpec(per_type={AtomKind.TEXT: DepthLevel.PARAGRAPH})
result = await omni.fetch("notes.md", zoom=spec)
# result.tree now has one "paragraph" child node per text block;
# the pieces concatenate exactly to the natural content.
```

- Coarser than natural (`WHOLE`, `SECTION` on deep trees) works for **every**
  connector — the tree is pruned centrally.
- Finer than natural (`SECTION`/`PARAGRAPH`/`SENTENCE`) decomposes `Text`
  atoms in the text-bearing connectors (local_file, http_url, pdf, docx,
  pptx). pptx maps `SECTION` onto its slides. A finer level explicitly
  requested for an undecomposable kind (image, audio, video, table) records
  an honest gap (`Partial`) instead of a silent no-op.

## Retrying transient failures

Connectors classify failures onto a shared taxonomy (429 → `RATE_LIMITED`,
5xx/timeouts → `TRANSIENT` — the table lives in `omni_fetcher.v1.errors`).
Retrying is a host decision:

```python
from omni_fetcher.v1 import RetryPolicy, fetch_with_retry

policy = RetryPolicy(max_attempts=4, initial_delay=0.5, jitter=0.2)
result = await fetch_with_retry(omni, uri, policy=policy, auth=credential)
```

The policy is frozen (safe to share across tenants); delivered data
(`Success` / `Partial`) is never retried; the final `Result` comes back
unchanged.

## Writing a connector

Subclass `BaseFetcher`, override `stream()`, build canonical nodes with the
mapping helper — `fetch()` comes for free:

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
            kind="greeting",
            atoms=[Text(content=f"hello, {uri}")],
            source_namespace="hello",
            source_fields={"lang": "en"},
        )
        yield success(node)
```

Register it with a `SourceDefinition` on your own `RegistryBuilder` and it
routes like any built-in.

## Legacy fetchers

The pre-1.0 fetcher classes still exist under `omni_fetcher.fetchers` but
are deprecated (removal in 2.0). See [migration-v1.md](migration-v1.md).
