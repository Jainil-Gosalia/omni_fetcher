# Issue #7 — Fetcher protocol + base fetch() + shared node-mapping helper

## Parent Plan Phase
Phase 4

## What to build

Implement the execution seam from the plan's *Architectural decisions*: `stream(uri, *,
auth, zoom) -> AsyncIterator[Result[CompositionNode]]` as the primitive, and a
base-provided `fetch()` that collects a bounded stream into the final tree and returns
a single `Result`. Streamed items carry temporal-ordering metadata (timestamp +
monotonic sequence). Provide the **shared node-mapping helper** that connectors use to
emit canonical `CompositionNode`s + metadata consistently.

## Acceptance criteria

- [ ] `stream()` is the primitive; base `fetch()` collects a bounded stream.
- [ ] A bounded source's `fetch()` equals collecting its `stream()`.
- [ ] Streamed items carry timestamp + monotonic sequence in metadata.
- [ ] The shared node-mapping helper produces canonical nodes/metadata for connectors.
- [ ] Connectors need implement only `stream()`.

## Blocked by

- Blocked by #1

## Phase addressed

- Phase 4
