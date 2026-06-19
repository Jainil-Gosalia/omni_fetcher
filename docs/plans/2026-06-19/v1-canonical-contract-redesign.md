# Plan: OmniFetcher v1.0 — The Canonical Contract Redesign

> Source PRD: `docs/prds/2026-06-19/v1-canonical-contract-redesign.md`
> Philosophy: `PHILOSOPHY.md` (21 principles)

This plan is sliced as **tracer bullets**: each phase cuts end-to-end through all
seven modules and is verifiable on its own, rather than building one horizontal layer
at a time. The architecture (registry, auth, result envelope) is built *correctly from
Phase 1* so nothing is built only to be torn out later.

## Architectural decisions

Durable decisions that apply across all phases:

- **Output contract**: a single recursive `CompositionNode` tree is the output for
  every source. Leaves are the small canonical **atom** set: **Text, Image, Audio,
  Video, Table**. A node's semantic meaning (issue / page / message / file / record /
  event) is an advisory `kind` **string in metadata**, never a distinct type. No
  source-specific public schema types exist.
- **Metadata channel**: content and description are separate. Every node carries a
  typed metadata **common core** — stable `id`, `created`/`updated` timestamps,
  `author`, source `url`, `permissions`, **temporal position** (`timestamp` +
  monotonic `sequence`), and reserved `content_hash` / `prev_hash` — plus a
  **namespaced `source_extra`** validated per source. Atoms carry content only (no
  inline EXIF/ID3/author/id fields).
- **Result envelope**: boundary calls return `Result = success(tree) | partial(tree,
  gaps) | error(kind)`. Expected failures are **returned, not raised**; exceptions
  signal programmer error only and preserve their cause (chained, never flattened to a
  string). `gaps` is a typed list of what failed/was skipped.
- **Error taxonomy** (typed kinds): `auth_failed`, `permission_denied`, `not_found`,
  `unsupported`, `rate_limited`, `transient`, `parse_error`, `invalid_input`.
  `unsupported` is distinct from `not_found` and `transient`.
- **Execution model**: `stream(uri, *, auth, zoom) -> AsyncIterator[Result[CompositionNode]]`
  is the primitive. `fetch(uri, ...)` is base-provided sugar that collects a bounded
  stream into the final tree and returns a single `Result`. Connectors implement
  `stream()`.
- **Auth**: normalized canonical types — `bearer`, `api_key`, `basic`, `oauth2`,
  `aws`. Credentials are **injected per call**; no ambient `.env`/environment loading
  by default (opt-in only); no token caching/mutation on shared objects.
- **Registry**: a module-level, read-only **source-definition** lookup (routing
  `uri → fetcher`). No global mutable singleton; no shared auth/data state. The
  orchestrator is stateless per call.
- **Zoom**: a per-atom-type spec passed to `fetch`/`stream` (e.g. `{text: sentence,
  image: whole}`), defaulting to each source's natural granularity. It is **semantic
  tree depth**, not token/character windowing.
- **Versioning**: clean v1.0 break removing the ~50 source-specific schemas;
  `__version__` single-sourced from package metadata.
- **Preserved invariants** (already conformant): deterministic Phase-1 output,
  read-only procurement, no model-based extraction (OCR/transcription), no embeddings.

---

## Phase 1: Spine tracer — one local source, end-to-end

**User stories**: 1, 2, 3, 4, 5, 9, 16

### What to build

The thinnest complete path through the whole new architecture, using the simplest
bounded source (a local text/CSV file — deterministic, no network, no real auth). This
phase stands up skeletal-but-real versions of all seven modules: the canonical
`CompositionNode` + the Text and Table atoms + the typed metadata core (incl. reserved
`content_hash`/`prev_hash`), the `Result` envelope (`success`/`error`), the immutable
definition registry and routing, the per-call auth resolver (a no-op path for local
files, but the real interface), the fetcher protocol with base-provided `fetch()`, and
the stateless orchestrator. The local connector emits a canonical tree wrapped in a
`Result`. This proves the spine end-to-end and locks the contract types every later
phase builds on. See *Architectural decisions* for all shapes.

### Acceptance criteria

- [ ] Fetching a local text/CSV file returns `Result.success` whose payload is a
      `CompositionNode` tree with canonical atoms (Text and/or Table).
- [ ] Descriptive attributes appear only in the metadata core; atoms contain no inline
      descriptive fields.
- [ ] A missing/unreadable file returns a typed `error` (`not_found` / `parse_error`),
      not a raised exception.
- [ ] `fetch()` returns a single `Result`; no streaming interface is required of the
      caller.
- [ ] Routing goes through the immutable definition registry; no global mutable
      singleton holds state.
- [ ] Output is deterministic: identical input yields identical atoms.
- [ ] Unit tests for the contract schema module and the result envelope cover this path.

---

## Phase 2: Authed network connector + error / partial / unsupported

**User stories**: 4, 5, 6, 11, 16

### What to build

Add one authenticated network source (an HTTP/JSON or GitHub-style API) through the
same spine, so the auth resolver and the full error/partial behavior are exercised
end-to-end. Credentials are injected per call (no ambient load). This phase makes the
typed error taxonomy real (`auth_failed`, `permission_denied`, `not_found`,
`rate_limited`, `transient`), makes **partial** honest (when a sub-resource fails, the
result is `partial` carrying the tree plus typed gaps — never a `success` with silently
missing content), and introduces the `unsupported` signal for sub-features the
connector can't represent. See *Architectural decisions* for the auth and envelope
shapes.

### Acceptance criteria

- [ ] A successful authed fetch returns `Result.success` with a canonical tree;
      credentials are passed per call and never read from the ambient environment.
- [ ] Auth failure returns `error(auth_failed)`; insufficient scope returns
      `error(permission_denied)`; both are returned, not raised.
- [ ] A partial failure (one sub-resource fails) returns `Result.partial` with the
      partial tree and a typed gap describing what failed — never `success`.
- [ ] An unsupported sub-feature returns `unsupported` (as an `error` or a `partial`
      gap), distinct from `not_found`.
- [ ] No broad `except` flattens errors; internal wrapping preserves the original cause.
- [ ] Unit tests for the result envelope's error taxonomy and the auth resolver cover
      this path.

---

## Phase 3: Per-atom-type zoom

**User stories**: 10

### What to build

Introduce consumer-selectable zoom as a pure **zoom resolver** module and wire it
through `fetch()`/`stream()` on the Phase 1–2 connectors. Zoom is a per-atom-type
spec (e.g. text → sentence, image → whole) that controls **depth in the semantic
composition tree**; unspecified types default to the source's natural granularity. It
is never token/character windowing. See *Architectural decisions* for the zoom shape.

### Acceptance criteria

- [ ] Passing a per-atom-type zoom spec changes decomposition depth per type (e.g. text
      decomposes to sentences while images stay whole).
- [ ] Omitting zoom yields the source's natural granularity.
- [ ] Zoom expands the semantic tree; it never produces arbitrary token/character
      windows.
- [ ] The result is a valid `Result` at every zoom level.
- [ ] Unit tests for the zoom resolver cover per-type resolution, the natural default,
      and edge cases (unknown type, leaf atoms, max depth).

---

## Phase 4: Stream seam

**User stories**: 7, 8, 9

### What to build

Make `stream()` the real primitive and `fetch()` the sugar. The base provides
`fetch()` as "collect a bounded stream into the final tree"; the Phase 1–2 connectors
implement `stream()` and yield a terminating stream for bounded sources. Streamed items
carry temporal-ordering metadata (timestamp + monotonic sequence). This establishes the
seam that future unbounded connectors (out of scope here) will use. See *Architectural
decisions* for the stream signature.

### Acceptance criteria

- [ ] `stream(uri)` yields `Result[CompositionNode]` items and terminates for bounded
      sources.
- [ ] `fetch(uri)` returns the same final tree as collecting `stream(uri)`.
- [ ] Connectors implement only `stream()`; `fetch()` is inherited.
- [ ] Each streamed item carries a timestamp and monotonic sequence in metadata.
- [ ] Single-document consumers can still use `fetch()` without iterating a stream.

---

## Phase 5: Multi-tenant isolation proof + auth reconciliation

**User stories**: 11, 12, 13

### What to build

Harden and *prove* the multi-tenant guarantees. Confirm there is no residual shared
mutable state (registry is immutable; no token caching/mutation on shared objects),
add concurrency tests that run interleaved calls with different credentials and assert
no cross-contamination of data or auth, and reconcile the non-canonical
`google_service_account` auth type (map it under a canonical type or document it as the
single justified exception). See *Architectural decisions* for the auth/registry model.

### Acceptance criteria

- [ ] Concurrent calls with different credentials never observe each other's data or
      auth.
- [ ] No process-global mutable state holds credentials or fetched data; the registry
      is read-only after registration.
- [ ] OAuth2 tokens are never cached/mutated onto shared objects; refresh is the host's
      responsibility.
- [ ] Auth configuration is uniform across sources and limited to the canonical set;
      `google_service_account` is reconciled and documented.
- [ ] Tests for tenant isolation (auth resolver + registry) pass under concurrency.

---

## Phase 6: Connector migration — documents & web

**User stories**: 17

### What to build

Migrate the document and web connectors (pdf, docx, pptx, csv, http_url, rss, graphql)
onto the canonical contract using the shared node-mapping helper: each emits
`CompositionNode` + metadata, returns `Result`, sets an appropriate semantic `kind`,
implements `stream()`, and reports any uncovered surface via `partial`/`unsupported`
rather than silently. Preserves determinism and the extraction boundary (no OCR). See
*Architectural decisions*.

### Acceptance criteria

- [ ] Each listed connector returns a canonical `CompositionNode` tree wrapped in a
      `Result`.
- [ ] Each sets a sensible semantic `kind` and populates the metadata core uniformly.
- [ ] Source-specific fields live in namespaced `source_extra`, not in content.
- [ ] Uncovered sub-features are reported via `partial`/`unsupported`, never silently
      dropped.
- [ ] Existing integration tests are updated to assert canonical output and pass.

---

## Phase 7: Connector migration — cloud, SaaS & media

**User stories**: 17

### What to build

Migrate the remaining connectors (s3, google_drive, sharepoint, github, slack, jira,
confluence, notion, linear, youtube, audio) onto the canonical contract, same
requirements as Phase 6. This is the bulk of the SaaS/source-specific surface, where
the old per-source schemas were richest — their data is re-expressed as canonical atoms
+ metadata (`source_extra`) with a semantic `kind`. Preserves read-only and
determinism; no transcription. See *Architectural decisions*.

### Acceptance criteria

- [ ] Each listed connector returns a canonical `CompositionNode` tree wrapped in a
      `Result` and implements `stream()`.
- [ ] Former source-specific fields (e.g. issue/page/message attributes) are mapped to
      the metadata core + namespaced `source_extra`, not bespoke content types.
- [ ] Each sets a sensible semantic `kind`.
- [ ] Partial/unsupported surfaces are explicit, never silent.
- [ ] Integration tests updated to assert canonical output and pass.

---

## Phase 8: Clean break — remove source schemas, migration guide, versioning

**User stories**: 14, 15

### What to build

Complete the v1.0 break now that every connector emits the canonical contract: remove
the ~50 source-specific public schema classes and their exports, write a migration
guide mapping each removed schema's fields onto the canonical atoms + metadata,
single-source `__version__` from package metadata (resolving the `0.9.0` vs `0.11.2`
mismatch), bump to v1.0, and update README/CHANGELOG. Finalize the reserved
`content_hash`/`prev_hash` fields (populate `content_hash` where cheap; leave
verification out of scope). See *Architectural decisions*.

### Acceptance criteria

- [ ] No source-specific public schema classes remain; only the canonical contract is
      exported.
- [ ] A migration guide maps every removed schema onto the canonical atoms + metadata.
- [ ] `__version__` is single-sourced and reads `1.0.0`; README/CHANGELOG updated.
- [ ] `content_hash` is populated where cheap; `prev_hash` is reserved; no verification
      logic ships.
- [ ] Full test suite (contract schema, result envelope, zoom resolver, auth resolver +
      registry, plus connector integration tests) passes.
