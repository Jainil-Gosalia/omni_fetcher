# Issue #9 — Multi-tenant isolation proof

## Parent Plan Phase
Phase 5

## What to build

Prove the multi-tenant guarantees end-to-end per the plan's Phase 5. Add concurrency
tests that interleave calls with different credentials and assert no cross-contamination
of data or auth, confirm no residual shared mutable state, and verify the
`google_service_account` reconciliation. This is the verification join for the auth +
registry + orchestrator work.

## Acceptance criteria

- [ ] Concurrent calls with different credentials never observe each other's data/auth.
- [ ] No process-global mutable state holds credentials or fetched data.
- [ ] OAuth2 tokens are never cached/mutated onto shared objects.
- [ ] Isolation tests (auth resolver + registry + orchestrator) pass under concurrency.

## Blocked by

- Blocked by #5
- Blocked by #6
- Blocked by #8

## Phase addressed

- Phase 5
