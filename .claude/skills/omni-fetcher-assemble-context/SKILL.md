---
name: omni-fetcher-assemble-context
description: Assemble a working context by pulling a set of related sources — Jira/Linear issues, GitHub PRs, Confluence/Notion pages, PDFs, log tails, a postgres:// or mysql:// query result, an S3 object — into one provenance-tagged bundle through omni-fetcher, honestly reporting what could not be fetched. Use when the user wants several related things gathered so you can answer or act over them together ("pull the epic and its linked PRs and the design doc", "gather this ticket, the customer's recent orders, and the runbook"), when they say assemble/gather/collect/compile context from multiple sources, or when a task needs many heterogeneous sources in front of the model at once. Builds on the `omni-fetcher` skill (which teaches the contract and how to discover sources); read that first for tool details.
---

# Assemble Context with OmniFetcher

The task: pull *these related sources* into your working context so you can
answer or act over them together — and be honest about what you could not get.
The deliverable is you, *informed*, with every piece traceable to its source and
every gap named. Not a file; context.

This skill orchestrates the `omni-fetcher` tools. It teaches the *workflow*; the
`omni-fetcher` skill teaches the contract (the `Result` envelope, atoms, zoom)
and how to discover what a source is. Read that skill for any tool detail; do not
duplicate its knowledge here.

## The procedure

**1. Resolve the source set.**
Take the URIs the user gave. If the ask is compound — "the epic *and its linked
PRs*", "the page *and the pages it links*" — fetch the anchor first, read the
*typed* links its metadata exposes (a Jira epic's issue links, a PR's linked
issues — not every raw hyperlink), and add those to the set. Expand **one level
only** and stop. Never turn this into a crawl; a plain list of URIs is fetched
as-is, no expansion.

**2. Decide bounded vs. unbounded per source — from the contract, not memory.**
A bounded source (document, database query, search) is fetched; an unbounded one
(log tail, Kafka/Redis stream, CDC feed) is *sampled* — `fetch()` on it returns
`unsupported` and would otherwise hang. Which is which is discoverable
(`list_sources` over MCP, the `bounded`/`stream` flag from `builtin_registry()`,
or the typed `unsupported` a stream returns). When unsure, discover; do not guess
from the scheme.

**3. Fetch each source, budgeting for context.**
- **Bounded** → `fetch(uri)`. If a source is large enough to dominate the window
  (a long PDF, a wide query), fetch it at a coarser `zoom` (e.g.
  `text=paragraph`) or bound it (`?limit=` on a SQL query) — and *say* you did.
- **Unbounded** → `sample(uri, max_items=N)` with a small `N` and a timeout; note
  the window and why it stopped (`max_items` / `timeout`).
- Never silently truncate. Coarsening or capping is fine; hiding that you did is
  not.

**4. Assemble the bundle — provenance first, stable order.**
Present the sources in a stable order (the anchor first for a compound ask, then
the order given). Head each piece with its `source_url` and the load-bearing
metadata (`kind`, `author`, `updated`), then its content. Every claim you later
make must be traceable to one of these sources — so keep the labels attached, and
keep content and metadata separate as the contract does.

**5. Report what failed — always.**
This is the one non-negotiable step. Read each result's state:
- `success` → include it.
- `partial` → include what came back, and list its typed `gaps` (what's missing).
- `error` → do **not** drop it silently; state the source, the `kind`
  (`not_found`, `auth_failed`, …), and that it is missing from the bundle.
State a **requested-vs-fetched count** ("gathered 4 of 5 sources; `notion://…`
returned `auth_failed`"). A context that hides a failed source is worse than an
incomplete one, because you will confidently reason over a hole you cannot see.

## How to call the tools

Use whichever channel is available; the typed `Result` JSON is identical either
way, and name the channel you used so the user can reproduce it.

- **MCP server connected** → the `fetch` / `sample` / `list_sources` tools.
  Credentials are host-configured (`OMNI_FETCHER_<SOURCE>_<FIELD>`); you never
  pass a token. An auth-requiring source with nothing configured returns
  `auth_failed` naming the env var — surface that to the user.
- **CLI (coding-agent context)** → `omni-fetcher v1 fetch "<uri>" --json`,
  `omni-fetcher v1 fetch "<uri>" --zoom text=paragraph --json`, and
  `omni-fetcher v1 stream "<uri>" --json --max-items N` for streams. Pass
  credentials as env-var *names* (`--auth-type basic --username-env … --password-env …`),
  never raw. Parse the JSON `Result` and read its `state`.

Read the tool/CLI specifics from the `omni-fetcher` skill; discover a specific
source's URI shape from its connector docstring.

## Output shape

A short synthesis the user (or you) can act on, backed by a labelled bundle:

```
Assembled 4 of 5 requested sources (1 could not be fetched).

## jira://issue/PROJ-42   [issue · updated 2026-07-18 · alice]
<content, or a faithful summary if it was coarsened — say which>

## github://.../pull/9    [pull_request · updated 2026-07-18 · bob]
<content>

## postgres://db/app?query=…   [query_result · 12 rows]
<the Table, or its first N rows if capped — say which>

## report.pdf             [file]
<content at text=paragraph zoom (coarsened to fit context)>

Could not fetch:
- notion://page/abc  →  auth_failed (set OMNI_FETCHER_NOTION_TOKEN)
```

Then answer the user's actual question over that bundle, citing which source each
fact came from.

## Rules

- **Discover, don't assume.** Whether a source exists, is installed, or streams
  is a runtime fact — check it (see the `omni-fetcher` skill's discovery
  commands) rather than trusting a memorized list.
- **Honesty over completeness.** Report every failure and every coarsening. A
  clean-looking bundle that silently dropped a source is a bug, not a feature.
- **One level of expansion, opt-in.** Follow only the typed links a compound ask
  implies, one hop. No crawling.
- **Read-only.** OmniFetcher never mutates a source, and neither does this skill.
- **Stateless credentials.** Over MCP the host holds them; over the CLI they are
  env-var names. Never hardcode or echo a secret.

## Not this skill's job

- Ranking or relevance-scoring the sources — assemble what you were asked for;
  which sources matter is the calling task's decision.
- Summarizing by default — summarize only when budget forces it, and say so;
  content fidelity first.
- Cross-source deduplication — note obvious overlap, but a real dedup pass is out
  of scope.
- Crawling or link-following beyond one level.
