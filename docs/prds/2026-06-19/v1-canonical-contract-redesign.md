# PRD — OmniFetcher v1.0: The Canonical Contract Redesign

**Date:** 2026-06-19
**Status:** Draft
**Related:** `PHILOSOPHY.md` (the 21 product principles this PRD operationalizes)

---

## Problem Statement

OmniFetcher's promise is a *data procurement layer*: any source in, a small, stable,
trustworthy typed contract out. But the v0.11 code is **connector-centric, not
contract-centric**, so it breaks that promise in four ways a consumer feels directly:

1. **The contract isn't stable or small.** The public API is ~50 source-specific
   schemas (`GitHubIssue`, `NotionPage`, `JiraIssue`, `SlackMessage`, …). A consumer
   handling "an issue" writes different code for every source, and the API surface
   grows with every connector added. Switching or adding a source forces consumers to
   change their code — the exact churn the product exists to eliminate.

2. **Output can't be trusted without defensive parsing.** `fetch()` raises exceptions
   (flattened to bare strings, losing type and chain), and several connectors return
   schema-valid trees that are silently incomplete when a sub-fetch fails. A consumer
   can receive a "successful" object that is quietly missing data.

3. **It can't handle continuous data.** The only primitive is a pull `fetch()`; there
   is no streaming interface, so logs, Kafka, and Elasticsearch — explicitly on the
   roadmap — are unreachable.

4. **It isn't safe to host for multiple callers.** Credentials are loaded from the
   ambient environment and held on shared, mutable objects, and the registry is a
   process-global singleton. A single shared instance serving many callers would leak
   one caller's credentials/data to another — blocking the MCP distribution plan.

Descriptive attributes (author, timestamps, IDs, permissions) are also mixed into
content objects, and there is no way for a consumer to ask for a different granularity
of decomposition.

## Solution

Replace the source-shaped public API with a **single canonical output contract** and a
**typed result envelope**, add a **streaming primitive**, and make the core
**stateless and per-call credential-injected** so it is safe to host.

From the consumer's perspective:

- Every source returns the **same structural type**: a `CompositionNode` tree whose
  leaves are a small, fixed set of **atoms** (Text, Image, Audio, Video, Table).
  Semantic meaning ("this node is an issue/page/message") is an **advisory label in
  metadata**, never a distinct type. You write ingestion code once; it works for every
  source, today and for sources added later.
- **Content and descriptive metadata are separated.** Every node carries a typed
  metadata core (id, timestamps, author, url, permissions, temporal position,
  reserved `content_hash`/`prev_hash`) plus a namespaced `source_extra` for the long
  tail. Atoms carry content only.
- Calls return a **`Result`**: `success(tree)`, `partial(tree, gaps)`, or
  `error(kind)`. Expected failures are returned as typed values you branch on —
  never raised, never silently partial. "Unsupported" is its own signal, distinct
  from "not found" and "transient."
- **`stream(uri)`** is the primitive (an async iterator of `Result` items);
  **`fetch(uri)`** is sugar that collects a bounded stream into the final tree.
- You can request **per-atom-type zoom** (e.g. text at sentence level, images whole),
  defaulting to each source's natural granularity.
- **Credentials are injected per call.** Nothing is loaded from the environment by
  default and nothing mutable is shared, so the same process can safely serve many
  callers (e.g. as an MCP server).

This is a **clean v1.0 break**: source-specific schemas are removed, with a migration
guide mapping the old types onto the canonical contract.

## User Stories

1. **As an AI/agent developer, I want every source to return the same canonical
   composition-tree type, so that I write ingestion code once and reuse it across all
   sources.**
   **Acceptance Criteria:**
   - Single Public Type — A single `CompositionNode` tree is the output for every
     source; no source-specific schema classes are publicly exported.
   - Canonical Atoms — Leaf nodes are drawn from the small fixed atom set (Text, Image,
     Audio, Video, Table); no connector introduces its own content type.
   - Semantic Label — A node's semantic kind (issue, page, message, file, …) is an
     advisory string in metadata, not a type.
   - Source-Swap Invariance — Changing the source URI (e.g. a GitHub issue → a Jira
     issue) requires no change to consumer code that reads atoms + metadata.

2. **As an AI/agent developer, I want descriptive attributes separated from content,
   so that I read content uniformly and consult metadata only when I need to.**
   **Acceptance Criteria:**
   - Content-Only Atoms — Atoms and composites carry content only; no inline
     author/timestamp/EXIF/ID3/ID fields.
   - Typed Metadata Core — Every node carries a typed metadata object with a common
     core: stable id, created/updated timestamps, author, source url, permissions,
     temporal position (timestamp + sequence), and reserved `content_hash`/`prev_hash`.
   - Namespaced Extras — Source-specific descriptive data lives in a namespaced
     `source_extra`, validated per source, never mixed into content.

3. **As an AI/agent developer, I want a small, governed atom vocabulary, so that the
   contract stays stable and learnable.**
   **Acceptance Criteria:**
   - Enumerated Set — The canonical atom types are explicitly enumerated and documented.
   - High Bar — A new atom is added only when content is genuinely reusable across
     multiple sources; one-off source data goes to metadata, not a new atom.
   - No Source Atoms — No atom type is named after or specific to a single source.

4. **As an AI/agent developer, I want calls to return a typed Result instead of
   raising, so that I branch on a value and never defensively parse output.**
   **Acceptance Criteria:**
   - Result Envelope — `fetch()`/`stream()` return `Result`: `success(tree)`,
     `partial(tree, gaps)`, or `error(kind)`.
   - No Raise For Expected Failures — Auth failure, not found, unsupported, rate limit,
     transient, parse, and invalid-input outcomes are returned, not raised.
   - Exceptions Are Bugs — Raised exceptions indicate programmer error only; internal
     error wrapping preserves the original cause (chained, not flattened to a string).
   - Typed Error Kind — `error` carries a typed kind from the taxonomy, not a free
     string.

5. **As an AI/agent developer, I want partial results to be explicit, so that I never
   trust a silently-incomplete tree.**
   **Acceptance Criteria:**
   - Explicit Partial — When some sub-content fails or is skipped, the result is
     `partial`, carrying the tree it could build plus a typed list of gaps (what
     failed and why).
   - No Silent Swallowing — Connectors never catch a sub-fetch error and return it as
     `success`; a degraded fetch is always `partial` or `error`.

6. **As an AI/agent developer, I want "unsupported" to be a distinct typed signal, so
   that I handle it differently from "not found" or a transient error.**
   **Acceptance Criteria:**
   - Distinct Kind — `unsupported` is its own error kind, separate from `not_found`
     and `transient`.
   - Sub-Feature Honesty — A connector that can't handle a particular sub-resource
     returns `unsupported` (or `partial` with an `unsupported` gap), never a misleading
     `not_found` or a silent skip.

7. **As an AI/agent developer, I want `stream(uri)` to yield results incrementally, so
   that I can process unbounded or very large sources.**
   **Acceptance Criteria:**
   - Stream Primitive — `stream(uri, *, auth, zoom)` returns an async iterator of
     `Result[CompositionNode]` items.
   - Bounded Terminates — Bounded sources end the stream; unbounded sources do not.
   - Ordering Metadata — Each streamed item carries a timestamp and a monotonic
     sequence number in metadata to preserve temporal order.

8. **As a connector author, I want to implement only the streaming primitive, so that
   `fetch()` comes for free and behaves consistently.**
   **Acceptance Criteria:**
   - Single Method — A connector implements `stream()`; the base provides `fetch()` as
     "collect the bounded stream into the final tree."
   - Shared Mapping Helper — A shared helper produces canonical `CompositionNode`s and
     metadata so all connectors emit consistent output.

9. **As an AI/agent developer, I want `fetch(uri)` to stay a simple single call for
   bounded sources, so that grabbing one document is ergonomic.**
   **Acceptance Criteria:**
   - Sugar Preserved — `fetch(uri)` returns a single `Result` whose success payload is
     the final composition tree.
   - No Async-Iteration Tax — Single-document consumers do not have to iterate a stream.

10. **As an AI/agent developer, I want to request decomposition depth per atom type,
    so that I get the granularity my use case needs.**
    **Acceptance Criteria:**
    - Per-Type Spec — Zoom is specified per atom type (e.g. text→sentence, image→whole).
    - Natural Default — Unspecified types default to the source's natural granularity.
    - Semantic, Not Windowed — Zoom is depth in the semantic composition tree, never
      token/character windowing.
    - Valid At Any Zoom — The result is a valid `Result` at every zoom level.

11. **As an MCP host operator, I want per-call credential injection with no ambient
    loading, so that one caller's credentials never serve another.**
    **Acceptance Criteria:**
    - Per-Call Creds — Credentials are passed per call as the primary path.
    - No Ambient Load — `.env`/environment auto-loading is off by default (opt-in only).
    - No Shared Mutation — Tokens are never cached or mutated onto shared objects;
      token refresh is the host's responsibility.
    - Tenant Isolation — Concurrent calls with different credentials never observe each
      other's data or auth.

12. **As an MCP host operator, I want the registry to hold only immutable definitions,
    so that nothing mutable or credential-bearing is shared across callers.**
    **Acceptance Criteria:**
    - Definitions Only — The registry holds stateless source definitions (patterns,
      fetcher classes) and is read-only after registration.
    - No Shared Auth/Data — No process-global mutable state holds credentials or
      fetched data.
    - Stateless Orchestrator — A fetch carries no state between calls.

13. **As an AI/agent developer, I want normalized auth types, so that I configure
    authentication the same way across every source.**
    **Acceptance Criteria:**
    - Canonical Set — Auth is one of the canonical types (bearer, api_key, basic,
      oauth2, aws).
    - Uniform Config — Every source maps onto the same auth configuration shape.
    - Exceptions Reconciled — Any non-canonical type (e.g. google_service_account) is
      either mapped under a canonical type or documented as the single justified
      exception.

14. **As a library maintainer, I want `content_hash`/`prev_hash` reserved in metadata,
    so that future tamper-evidence is purely additive.**
    **Acceptance Criteria:**
    - Reserved Fields — Both fields exist in the metadata schema from v1.0.
    - Cheap Population — `content_hash` (a Merkle hash over a node's children) is
      populated where it's cheap; `prev_hash` is reserved for event/stream nodes.
    - No Verification Yet — No verification logic ships; the fields are forward-compat
      only.

15. **As a library maintainer, I want a clean v1.0 break with a migration guide, so
    that the contract finally delivers the litmus test.**
    **Acceptance Criteria:**
    - Schemas Removed — Source-specific public schemas are removed.
    - Migration Guide — A guide maps each removed schema's fields onto the canonical
      atoms + metadata.
    - Versioning — Major version bump; `__version__` is single-sourced from package
      metadata (resolving the existing `0.9.0` vs `0.11.2` mismatch).

16. **As an AI/agent developer, I want Phase-1 output to stay deterministic, read-only,
    and free of model inference, so that results are reproducible and safe.**
    **Acceptance Criteria:**
    - Deterministic — Same input yields the same atoms.
    - Read-Only — No source is written, mutated, or synced.
    - No Model Extraction — No OCR, transcription, or embeddings in the core; such
      extraction remains out of scope (Phase 2).

17. **As a connector author, I want all 21 existing connectors migrated to the
    canonical contract, so that the break is complete and no connector is half-migrated.**
    **Acceptance Criteria:**
    - Full Coverage — All 21 current (bounded) connectors emit the canonical
      `CompositionNode` + metadata and return `Result`.
    - Consistent Semantics — Each connector sets an appropriate semantic `kind` and
      populates the metadata core uniformly.
    - Partial Honesty — Connectors that can only cover part of a source surface
      report the rest via `unsupported`/`partial`, never silently.

## Implementation Decisions

**Output contract (Cluster A)**
- The canonical output is a single **`CompositionNode`** type forming a recursive tree;
  leaves are the small canonical **atom** set (Text, Image, Audio, Video, Table).
- A node's semantic meaning is an **advisory `kind` string in metadata**, not a type.
  No source-specific or semantic composite *types* are introduced.
- **Content vs metadata are separate channels.** Metadata is a typed common core
  (stable id, created/updated timestamps, author, source url, permissions, temporal
  position = timestamp + monotonic sequence, reserved `content_hash`/`prev_hash`) plus
  a **namespaced `source_extra`** validated per source. Existing inline descriptive
  fields on atoms (EXIF on images, ID3 on audio) move into metadata.
- The atom vocabulary is governed by a high bar: new atoms only when reusable across
  multiple sources; one-off data goes to metadata.

**Result envelope (Cluster B)**
- Boundary calls return a **`Result`** union: `success(tree)`, `partial(tree, gaps)`,
  `error(kind)`. Gaps are a typed list describing what failed/was skipped.
- **Error taxonomy** (typed kinds): `auth_failed`, `permission_denied`, `not_found`,
  `unsupported`, `rate_limited`, `transient`, `parse_error`, `invalid_input`.
- Expected failures are **returned, not raised**. Exceptions signal programmer error
  only. Internal error wrapping **preserves the cause** (chained); no broad
  `except Exception` that flattens to a string.
- Connectors never convert a degraded fetch into `success`; degraded fetches are
  `partial` or `error`.

**Execution model (Cluster C)**
- **`stream(uri, *, auth, zoom) -> AsyncIterator[Result[CompositionNode]]`** is the
  primitive. **`fetch(uri, …)`** is provided by the base as "collect a bounded stream
  into the final tree," returning a single `Result`.
- Connectors implement `stream()`; bounded connectors yield a terminating stream.
- A shared mapping helper builds canonical nodes/metadata so connectors stay consistent.

**Multi-tenant safety (Cluster D)**
- The registry holds **immutable source definitions only** (module-level, read-only
  after registration). The global mutable singleton is removed.
- **Credentials are injected per call**; ambient `.env`/environment loading is off by
  default (opt-in). No token caching/mutation on shared objects.
- Auth is **normalized** to the canonical set (bearer, api_key, basic, oauth2, aws);
  `google_service_account` is reconciled (mapped under a canonical type or documented
  as the lone exception).
- The orchestrator is stateless per call; tenant isolation is absolute.

**Zoom (#7)**
- Zoom is a **per-atom-type specification** passed to `fetch`/`stream` (e.g.
  `{text: sentence, image: whole}`), defaulting to the source's **natural
  granularity**. It is **semantic tree depth**, not token/character windowing. A pure
  **zoom resolver** computes per-node depth from the spec.

**Modules (deep modules with stable interfaces)**
1. **Contract schema** — atoms, `CompositionNode`, `Metadata` (core + `source_extra`,
   reserved hash fields). The deepest, most-stable module.
2. **Result envelope** — `Result` union + typed error/`unsupported` taxonomy.
3. **Zoom resolver** — pure `(zoom_spec, node) → depth` function.
4. **Registry** — immutable definition lookup / routing.
5. **Auth resolver** — normalized types, per-call resolution, no ambient/shared state.
6. **Fetcher protocol** — `stream()` primitive + base-provided `fetch()` + shared
   node-mapping helper.
7. **OmniFetcher orchestrator** — stateless route → invoke → zoom → wrap in `Result` →
   merge metadata/tags.

**Versioning / migration**
- Clean v1.0 break; source-specific schemas removed; migration guide + CHANGELOG;
  `__version__` single-sourced from package metadata.

## Testing Decisions

**What makes a good test here:** tests assert **external behavior at module
boundaries**, not internal implementation. A test should survive a refactor that keeps
the contract. Concretely: given an input (or a recorded source fixture), assert the
shape and invariants of the returned `Result`/`CompositionNode` — not private helpers
or call sequences.

**Modules to test (confirmed):**
- **Contract schema** — validation rules; the composition tree; metadata core +
  namespaced extras; content-vs-metadata separation (no inline descriptive fields on
  atoms); tag merging; `content_hash` population.
- **Result envelope + error taxonomy** — every state (`success`/`partial`/`error`);
  each typed error kind; `unsupported` distinct from `not_found`/`transient`; the
  no-raise-for-expected-failures guarantee; cause preservation.
- **Zoom resolver** — per-atom-type depth resolution; natural-default behavior;
  semantic-not-windowed behavior; edge cases (unknown type, leaf atoms, max depth).
- **Auth resolver + registry** — per-call credential resolution; **no ambient
  leakage** and tenant isolation (concurrent differing-credential calls don't
  cross-contaminate); normalized auth mapping; routing correctness; registry
  immutability.

**Prior art:** mirror the existing layout under `tests/` (`tests/core`,
`tests/fetchers`, `tests/schemas`, with `conftest.py` fixtures and `pytest-asyncio`
auto mode already configured in `pyproject.toml`). Connector behavior continues to be
covered by the existing integration tests (`tests/integration/`), extended to assert
connectors emit canonical nodes and honest `partial`/`unsupported` results.

## Out of Scope

- **Streaming connectors** (logs, Kafka, Elasticsearch, CDC). The `stream()` *seam*
  ships in v1.0; concrete unbounded connectors are a follow-up PRD.
- **Phase 2 LLM-imposed structure** (coercing arbitrary data into a caller-supplied
  Pydantic schema).
- **Model-based extraction** — OCR, audio/video transcription. Remains Phase 2.
- **Embeddings / vectorization.** Never in scope.
- **Tamper-evidence verification.** Hash fields are reserved; verification is deferred
  until a concrete compliance/audit use case exists.
- **Write-back / sync.** OmniFetcher stays read-only procurement.
- **Backward-compatibility shims for the removed source-specific schemas.** This is a
  clean break with a migration guide, not a deprecation period.

## Further Notes

- This PRD operationalizes `PHILOSOPHY.md`. Mapping: Cluster A → decisions #1, #4, #6,
  #8, #21; Cluster B → #3, #19; Cluster C → #9, #10; Cluster D → #11, #16, #17, #18;
  plus zoom (#7) and reserved hashing (#12, deferred).
- **Preserved as-is** (already conformant): determinism (#5), read-only (#13), the
  extraction boundary (#14), no embeddings (#15), and the existing atom/composite
  *design* (which becomes the basis for the canonical contract rather than being
  discarded).
- **Suggested build sequence:** Cluster A (the contract) is the keystone — the result
  envelope, metadata channel, and zoom all depend on the contract type existing first.
  Then Cluster B (envelope) on top of it. Clusters C (stream seam) and D (tenant
  safety) are largely independent and can proceed in parallel. Connector migration
  (story 17) follows once the contract + envelope + base protocol are stable.
- **One unresolved design detail for implementation time** (noted in the gap analysis,
  not blocking this PRD): append-only event streams vs. evolving-state sources sampled
  over time behave differently; how an evolving-state source represents its history
  should be decided when the first streaming connector is built.
- The `google_service_account` auth type and the `__version__` mismatch are explicitly
  folded in (stories 13 and 15) so the redesign also closes those smaller known issues.
