# 011 — Transient error-classification audit

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 4

## What to build

Make retry meaningful by ensuring connectors classify retryable conditions
consistently: HTTP 429 → `RATE_LIMITED`; 5xx / timeouts / connection resets →
`TRANSIENT`; 401/403 → `AUTH_FAILED`; 404 → `NOT_FOUND`. Audit every v1
connector's classification path, fix divergences, and add per-connector
tests for the 429 and 5xx cases where missing.

## Acceptance criteria

- [ ] A classification table (status/exception → ErrorKind) documented in
      the errors module docstring.
- [ ] Every httpx-based connector maps 429 and 5xx per the table, with tests.
- [ ] Client-library connectors (jira, confluence, s3, youtube) map their
      library's transient exceptions per the table.
- [ ] No existing test regresses.

## Blocked by

None — can start immediately.

## Phase addressed

- Phase 4
