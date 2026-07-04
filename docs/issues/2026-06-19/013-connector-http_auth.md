# Issue #13 — Migrate `http_auth` connector to the canonical contract

## Parent Plan Phase
Phase 6

## What to build

Migrate the `http_auth` connector onto the canonical contract per the plan's
*Architectural decisions* and Phase 6. Using the shared node-mapping helper: emit a
`CompositionNode` tree of canonical atoms, return a `Result`, set a semantic `kind`,
place source-specific fields in namespaced `source_extra`, implement `stream()`
(bounded), and report uncovered surface via `partial`/`unsupported` — never silently.
Uses per-call auth via the normalized auth resolver. Preserve determinism and read-only.

## Acceptance criteria

- [ ] Returns a canonical `CompositionNode` tree wrapped in a `Result`.
- [ ] Sets a sensible semantic `kind`; metadata core populated uniformly.
- [ ] Source-specific fields live in namespaced `source_extra`, not in content.
- [ ] Auth is resolved per call; auth failures return typed errors, not raises.
- [ ] Implements `stream()`; `fetch()` inherited.
- [ ] Integration tests updated to assert canonical output and pass.

## Blocked by

- Blocked by #1
- (Recommended after #2, #6, and #7; buildable against #1's frozen interfaces with stubs.)

## Phase addressed

- Phase 6
