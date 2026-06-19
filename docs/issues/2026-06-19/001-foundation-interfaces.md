# Issue #1 — Foundation: frozen type & interface skeletons

## Parent Plan Phase
Phase 1

## What to build

The single foundational slice that unblocks all parallel work. Land **stub but
importable** versions of every shared type and interface defined in the plan's
*Architectural decisions*, so every other module and connector can be built and
type-checked concurrently against frozen contracts:

- `CompositionNode` (recursive tree) and the 5 canonical atoms (Text, Image, Audio,
  Video, Table).
- `Metadata` — typed common core (id, created/updated, author, url, permissions,
  temporal position = timestamp + sequence, reserved `content_hash`/`prev_hash`) +
  namespaced `source_extra`.
- `Result = success(tree) | partial(tree, gaps) | error(kind)` and the error-kind enum
  (`auth_failed`, `permission_denied`, `not_found`, `unsupported`, `rate_limited`,
  `transient`, `parse_error`, `invalid_input`).
- The `BaseFetcher` protocol signatures: `stream(uri, *, auth, zoom)` and `fetch(...)`.
- The `AuthResolver` and `Registry` interfaces, and the `ZoomSpec` type.

Implementations may be stubs/`NotImplementedError`; the goal is frozen, importable
interfaces — not behavior.

## Acceptance criteria

- [ ] All shared types/interfaces from *Architectural decisions* exist and import cleanly.
- [ ] The package type-checks (mypy/ruff) with the new skeletons.
- [ ] No behavior is required; stubs are acceptable.
- [ ] Downstream issues can import these names without further changes.

## Blocked by

- None — must merge first; unblocks all other issues.

## Phase addressed

- Phase 1
