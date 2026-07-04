# Issue #29 — Migrate `youtube` connector to the canonical contract

## Parent Plan Phase
Phase 7

## What to build

Migrate the `youtube` connector onto the canonical contract per the plan's *Architectural
decisions* and Phase 7. Using the shared node-mapping helper: emit a `CompositionNode`
tree (Video atom; existing captions/description as Text atoms), return a `Result`, set a
semantic `kind` (e.g. `video`/`playlist`), place source-specific fields in namespaced
`source_extra`, implement `stream()` (bounded), and report uncovered surface via
`partial`/`unsupported`. Preserve the extraction boundary: read **existing** captions
only — **no transcription/ASR**.

## Acceptance criteria

- [ ] Returns a canonical `CompositionNode` tree wrapped in a `Result`.
- [ ] Sets a sensible semantic `kind`; metadata core populated uniformly.
- [ ] Source-specific fields live in namespaced `source_extra`, not in content.
- [ ] No transcription; only existing captions are read (absence reported, not generated).
- [ ] Implements `stream()`; `fetch()` inherited.
- [ ] Integration tests updated to assert canonical output and pass.

## Blocked by

- Blocked by #1
- (Recommended after #2 and #7; buildable against #1's frozen interfaces with stubs.)

## Phase addressed

- Phase 7
