# 002 — Legacy layer `DeprecationWarning`

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 1

## What to build

Importing the legacy layer (`omni_fetcher.fetcher`, `omni_fetcher.fetchers.*`,
the legacy `OmniFetcher` from the top-level package) emits a single
`DeprecationWarning` per process naming the removal target (2.0), pointing at
`docs/migration-v1.md`, and including the standard silencing recipe. The v1
package (`omni_fetcher.v1.*`) never triggers it.

## Acceptance criteria

- [ ] First legacy import warns exactly once; subsequent imports are silent.
- [ ] `python -W error::DeprecationWarning -c "import omni_fetcher.v1"` exits 0.
- [ ] Warning text names 2.0, the migration guide, and how to silence.
- [ ] CHANGELOG gains a deprecation notice under [1.1.0].
- [ ] Existing legacy tests pass unchanged (warnings not promoted to errors).

## Blocked by

None — can start immediately.

## Phase addressed

- Phase 1
