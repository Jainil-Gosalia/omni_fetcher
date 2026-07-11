# OmniFetcher Documentation

OmniFetcher fetches anything — a Jira issue, a PDF, an S3 object, a Slack
thread, a web page — and returns **the same typed shape every time**: a
`Result` envelope (`Success` / `Partial` / `Error`) wrapping a
`CompositionNode` tree of typed content atoms, a uniform metadata core, and
namespaced `source_extra` fields.

## Install

```bash
pip install omni-fetcher
```

Optional extras: `office` (DOCX/PPTX), `jira` / `confluence` (Atlassian
client), `dev` (tests, lint, typing). Everything else works with the core
install.

## Quick start

```python
import asyncio

from omni_fetcher.v1 import OmniFetcher, Success, builtin_registry


async def main() -> None:
    omni = OmniFetcher(builtin_registry())   # all built-in connectors

    result = await omni.fetch("README.md")
    if isinstance(result, Success):
        print(result.tree.metadata.kind)     # "file"


asyncio.run(main())
```

Or from the command line:

```bash
omni-fetcher v1 fetch README.md
omni-fetcher v1 fetch https://example.com/feed.xml --json
```

## Guides

- [Connectors](fetchers.md) — the 21 built-in connectors, URI shapes, zoom,
  retry, and writing your own.
- [Authentication](auth.md) — per-call credentials and the multi-tenant
  model.
- [Migration from 0.x](migration-v1.md) — field-by-field mapping from the
  removed source-specific schemas onto the canonical contract.
- The [README](../README.md) carries the full example gallery and the
  contract diagram.

## The legacy API

The pre-1.0 layer (`from omni_fetcher import OmniFetcher`) still works but
is **deprecated and will be removed in 2.0** — importing it emits a
`DeprecationWarning` pointing here. New code targets `omni_fetcher.v1`.
