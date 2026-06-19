# Issue #26 — Migrate `notion` connector to the canonical contract

## Parent Plan Phase
Phase 7

## What to build

Migrate the `notion` connector onto the canonical contract per the plan's *Architectural
decisions* and Phase 7. The former `NotionPage`/`NotionDatabase`/`NotionBlock`/etc. data
is re-expressed as canonical atoms + metadata: page blocks as Text/Image/Table atoms in
a composition tree, descriptive fields (created_by, last_edited_by, timestamps,
properties, url) in the metadata core + namespaced `source_extra`, with semantic `kind`
(e.g. `page`/`database`). Critically, **a failed sub-block fetch must yield `partial`
with a typed gap — never a `success` with silently-missing content** (the current
anti-pattern). Return a `Result`, implement `stream()` (bounded). Uses per-call auth.

## Acceptance criteria

- [ ] Returns a canonical `CompositionNode` tree wrapped in a `Result` (no `Notion*` types).
- [ ] Descriptive fields in metadata core + `source_extra`; content in atoms.
- [ ] Failed sub-block/row fetches yield `partial` with typed gaps, never silent `success`.
- [ ] Auth resolved per call; failures are typed errors, not raises.
- [ ] Implements `stream()`; `fetch()` inherited.
- [ ] Integration tests updated to assert canonical output and pass.

## Blocked by

- Blocked by #1
- (Recommended after #2, #6, and #7; buildable against #1's frozen interfaces with stubs.)

## Phase addressed

- Phase 7
