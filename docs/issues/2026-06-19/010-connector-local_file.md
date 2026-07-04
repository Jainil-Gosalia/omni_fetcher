# Issue #10 — Migrate `local_file` connector to the canonical contract

## Parent Plan Phase
Phase 1

## What to build

Migrate the `local_file` connector onto the canonical contract per the plan's
*Architectural decisions*. This is the spine-tracer connector (simplest, deterministic,
no real auth): using the shared node-mapping helper, emit a `CompositionNode` tree of
canonical atoms (Text/Table), return a `Result`, set a semantic `kind`, place
source-specific fields in namespaced `source_extra`, implement `stream()` (bounded),
and report uncovered surface via `partial`/`unsupported`. Preserve determinism and
read-only.

## Acceptance criteria

- [ ] Returns a canonical `CompositionNode` tree wrapped in a `Result`.
- [ ] Sets a sensible semantic `kind`; metadata core populated uniformly.
- [ ] Source-specific fields live in namespaced `source_extra`, not in content.
- [ ] Missing/unreadable file returns a typed `error`, not a raised exception.
- [ ] Implements `stream()`; `fetch()` inherited.
- [ ] Integration tests updated to assert canonical output and pass.

## Blocked by

- Blocked by #1
- (Recommended after #2 and #7; buildable against #1's frozen interfaces with stubs.)

## Phase addressed

- Phase 1
