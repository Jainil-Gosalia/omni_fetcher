# OmniFetcher — Product Philosophy

> This document defines what OmniFetcher *is*, what it *refuses to be*, and the
> rules we use to break ties. Every design decision, connector, and feature is
> tested against it. When code and this document disagree, one of them is wrong —
> and we decide which on purpose, not by accident.

---

## 1. North Star

**OmniFetcher is a data procurement layer: it takes data from any source and emits
it as a small, stable, typed contract that consumers can trust forever.**

The product is *the contract* — the canonical typed shape of the output — not the
connectors. Connectors are interchangeable plumbing; the contract is the moat.

When two values conflict, **the trustworthiness and stability of the output
contract wins** — over source breadth, over fidelity to any one source, over
short-term convenience.

## 2. Who It's For

OmniFetcher is built **for AI and agent developers first** — people who need to
feed heterogeneous data into LLMs, RAG pipelines, and agents and want it as clean,
predictable, typed chunks. Every other audience is welcome, but when priorities
collide, the AI/agent developer's needs decide.

## 3. The Mental Model

Think of OmniFetcher as a **Kafka-like stream of typed composition trees.**

- **Everything is a stream.** A bounded source (a PDF, a Notion page) is a stream
  that terminates; an unbounded source (logs, a Kafka topic) is a stream that
  doesn't. The stream procedurally grows a composition tree as data arrives.
- **The unit on the wire is a composition tree** whose leaves are *atoms*.
- A convenience `fetch(uri)` exists as **sugar**: it awaits a bounded stream to
  completion and returns the final tree, so the common case (grab one document)
  stays a single ergonomic call. `stream(uri)` is there for unbounded sources.

## 4. The Atomic Model

**An atom is the lowest *useful* composition artifact — not the point of absolute
irreducibility.**

Decomposition stops where further breakdown loses utility for most consumers.
*Text* is an atom; sentences, words, and letters are technically "lower" but less
useful for most work. Everything is a **recursive composition tree**:

```
PPTX → slides → { text, image, table, … }   ← leaves are atoms
```

- **The atom vocabulary is small and curated.** A new atom type is added only when
  content is genuinely reusable across multiple sources. One-off source quirks fall
  back to an existing atom or to metadata — they do **not** mint a new atom. A
  sprawling vocabulary is an untrustworthy contract.
- **Promotion is the escape hatch.** If content genuinely cannot be expressed with
  existing atoms, it *becomes* a new atom — under the high bar above.

## 5. The Output Contract & Its Invariants

These are promises. They do not bend per connector.

1. **Valid atoms or a typed error — never raw, partial, or unvalidated data.**
   Every result is either schema-valid output or a structured, typed error the
   consumer can branch on. Consumers never have to defensively parse our output.

2. **Deterministic in Phase 1.** The same input always yields the same atoms. Any
   probabilistic behavior lives in the explicitly-separate Phase 2 layer and never
   contaminates the deterministic core (see §10).

3. **Content and metadata are separate.** Atoms and composites carry *content*
   only. Everything that *describes* an artifact — author, timestamps, source IDs,
   permissions, temporal/ordering position, source-specific fields — lives in a
   distinct, typed **metadata channel** on every node.

4. **Consumer-selectable zoom, defaulting to the source's natural granularity.**
   "Zoom" is **depth in the composition tree** — how far to expand the source's
   natural semantic structure — and is selectable **per atom type** (e.g. text at
   sentence level while images stay whole). It is *not* arbitrary token/character
   windowing. With no zoom specified, OmniFetcher returns the source's natural
   top-level decomposition.

5. **The contract evolves additively.** New atoms and fields are additive; existing
   shapes never silently change meaning. Breaking changes are rare, explicitly
   versioned, and loud. *Today's consumer code keeps working tomorrow.*

## 6. Execution Model

- **Stateless core.** OmniFetcher is a pure transform: source in → typed
  compositions out. It holds no cursors, offsets, or accumulated trees. The
  **host/caller owns all state** — incremental position, stream accumulation, and
  capture cadence. This keeps it trivial to host (including as an MCP server) and
  free of cross-call leakage.
- **Temporal order is metadata.** Streamed/event data carries a timestamp plus a
  monotonic sequence number in the metadata channel to preserve order. The growing
  "order line" is the host's to accumulate; OmniFetcher just emits ordered nodes.
- **Integrity is designed-for, not built yet.** When fidelity/tamper-evidence is
  required, we use **content-addressed hash-linking** (a Merkle structure: each node
  carries a `content_hash`; event nodes carry `prev_hash`) — *not* a blockchain. A
  blockchain solves trustless multi-party consensus, a problem a single-trust-domain
  transform layer does not have, and it would be stateful (contradicting the
  stateless core). The metadata fields are reserved now; verification ships only when
  a concrete compliance/audit use case demands it.

## 7. Authentication & Trust

- **OmniFetcher never stores credentials.** Credentials or short-lived access tokens
  are injected by the caller at call time and used transiently. Token acquisition,
  refresh, and storage are the host's responsibility.
- **Auth is normalized, like data.** A small canonical set of auth types (bearer,
  api_key, basic, oauth2, aws) that every source maps onto — the same
  small-stable-contract ethos applied to credentials.
- **Permission-faithful, never escalates.** OmniFetcher can only ever see what the
  provided credential can see. It never broadens access, never serves one caller's
  data to another, and treats auth/permission failures as typed errors (never
  partial data). **Tenant isolation is absolute.**

## 8. Non-Goals — What OmniFetcher Refuses To Be

- **Not a writer.** OmniFetcher is **read-only procurement**. It acquires and
  normalizes data *from* sources; it never writes, mutates, or syncs back.
- **Not a transformer.** The core only *decomposes* into the canonical atom tree. It
  does not translate, summarize, reformat, or otherwise alter content. Semantic
  transformation is Phase 2 (see §10).
- **Not an embedding/vector engine.** It emits typed content, including clean text
  atoms. Vectorization is downstream infrastructure.
- **Not a chunking engine.** Zoom is semantic decomposition, not token-window
  chunking for embeddings — that's the consumer's job.
- **Not a store or database.** It is stateless and persists nothing.
- **Not an ETL/orchestration tool.** No scheduling, no pipelines, no DAGs. It is the
  extraction-and-normalization step others orchestrate.

## 9. Tie-Breaker Principles

When a decision is genuinely close, prefer in this order:

1. **Contract stability** over expressiveness.
2. **Correctness** (valid-or-typed-error) over best-effort coverage.
3. **Uniformity** over fidelity to any single source.
4. **Statelessness** over powerful-but-stateful.
5. **Determinism** over convenience (in the core).
6. **Partial but correct, explicit about gaps** over broad-but-silently-wrong.
   A new connector ships as a vertical slice that is fully correct for what it
   covers; unsupported surface returns a typed "unsupported" signal.

## 10. The Two Phases

- **Phase 1 — Structured procurement (now).** Deterministic decomposition of
  already-structured and easily-parsed sources into the canonical atom tree. Cheap,
  fast, testable, reproducible. This is the foundation everything else stands on.
- **Phase 2 — LLM-imposed structure (later).** Use LLMs to coerce *any* data into a
  caller-supplied Pydantic structure. This layer is **non-deterministic, costs
  money, and requires evaluation/guardrails**, so it sits cleanly *on top of* the
  Phase-1 atoms (extract a clean Text atom → LLM → target schema). It is always
  opt-in, always labeled, and never woven into the deterministic core.

The boundary between them is **determinism, not modality**: deterministic
extraction (PDF text, structured parsing) is core; model-based extraction (OCR on a
scan, audio→text transcription) is Phase 2.

## 11. Distribution

The primary distribution vector is an **MCP server**, turning OmniFetcher from "a
Python library" into "a capability any agent can plug in." The Pydantic contract
maps directly onto MCP tool/resource output. The stateless core and per-call
credential model exist in part to make this clean and multi-tenant-safe.

## 12. The Litmus Test

> **A consumer codes against the contract once, and never has to change that code
> when a new source is added or an existing source changes its API.**

If adding a source, or a source shifting its API, forces consumers to touch their
code, the contract failed to absorb the variability — and we have a philosophy
violation, not just a bug. Success is the *absence* of that churn, not the number
of connectors or installs.

---

## Decision Ledger

| # | Decision | Choice |
|---|----------|--------|
| 1 | Core identity | The output contract is the product |
| 2 | Primary user | AI/agent developers |
| 3 | Fetch guarantee | Valid atoms or typed error |
| 4 | Atom definition | Lowest *useful* composition artifact; recursive tree; promotion under a high bar |
| 5 | Determinism | Phase 1 deterministic; probabilistic work is Phase 2 |
| 6 | Atom governance | Small curated core, high bar to add |
| 7 | Granularity | Consumer-selectable zoom = tree depth, per atom type, default = natural |
| 8 | Content vs metadata | Separate typed metadata channel |
| 9 | Execution | Everything is a stream; `fetch()` is bounded-stream sugar |
| 10 | Snapshot vs event | Unified as compositions; temporal order in metadata |
| 11 | State | Stateless core; host owns state |
| 12 | Integrity | Content-addressed hash-linking (Merkle), not blockchain; deferred |
| 13 | Read/write | Read-only procurement |
| 14 | Extraction boundary | Determinism is the line |
| 15 | Embeddings | No — typed atoms only |
| 16 | Credentials | Never stored; injected per call |
| 17 | Auth model | Normalized canonical auth types |
| 18 | Permissions | Permission-faithful, never escalates, absolute tenant isolation |
| 19 | Connector coverage | Partial but correct, explicit about gaps |
| 20 | Contract evolution | Additive & backward-compatible by default |
| 21 | Success criterion | Consumers never touch code when sources change |
