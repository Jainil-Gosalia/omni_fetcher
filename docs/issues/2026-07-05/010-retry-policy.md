# 010 — `RetryPolicy` + `fetch_with_retry`

## Parent Plan Phase

docs/plans/2026-07-05/v1.1-completion-and-dx.md — Phase 4

## What to build

A stateless, host-side resilience helper in `omni_fetcher.v1.retry`:

- `RetryPolicy` — frozen pydantic config: max attempts, backoff base/cap,
  jitter, retryable kinds (default `{TRANSIENT, RATE_LIMITED}`).
- `await fetch_with_retry(target, uri, *, policy, auth=None, zoom=None,
  tags=None)` — works against a fetcher or an orchestrator; re-invokes on a
  retryable `Error` result, returns the final `Result`. Never raises, never
  retries `Success` or `Partial` (data was delivered), holds no state between
  calls. Sleeping goes through an injectable async sleeper so tests never
  wait.

## Acceptance criteria

- [ ] Scripted-fetcher tests prove attempt counting and the backoff sequence
      (recorded via the injected sleeper), including the cap and jitter
      bounds.
- [ ] Non-retryable kinds (e.g. NOT_FOUND, AUTH_FAILED) return after one
      attempt.
- [ ] `Partial` is returned immediately, never retried.
- [ ] Exhausted attempts return the last `Error` unchanged.
- [ ] Two concurrent `fetch_with_retry` calls on one policy share no state
      (policy is frozen; verify with interleaved coroutines).

## Blocked by

None — can start immediately.

## Phase addressed

- Phase 4
