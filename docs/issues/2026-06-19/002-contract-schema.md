# Issue #2 — Contract schema module (impl + tests)

## Parent Plan Phase
Phase 1

## What to build

Flesh out the canonical contract types from stubs to full behavior, per the plan's
*Architectural decisions*: the recursive `CompositionNode` tree, the 5 content-only
atoms (no inline EXIF/ID3/author/id — those move to metadata), the typed metadata
common core + namespaced `source_extra` validation, `content_hash` population (Merkle
over children) where cheap, and tag merging. This is the deepest, most-stable module —
the contract is the product.

## Acceptance criteria

- [ ] `CompositionNode` composes atoms recursively; leaves are canonical atoms only.
- [ ] Atoms carry content only; descriptive attributes live in the metadata core.
- [ ] `source_extra` is namespaced and validated per source.
- [ ] `content_hash` is populated where cheap; `prev_hash` reserved.
- [ ] Tag merging works across the tree.
- [ ] Unit tests cover validation, the tree, content-vs-metadata separation, and hashing.

## Blocked by

- Blocked by #1

## Phase addressed

- Phase 1
