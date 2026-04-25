# FreelOAder status — 2026-04-25

## Phase: 4/5 — quota tracking + threshold switching
## Step: 4.3 — quota-aware routing strategy

Purpose (why this step exists):
  Steps 4.1 and 4.2a built the *signal*: claude's native
  `RateLimitDelta` and gemini/codex's inferred token-window pressure
  both land on `events.jsonl` as `quota_signal` records sharing one
  canonical shape (PLAN principle #5, decision #6). 4.3 makes the
  router *act* on that signal — replace round-robin's blind cycling
  with a strategy that skips pressured providers on first-turn
  dispatch.

  This is the payoff for phase 4: traffic actually shifts away from a
  rate-limited backend instead of bouncing into it on every other
  conversation.

Design (one sentence per file):
  - `src/freeloader/core/routing/quota_aware.py` — new
    `QuotaAwareStrategy` with `pick(order) -> str` (same shape as
    `RoundRobinStrategy`, so Router stays strategy-agnostic) plus a
    new `observe(event: dict) -> None` hook fed by the Router every
    time a `quota_signal` is written. State: per-provider, per-
    `rate_limit_type`, the latest `status` seen. "Pressured" = ANY
    `rate_limit_type`'s latest status == `"exceeded"`. `pick()` scans
    `order` left-to-right and returns the first non-pressured
    provider; if all are pressured, returns the first in `order` —
    deterministic fallback, no starvation (the user wants service
    even under universal pressure; the journal records the breach).
  - `src/freeloader/core/routing/__init__.py` — export
    `QuotaAwareStrategy` alongside `RoundRobinStrategy`.
  - `src/freeloader/router.py` — after every successful
    `self.events.write(event)` for a `quota_signal` (both native via
    `_emit_quota_signal` and inferred via
    `_emit_inferred_quota_signal`), call
    `self._strategy.observe(event)` if `hasattr(self._strategy,
    "observe")`. Duck-typed so `RoundRobinStrategy` (no `observe`)
    keeps working unchanged. NO change to Router signature, NO
    change to `pick(order)` interface.
  - `tests/core/test_routing_quota_aware.py` — strategy unit tests
    (pure: feed events via `observe()`, assert on `pick()`) +
    integration tests through `Router` with `_ScriptedAdapter`
    (claude RateLimitDelta exceeded → next fresh conversation skips
    claude, etc.).

Why no wall-clock decay:
  - 4.5's replay test wants "fixture JOURNAL → exact same routing
    decisions". A `resets_at` comparison against `now()` couples the
    strategy to wall-clock and breaks replay determinism.
  - Pressure clears only when a later `allowed` event arrives for
    the same `rate_limit_type`. For the inferred path this is
    automatic — every UsageDelta produces a new windowed status. For
    claude it requires the next turn (or background poll) to surface
    a fresh `rate_limit_event`, which matches how claude actually
    publishes it today.
  - Decay/forecasting can be a 4.5+ refinement once the replay
    harness proves the deterministic path works.

Why "all pressured → first in order" instead of "raise":
  - PLAN principle #5 says quota is a signal, not a hard gate. The
    user wants a response; the router's job is to pick the *least
    bad* option, not to refuse service.
  - "First in order" is the simplest deterministic tiebreaker.
    Round-robin among pressured providers is also defensible but
    adds state (a second cursor) for marginal benefit; revisit if
    the breach pattern in practice argues for it.

Why duck-typed `observe()`:
  - Avoids forcing `RoundRobinStrategy` to grow a no-op method
    (which would either be dead code or pretend to consume signals
    it ignores). The Router's `hasattr` guard is one line; the cost
    of a Protocol with `observe` is shared confusion about what
    round-robin does with quota signals (answer: nothing, by design).
  - `QuotaAwareStrategy.pick()` is still the same shape, so any
    future strategy that wants to consume signals just adds the
    method.

Exit criteria for step 4.3:
  - [ ] `src/freeloader/core/routing/quota_aware.py` exists with a
        `QuotaAwareStrategy` class exposing `pick(order)` and
        `observe(event)`. No clock dependency; pure event-stream
        folding.
  - [ ] `src/freeloader/core/routing/__init__.py` re-exports it
        alongside `RoundRobinStrategy`.
  - [ ] Router calls `self._strategy.observe(event)` after every
        `quota_signal` write (both native and inferred), guarded by
        `hasattr`. RoundRobinStrategy continues to work unchanged.
  - [ ] `tests/core/test_routing_quota_aware.py` covers:
          * strategy unit: all allowed → cycles like round-robin
            (NOT a strict equality — "no provider is unfairly
            avoided" is enough; pick() picks the first in order
            when none are pressured)
          * strategy unit: claude exceeded → pick skips claude,
            picks codex (or next non-pressured)
          * strategy unit: codex `inferred_window` exceeded → pick
            skips codex
          * strategy unit: ALL pressured → pick returns first in
            order (deterministic fallback, no starvation)
          * strategy unit: recovery — exceeded then later allowed
            for same rate_limit_type → provider is selectable again
          * strategy unit: claude five_hour exceeded but seven_day
            allowed → still pressured (any-rule)
          * Router integration: scripted claude RateLimitDelta
            exceeded on conv-A → next fresh conv-B dispatches to
            codex, NOT claude
          * Router integration: bound conversation continues on
            its bound provider regardless of pressure (binding
            wins over strategy)
          * Router integration: RoundRobinStrategy default keeps
            working — no AttributeError on `observe`
  - [ ] All existing tests still green (191 → ~201 expected).
  - [ ] ruff check + ruff format clean.
  - [ ] gate_4.sh's "quota-aware routing strategy exists" check
        passes (file existence — it does not introspect contents).

Out of scope for 4.3 (deferred):
  - 429 detection from codex/gemini (4.2b). The strategy will react
    to those as soon as 4.2b emits `quota_signal` events with
    status="exceeded" — no Strategy change needed when 4.2b lands.
  - Wall-clock decay / `resets_at` forecasting — see "Why no
    wall-clock decay" above.
  - `freeloader.toml` thresholds — 4.4. The placeholder constants
    in 4.2a stay; QuotaAwareStrategy doesn't read thresholds
    (it consumes pre-computed status).
  - Replay test that loads fixture JSONL — 4.5. The `observe(event)`
    seam is exactly what 4.5 needs to feed events.
  - Mid-conversation provider switch driven by pressure (currently
    only manual via `Router.bind()`). Triggering a bind() from a
    `quota_signal` mid-conversation is a separate concern — the
    user might be mid-stream when claude flips to exceeded, and
    deciding whether to interrupt vs let the current turn finish
    is policy, not strategy. Defer.

Phase 4 sketch:
  - 4.1 claude rate_limit_event → quota_signal events ✅
  - 4.2a gemini/codex token-window inference ✅
  - 4.2b gemini/codex 429 detection (adapter stderr work)
  - 4.3 Quota-aware Strategy reading the derived view (this step).
  - 4.4 Threshold + weight config in freeloader.toml.
  - 4.5 Replay test: fixture JOURNAL → deterministic decisions.

Recent lessons (see JOURNAL.jsonl for full text):
  - claude -p exits 0 on rate_limit; inspect events
  - cold cache tax 6k–14k input tokens; warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini compound provider (stats.models per turn); UUID
    session_id (spike drift)
  - GEMINI_CLI_HOME couples auth to state — per-adapter mutex
    fallback per PLAN decision #16
  - codex exec resume rejects -s; sandbox set on first turn persists
  - codex --json emits pure JSONL on stdout; no model id field
  - asyncio create_subprocess_exec env=dict REPLACES env entirely
  - Starlette aclose() injects GeneratorExit, not CancelledError
  - json.dumps default separators differ from OpenAI wire bytes
  - Router._bindings is now (provider, sid|None); None pins for
    replay (mid-conversation provider switch)
  - 4.1 frozen quota_signal shape: rate_limit_type/status/resets_at/
    overage_status/raw — sibling builders must produce identical
    shape so 4.3 reads ONE stream
  - 4.2a inferred token-window pressure for codex/gemini; in-memory
    rolling window per provider; same canonical shape with
    rate_limit_type="inferred_window"
