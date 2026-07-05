# 008 — Text decomposition tracer (local_file)

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 3 (half 2, tracer)

## What to build

Finer-than-natural zoom, proven end-to-end on one connector. A pure,
versioned helper (`omni_fetcher.v1.decompose` or similar) splits a `Text`
atom into `SECTION` / `PARAGRAPH` / `SENTENCE` child nodes; the local_file
connector honors a finer-level `ZoomSpec` by decomposing its Text atoms
through it. SENTENCE is documented best-effort. Non-text kinds are untouched;
a finer level explicitly requested for a kind that cannot decompose records
an honest gap rather than silently ignoring it.

## Acceptance criteria

- [ ] A multi-paragraph markdown file fetched at `PARAGRAPH` yields one child
      node per paragraph; concatenated child content equals the natural
      fetch's content.
- [ ] `SECTION` on a headed document splits on headings.
- [ ] Determinism: same file + spec → identical tree across runs.
- [ ] The splitter is pure (no I/O, no state) with its own unit suite.
- [ ] Requesting `SENTENCE` for an image atom kind records a gap on the
      result (Partial) instead of raising or silently no-oping.

## Blocked by

- Blocked by 007 (pruning and expansion must compose through one zoom path).

## Phase addressed

- Phase 3
