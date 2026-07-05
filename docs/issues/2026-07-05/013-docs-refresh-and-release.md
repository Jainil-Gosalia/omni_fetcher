# 013 — Docs refresh + v1.1.0 release

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 6

## What to build

The release train. Rewrite `docs/index.md`, `docs/fetchers.md`, and
`docs/auth.md` to the v1 API with the same verified-example discipline as the
README rewrite (legacy content under a labeled legacy heading or
`docs/legacy/`). Add the CHANGELOG `[1.1.0]` entry covering: builtin
registry, legacy deprecation, zoom, retry helpers, CLI fetch, new connector
test coverage. Touch up the README for the new surface. Then merge, publish
GitHub Release `v1.1.0`, and confirm trusted publishing lands 1.1.0 on PyPI
(which also refreshes the PyPI project page with the v1-first README).

## Acceptance criteria

- [ ] Every code snippet in the three docs pages executed against the real
      API before landing.
- [ ] CHANGELOG `[1.1.0]` complete; deprecation notice included.
- [ ] All six CI jobs green on the release commit.
- [ ] `pip install omni-fetcher==1.1.0` serves the new surface; PyPI page
      shows the v1-first README.

## Blocked by

- Blocked by 001–012 (everything shippable must be in).

## Phase addressed

- Phase 6
