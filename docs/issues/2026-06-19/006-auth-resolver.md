# Issue #6 — Auth resolver: normalized, per-call, no shared state (impl + tests)

## Parent Plan Phase
Phase 2

## What to build

Implement the normalized auth resolver per the plan's *Architectural decisions*:
canonical types (`bearer`, `api_key`, `basic`, `oauth2`, `aws`), credentials injected
**per call** (no ambient `.env`/environment loading by default — opt-in only), and no
token caching/mutation on shared objects (refresh is the host's responsibility).
Reconcile the non-canonical `google_service_account` type (map under a canonical type
or document as the single justified exception).

## Acceptance criteria

- [ ] All canonical auth types resolve to correct headers/signing.
- [ ] Credentials are injected per call; no ambient load by default.
- [ ] No token caching/mutation on shared objects.
- [ ] `google_service_account` is reconciled and documented.
- [ ] Unit tests cover per-call resolution, no-ambient-leakage, and normalized mapping.

## Blocked by

- Blocked by #1

## Phase addressed

- Phase 2
- Phase 5
