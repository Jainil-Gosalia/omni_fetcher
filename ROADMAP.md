# Roadmap

Forward-looking plan for the next five releases. Everything here is
**connectors only** — new sources behind the existing v1 contract, no changes
to the core shape. Each release ships a *family over a shared spec* rather than
a one-off, following the rhythm the SQL query (v1.9–1.10) and streaming
(v1.2–1.6) families established: build the spec once, then let each new member
prove the abstraction the way MySQL did for `_sql_query`.

This is a direction, not a contract — order and membership can move as demand
lands. Ordering is **leverage-first**: releases that reuse a spec we already
have come before ones that introduce a new one.

See [CHANGELOG.md](CHANGELOG.md) for what has already shipped (through 1.11.0),
and [PHILOSOPHY.md](PHILOSOPHY.md) for the contract these connectors emit.

---

## v1.12 — Cloud object storage: GCS + Azure Blob

Complete the object-storage trio. Reuses the `s3` connector's shape wholesale:
an object is a bounded `fetch`, a prefix is a `collection` listing. This is
S3's second and third real customers proving the abstraction, not a fork —
exactly the move MySQL made against the SQL spec.

- `gs://bucket/key` — Google Cloud Storage (extra: `gcs`, `google-cloud-storage`)
- `az://container/blob` (+ `abfs://` alias) — Azure Blob (extra: `azure`,
  `azure-storage-blob`)
- Credentials via the host-injected `OMNI_FETCHER_GCS_*` / `OMNI_FETCHER_AZURE_*`
  convention the MCP server already uses; URI params remain a CLI fallback.
- **Leverage: high** (reuses S3 node-building + prefix listing).
  **Design risk: low.**

## v1.13 — SQL warehouses: Snowflake + BigQuery + DuckDB

The `_sql_query` spec's next customers: `?table=` / `?query=` / `?query_env=` /
`?limit=`, a `kind="query_result"` node with a `Table` atom, read-only enforced
by the engine, over-cap results degraded to a typed `partial`. DuckDB is the
ungated, stdlib-ish member (like `sqlite`); Snowflake and BigQuery sit behind
extras.

- `snowflake://account/database/schema` (extra: `snowflake`)
- `bigquery://project/dataset` (extra: `bigquery`)
- `duckdb:///path/to/db.duckdb` (no extra — bundled driver)
- Expected to grow **one** portability seam on `_sql_query`: a read-only
  strategy hook alongside the existing dialect-quote parameter. BigQuery has no
  transactions, so read-only is inherent rather than transaction-scoped —
  the seam the third database asks for, not a rewrite.
- **Leverage: very high** (our largest shared spec). **Design risk: low–medium**
  (BigQuery's dry-run / read-only semantics differ from the transactional DBs).

## v1.14 — Knowledge base & wiki: Obsidian + MediaWiki + Logseq

A new family for the "second brain" / documentation sources that feed RAG and
agent context — the strongest fit for the product's positioning. A note is a
bounded `fetch` (Markdown body as a `Text` atom with `TextFormat.MARKDOWN`,
frontmatter + resolved `[[wikilinks]]` + `#tags` as `source_extra` graph
facts); a vault or space is a `collection`.

- **Obsidian** — `obsidian://vault/note` or a vault folder path. Markdown +
  YAML frontmatter + `[[wikilinks]]` + backlinks.
- **MediaWiki** — `mediawiki://host/wiki/Title` and `*.wikipedia.org/wiki/Title`
  via the MediaWiki API; wikitext (or HTML) → Markdown, categories and links in
  `source_extra`. Reuses the HTML→Markdown precedent from `confluence`.
- **Logseq** — a vault of Markdown/Org blocks with block references; completes
  the personal-knowledge-base trio.
- New shared spec (`_wiki_notes`): frontmatter parsing, `[[wikilink]]`
  extraction and in-vault resolution, tag capture, all rendered to
  `TextFormat.MARKDOWN`. Obsidian and Logseq share it directly; MediaWiki layers
  wikitext conversion on top.
- **Leverage: low** (new spec). **Design risk: low–medium** (wikilink
  resolution across a vault; wikitext parsing). Highest strategic value.

## v1.15 — Cloud messaging streams: Kinesis + Pub/Sub + AMQP

Extends the `stream()` seam and `stream_with_restart` resume derivation (the
Kafka/Redis pattern): one `Result` per message, resume position in
`source_extra`, a clean close ends the stream without a spurious `Error`. Comes
with `sample` (the bounded-window MCP tool) for free.

- `kinesis://stream?shard=` — AWS Kinesis (extra: `aws`, aioboto3)
- `pubsub://project/subscription` — GCP Pub/Sub (extra: `gcp-pubsub`)
- `amqp://host/queue` (+ `amqps://`) — RabbitMQ / AMQP (extra: `amqp`, aio-pika)
- Adds resume branches to `retry.py` (shard iterator / ack-id / delivery-tag).
- **Leverage: high** (reuses streaming infra). **Design risk: low–medium**
  (per-broker ack / at-least-once semantics).

## v1.16 — NoSQL document stores: MongoDB + DynamoDB

Touches **both** seams and closes the CDC story for NoSQL. A bounded query
mirrors `elasticsearch`'s `search_results` → `json_document` children shape;
MongoDB change streams ride the `stream()` seam the way `postgres-cdc` does.

- `mongodb://host/db.collection?query=` (bounded) and
  `mongodb+changestream://…` (stream) — extra: `mongodb`, motor
- `dynamodb://table?key=` / `?query=` — extra: `aws`, aioboto3
- **Leverage: high** (reuses the Elasticsearch and Postgres-CDC shapes).
  **Design risk: low.**

---

## Deferred / alternatives

Strong candidates held back so each release stays a clean family. Any of these
can swap in if demand reweights the plan:

- **Collab & dev tools** — GitLab, Bitbucket, Zendesk, Salesforce, Teams,
  Discord. High raw demand, but each is bespoke with little shared-spec leverage,
  so they don't cluster into a family the way the releases above do.
- **Document files** — XLSX, Parquet, image OCR, EPUB. Extends the
  PDF/DOCX/PPTX/CSV family; low-risk, lower-impact than the cloud sources.
- **Observability** — Prometheus, Loki, Datadog, Splunk, CloudWatch Logs. A
  natural pairing with `tail` and `elasticsearch`.
- **Vector search** — Pinecone, Qdrant, pgvector. Considered and set aside: it
  is retrieval *infrastructure* rather than a content *source*, and the library
  decomposes rather than transforms (it will not embed), so a vector connector
  would ride on precomputed vectors or server-side embedding — a poorer fit for
  the contract than the knowledge-base family that replaced it.
