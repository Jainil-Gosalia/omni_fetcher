# 007 — Central zoom pruning

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 3 (half 1)

## What to build

Coarser-than-natural zoom for every connector at once: when a `ZoomSpec` is
supplied, `BaseFetcher.fetch()` (and the orchestrator pass-through) applies
the existing pure `prune_to_zoom` to each returned tree. No connector code
changes; connectors that already received `zoom` keep ignoring it and the
base applies the spec uniformly. Pruning operates on a copy — a fetcher's
own tree is never mutated (same rule the orchestrator's tag merge follows).

## Acceptance criteria

- [ ] `fetch(uri, zoom=ZoomSpec(...WHOLE...))` yields a childless node for a
      representative fixture of every connector suite (parametrized contract
      test).
- [ ] `SECTION` on a deep tree keeps exactly one level of children.
- [ ] No spec → natural tree, byte-identical to 1.0 behavior.
- [ ] Determinism: same input + spec → identical tree (run twice, compare
      dumps).
- [ ] Streamed (`stream()`) items are pruned identically to `fetch()`.

## Blocked by

None — can start immediately.

## Phase addressed

- Phase 3
