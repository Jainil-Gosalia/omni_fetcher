# 009 — Text decomposition rollout (http_url, pdf, docx, pptx)

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 3 (half 2, rollout)

## What to build

Wire the tracer-proven splitter (issue 008) into the remaining text-bearing
connectors: http_url, pdf, docx, pptx. Each honors finer `ZoomSpec` levels on
its Text atoms with the same semantics the tracer established; sources whose
natural structure already provides sections (pptx slides, docx headings, pdf
pages) map those to `SECTION` rather than re-splitting flattened text.

## Acceptance criteria

- [ ] Each of the four connectors passes the shared expansion contract tests
      (paragraph split, content equality, determinism).
- [ ] pptx at `SECTION` returns slide-level children (existing natural
      structure, not re-split text).
- [ ] No behavior change when zoom is omitted (existing suites untouched and
      green).

## Blocked by

- Blocked by 008.

## Phase addressed

- Phase 3
