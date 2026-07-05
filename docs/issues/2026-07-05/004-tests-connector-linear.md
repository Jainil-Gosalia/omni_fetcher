# 004 — v1 linear connector behavior tests

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 2

## What to build

`tests/v1/test_connector_linear.py`: issue / team / project routes map the
GraphQL payload onto canonical nodes; both credential shapes the connector
documents (`BearerAuth` and `ApiKeyAuth`) are accepted and reach the request;
Linear-only fields live in `source_extra["linear"]`; expected failures are
typed `Error` values.

## Acceptance criteria

- [ ] Issue fetch: `Success` with metadata core populated (id, author,
      created/updated) and Linear fields namespaced.
- [ ] Team and project fetches: container nodes with issue children.
- [ ] Both `BearerAuth` and `ApiKeyAuth` are asserted on the wire.
- [ ] Unknown identifier → `Error(NOT_FOUND)`; invalid key →
      `Error(AUTH_FAILED)`.
- [ ] Suite runs offline in the guarded runner.

## Blocked by

None — can start immediately.

## Phase addressed

- Phase 2
