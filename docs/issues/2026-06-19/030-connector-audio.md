# Issue #30 — Migrate `audio` connector to the canonical contract

## Parent Plan Phase
Phase 7

## What to build

Migrate the `audio` connector onto the canonical contract per the plan's *Architectural
decisions* and Phase 7. Using the shared node-mapping helper: emit a `CompositionNode`
with a canonical Audio atom (duration/format), move ID3-style descriptive fields
(artist, album, year, genre) into the metadata core + namespaced `source_extra`, return
a `Result`, set a semantic `kind`, implement `stream()` (bounded), and report uncovered
surface via `partial`/`unsupported`. Preserve the extraction boundary: **no
transcription**.

## Acceptance criteria

- [ ] Returns a canonical `CompositionNode` (Audio atom) wrapped in a `Result`.
- [ ] ID3/descriptive fields live in metadata core + `source_extra`, not in the atom.
- [ ] Sets a sensible semantic `kind`.
- [ ] No transcription is performed.
- [ ] Implements `stream()`; `fetch()` inherited.
- [ ] Integration tests updated to assert canonical output and pass.

## Blocked by

- Blocked by #1
- (Recommended after #2 and #7; buildable against #1's frozen interfaces with stubs.)

## Phase addressed

- Phase 7
