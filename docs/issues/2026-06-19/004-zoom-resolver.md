# Issue #4 — Zoom resolver (impl + tests)

## Parent Plan Phase
Phase 3

## What to build

Implement the pure zoom resolver from the plan's *Architectural decisions*: given a
per-atom-type `ZoomSpec` and a node, compute the decomposition depth per atom type.
Unspecified types default to the source's natural granularity. Zoom is **semantic tree
depth**, never token/character windowing.

## Acceptance criteria

- [ ] Per-atom-type depth resolution (e.g. text→sentence while image→whole).
- [ ] Omitted types resolve to the source's natural granularity.
- [ ] No token/character windowing is produced.
- [ ] Pure function (no I/O, deterministic).
- [ ] Unit tests cover per-type resolution, natural default, and edge cases (unknown
      type, leaf atoms, max depth).

## Blocked by

- Blocked by #1

## Phase addressed

- Phase 3
