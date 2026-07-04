# Issue #5 — Immutable definition registry (impl + tests)

## Parent Plan Phase
Phase 1

## What to build

Replace the global mutable singleton with a module-level, read-only **source-definition**
registry per the plan's *Architectural decisions*: it holds stateless definitions
(uri patterns, fetcher classes) and resolves `uri → fetcher`. No credentials, no
fetched data, no mutable shared state after registration.

## Acceptance criteria

- [ ] Registry holds only stateless source definitions; read-only after registration.
- [ ] Routing resolves a URI to the correct fetcher by pattern/priority.
- [ ] No process-global mutable state holds credentials or data.
- [ ] Unit tests cover routing correctness and registry immutability.

## Blocked by

- Blocked by #1

## Phase addressed

- Phase 1
- Phase 5
