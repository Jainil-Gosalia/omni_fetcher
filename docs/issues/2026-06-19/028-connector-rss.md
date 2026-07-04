# Issue #28 — Migrate `rss` connector to the canonical contract

## Parent Plan Phase
Phase 6

## What to build

Migrate the `rss` connector onto the canonical contract per the plan's *Architectural
decisions* and Phase 6. A feed is a natural container node whose items are sub-trees.
Using the shared node-mapping helper: emit that `CompositionNode` tree of canonical
atoms (item content as Text atoms), return a `Result`, set a semantic `kind` (e.g.
`feed`/`feed_item`), place source-specific fields in namespaced `source_extra`,
implement `stream()` (bounded), and report uncovered surface via `partial`/`unsupported`.
Preserve determinism and read-only.

## Acceptance criteria

- [ ] Returns a canonical `CompositionNode` tree (feed → items) wrapped in a `Result`.
- [ ] Sets a sensible semantic `kind`; metadata core populated uniformly.
- [ ] Source-specific fields live in namespaced `source_extra`, not in content.
- [ ] Uncovered sub-features reported via `partial`/`unsupported`, never silently.
- [ ] Implements `stream()`; `fetch()` inherited.
- [ ] Integration tests updated to assert canonical output and pass.

## Blocked by

- Blocked by #1
- (Recommended after #2 and #7; buildable against #1's frozen interfaces with stubs.)

## Phase addressed

- Phase 6
