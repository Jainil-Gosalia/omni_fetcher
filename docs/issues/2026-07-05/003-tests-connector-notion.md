# 003 — v1 notion connector behavior tests

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 2

## What to build

`tests/v1/test_connector_notion.py`, mirroring the established suite pattern
(httpx MockTransport / stubbed transport, external behavior only, no
network): page and database URIs yield canonical nodes; the per-call
`BearerAuth` integration token reaches the request headers; descriptive
fields live in `source_extra["notion"]` and the uniform metadata core;
missing resources and bad tokens come back as typed `Error` results.

## Acceptance criteria

- [ ] Page fetch: `Success` with advisory kind, body content in Text atoms,
      Notion-only fields under `source_extra["notion"]`.
- [ ] Database fetch: container node with one child per row/page.
- [ ] `BearerAuth(token=...)` asserted present on the outgoing request.
- [ ] 404 → `Error(NOT_FOUND)`; 401 → `Error(AUTH_FAILED)`; both returned,
      never raised.
- [ ] Suite runs offline in the guarded runner; no live Notion access.

## Blocked by

None — can start immediately.

## Phase addressed

- Phase 2
