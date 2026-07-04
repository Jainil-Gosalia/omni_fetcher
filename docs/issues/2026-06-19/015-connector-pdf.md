# Issue #15 — Migrate `pdf` connector to the canonical contract

## Parent Plan Phase
Phase 6

## What to build

Migrate the `pdf` connector onto the canonical contract per the plan's *Architectural
decisions* and Phase 6. Using the shared node-mapping helper: emit a `CompositionNode`
tree of canonical atoms (Text; Table/Image where present), return a `Result`, set a
semantic `kind`, place source-specific fields in namespaced `source_extra`, implement
`stream()` (bounded), and report uncovered surface via `partial`/`unsupported`. Preserve
the extraction boundary: deterministic text extraction only — **no OCR** (a scanned PDF
with no text layer is reported, not OCR'd).

## Acceptance criteria

- [ ] Returns a canonical `CompositionNode` tree wrapped in a `Result`.
- [ ] Sets a sensible semantic `kind`; metadata core populated uniformly.
- [ ] Source-specific fields live in namespaced `source_extra`, not in content.
- [ ] No OCR; scanned/empty-text PDFs reported via `partial`/`unsupported`, not OCR'd.
- [ ] Implements `stream()`; `fetch()` inherited.
- [ ] Integration tests updated to assert canonical output and pass.

## Blocked by

- Blocked by #1
- (Recommended after #2 and #7; buildable against #1's frozen interfaces with stubs.)

## Phase addressed

- Phase 6
