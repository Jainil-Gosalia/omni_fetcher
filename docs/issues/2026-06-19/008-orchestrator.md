# Issue #8 — Stateless orchestrator

## Parent Plan Phase
Phase 1

## What to build

Implement the stateless `OmniFetcher` orchestrator per the plan's *Architectural
decisions*: for each call, route via the registry, resolve per-call auth, invoke the
fetcher's `stream()`/`fetch()`, apply zoom, wrap in a `Result`, and merge
metadata/tags. It holds no state between calls. Build against the frozen interfaces
(#1); stub collaborating modules as needed.

## Acceptance criteria

- [ ] `fetch()`/`stream()` route → resolve auth → invoke → apply zoom → return `Result`.
- [ ] No state is retained between calls.
- [ ] Per-call auth override is honored; no ambient credentials are used by default.
- [ ] User-supplied tags merge into node metadata.

## Blocked by

- Blocked by #1

## Phase addressed

- Phase 1
