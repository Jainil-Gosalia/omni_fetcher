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
