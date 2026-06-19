# Issue #20 — Migrate `google_drive` connector to the canonical contract

## Parent Plan Phase
Phase 7

## What to build

Migrate the `google_drive` connector onto the canonical contract per the plan's
*Architectural decisions* and Phase 7. Using the shared node-mapping helper: emit a
`CompositionNode` tree of canonical atoms (a folder is a container node; files are
sub-trees), return a `Result`, set a semantic `kind`, place source-specific fields in
namespaced `source_extra`, implement `stream()` (bounded), and report uncovered surface
via `partial`/`unsupported`. Uses per-call auth (reconcile the former
`google_service_account` per #6). Preserve determinism and read-only.

## Acceptance criteria

- [ ] Returns a canonical `CompositionNode` tree wrapped in a `Result`.
- [ ] Sets a sensible semantic `kind`; metadata core populated uniformly.
- [ ] Source-specific fields live in namespaced `source_extra`, not in content.
- [ ] Auth resolved per call via the canonical/reconciled type; failures are typed errors.
- [ ] Implements `stream()`; `fetch()` inherited.
- [ ] Integration tests updated to assert canonical output and pass.

## Blocked by

- Blocked by #1
- (Recommended after #2, #6, and #7; buildable against #1's frozen interfaces with stubs.)

## Phase addressed

- Phase 7
