# 001 — `builtin_registry()` + v1 wiring exports

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 1

## What to build

A one-call wiring path for hosts: `omni_fetcher.v1.builtin_registry()`
returns a `FrozenRegistry` with all 21 built-in connectors registered under
their real URI patterns and sane priorities. Connector modules are imported
lazily (a definition resolves its fetcher class on first route), so
`import omni_fetcher.v1` stays light and heavy deps (yt_dlp) load only when
routed to. Connectors whose optional extra is missing (office / jira /
confluence) are skipped with a debug-level log entry, never an ImportError.

Also promote the wiring API to the package surface: export `OmniFetcher`
(orchestrator), `RegistryBuilder`, and `builtin_registry` from
`omni_fetcher.v1`, and update the README orchestrator example to the
one-liner.

Demo: `await OmniFetcher(builtin_registry()).fetch("README.md")` returns a
`Success` with a `"file"` node.

## Acceptance criteria

- [ ] `builtin_registry()` routes every connector's documented URI shapes
      (table in README "Connectors" section) to the right fetcher class.
- [ ] `import omni_fetcher.v1` and `builtin_registry()` do not import
      connector modules eagerly (assert via `sys.modules` in tests).
- [ ] With an extra missing, the registry builds cleanly and the remaining
      sources still route (simulate via an import-blocking MetaPathFinder).
- [ ] `from omni_fetcher.v1 import OmniFetcher, RegistryBuilder,
      builtin_registry` works; README example updated and verified.
- [ ] Registry remains immutable; concurrent `resolve()` calls share no
      mutable state.

## Blocked by

None — can start immediately.

## Phase addressed

- Phase 1
