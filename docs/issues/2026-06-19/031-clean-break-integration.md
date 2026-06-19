# Issue #31 — Clean break + integration (join point)

## Parent Plan Phase
Phase 8

## What to build

The final join slice, per the plan's Phase 8. Once every connector emits the canonical
contract, complete the v1.0 break: remove the ~50 source-specific public schema classes
and their exports, write a migration guide mapping each removed schema's fields onto the
canonical atoms + metadata, single-source `__version__` from package metadata (resolving
the `0.9.0` vs `0.11.2` mismatch), bump to v1.0, and update README/CHANGELOG. Finalize
the reserved hash fields (populate `content_hash` where cheap; leave verification out of
scope). This issue is the integration point — it is **not** parallel and lands last.

## Acceptance criteria

- [ ] No source-specific public schema classes remain; only the canonical contract is exported.
- [ ] Migration guide maps every removed schema onto canonical atoms + metadata.
- [ ] `__version__` is single-sourced and reads `1.0.0`; README/CHANGELOG updated.
- [ ] `content_hash` populated where cheap; `prev_hash` reserved; no verification logic ships.
- [ ] Full suite green: contract schema, result envelope, zoom resolver, auth + registry,
      and all connector integration tests.

## Blocked by

- Blocked by #2, #3, #4, #5, #6, #7, #8, #9 (all modules + isolation proof)
- Blocked by #10–#30 (all 21 connector migrations)

## Phase addressed

- Phase 8
