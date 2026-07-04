# Issue #3 — Result envelope + error taxonomy (impl + tests)

## Parent Plan Phase
Phase 2

## What to build

Implement the `Result` envelope and typed error taxonomy from the plan's
*Architectural decisions*: `success(tree)`, `partial(tree, gaps)`, `error(kind)`, with
`gaps` as a typed list of what failed/was skipped. Provide the helpers that let
callers return expected failures instead of raising, and that preserve the original
cause when wrapping (chained, never flattened to a string). `unsupported` is a distinct
kind from `not_found` and `transient`.

## Acceptance criteria

- [ ] `Result` supports `success` / `partial` / `error` states.
- [ ] All taxonomy kinds exist; `unsupported` is distinct from `not_found`/`transient`.
- [ ] Expected failures are returned, not raised; exceptions reserved for programmer error.
- [ ] Error wrapping preserves the original cause (no string flattening).
- [ ] Unit tests cover every state, every error kind, and the no-raise guarantee.

## Blocked by

- Blocked by #1

## Phase addressed

- Phase 2
