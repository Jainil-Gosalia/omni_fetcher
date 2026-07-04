# Issue #24 — Migrate `jira` connector to the canonical contract

## Parent Plan Phase
Phase 7

## What to build

Migrate the `jira` connector onto the canonical contract per the plan's *Architectural
decisions* and Phase 7. The former `JiraIssue`/`JiraEpic`/`JiraSprint`/`JiraProject`
data is re-expressed as canonical atoms + metadata: description/comments as Text atoms,
descriptive fields (status, assignee, reporter, priority, sprint, story points,
timestamps, url) in the metadata core + namespaced `source_extra`, with semantic `kind`.
Return a `Result`, implement `stream()` (bounded), report uncovered surface via
`partial`/`unsupported`. Uses per-call auth. Preserve determinism and read-only.

## Acceptance criteria

- [ ] Returns a canonical `CompositionNode` tree wrapped in a `Result` (no `Jira*` types).
- [ ] Descriptive fields in metadata core + `source_extra`; content in atoms.
- [ ] Sets a sensible semantic `kind` per resource.
- [ ] Auth resolved per call; failures are typed errors, not raises.
- [ ] Implements `stream()`; `fetch()` inherited.
- [ ] Integration tests updated to assert canonical output and pass.

## Blocked by

- Blocked by #1
- (Recommended after #2, #6, and #7; buildable against #1's frozen interfaces with stubs.)

## Phase addressed

- Phase 7
