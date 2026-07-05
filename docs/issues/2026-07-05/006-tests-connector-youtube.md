# 006 — v1 youtube connector behavior tests

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 2

## What to build

`tests/v1/test_connector_youtube.py`: video metadata mapping via a stubbed
`yt_dlp` extractor (monkeypatched, capturing the original reference so the
stub cannot recurse — see the rss suite's `_install_feed` for the pattern
and its historical bug). Watch/short URLs yield a canonical video node;
title/uploader/durations land in the metadata core and
`source_extra["youtube"]`; extraction failures are typed errors.

## Acceptance criteria

- [ ] Video fetch: `Success` with Video atom and namespaced YouTube fields.
- [ ] `youtube.com/watch?v=` and `youtu.be/` shapes both route.
- [ ] Unavailable/private video → `Error` with an appropriate kind, not a
      raise.
- [ ] No network and no real yt_dlp extraction; suite runs in the guarded
      runner.

## Blocked by

None — can start immediately.

## Phase addressed

- Phase 2
