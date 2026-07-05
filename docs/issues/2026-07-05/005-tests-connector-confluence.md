# 005 — v1 confluence connector behavior tests

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 2

## What to build

`tests/v1/test_connector_confluence.py`: page and space routes yield
canonical nodes with storage-format bodies mapped to Text atoms; the two
client shapes (`BasicAuth` for Cloud, `BearerAuth` for Server/DC) construct
correctly from per-call credentials only; a non-Basic/Bearer credential
returns the documented typed error; missing pages/spaces surface as
`Error(NOT_FOUND)`.

## Acceptance criteria

- [ ] Page fetch: `Success`, body as Text atom, page fields under
      `source_extra["confluence"]`, metadata core populated.
- [ ] Space fetch: container node with page children.
- [ ] `BasicAuth` and `BearerAuth` each produce the matching client shape;
      credentials never persist on the connector between calls.
- [ ] Unsupported credential type → typed error (per stream()'s contract).
- [ ] Suite runs offline (stubbed atlassian client) in the guarded runner.

## Blocked by

None — can start immediately.

## Phase addressed

- Phase 2
