# 012 — v1 CLI `fetch` command

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 5

## What to build

`omni-fetcher fetch <uri>` backed by `OmniFetcher(builtin_registry())`:

- Auth flags take env-var *names*, never raw secrets:
  `--auth-type bearer --token-env GITHUB_TOKEN`,
  `--auth-type basic --username-env U --password-env P`, etc.
- `--zoom text=paragraph` style per-atom-type spec.
- Output: `--tree` (default; rich-rendered metadata core, atom summaries,
  source_extra) or `--json` (`model_dump_json`).
- Exit codes: 0 for `Success` and `Partial` (gaps rendered to stderr),
  1 for `Error`, 2 for usage errors.
- Existing legacy CLI commands remain untouched.

## Acceptance criteria

- [ ] `omni-fetcher fetch README.md` prints a tree and exits 0.
- [ ] `--json` output round-trips through the Result model.
- [ ] Unrouted URI exits 1 with the NOT_FOUND error rendered.
- [ ] No credential value ever appears in argv, logs, or output.
- [ ] CLI test suite covers local-file and stubbed-HTTP paths; legacy CLI
      tests unchanged and green.

## Blocked by

- Blocked by 001 (builtin_registry).

## Phase addressed

- Phase 5
