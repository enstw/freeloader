# FreelOAder status — 2026-04-25

## Phase: 4/5 — quota tracking + threshold switching
## Step: 4.2a — gemini/codex token-window quota inference

Purpose (why this step exists):
  Phase 4.1 covered the only vendor with explicit rate-limit
  telemetry (claude's `rate_limit_event` → `quota_signal`). Gemini
  and codex give no such signal, so phase 4.3's quota-aware
  Strategy would be blind to them. Step 4.2 closes that gap by
  *inferring* pressure from observable signals.

  Two distinct inferred signals are in scope for phase 4.2 overall:
    a) cumulative input+output tokens per rolling window (THIS step)
    b) explicit 429 detection                                 (4.2b)

  4.2a delivers (a) on its own. (b) is split into 4.2b because
  surfacing 429 from codex/gemini requires adapter-level work that
  doesn't exist today (stderr capture, exit-code propagation, a
  delta variant or extended ErrorDelta). Splitting keeps 4.2a a
  small, low-risk router-and-builder change; 4.3 can read both 4.1
  and 4.2a signals from one stream while 4.2b grows in parallel.

Why split now (not bundle):
  - 4.2a touches: 1 builder + router glue + tests. Reversible.
  - 4.2b touches: gemini.py + codex.py adapter lifecycles
    (currently pipe stderr to /dev/null effectively); a new way
    for adapters to surface "I observed a 429"; tests across all
    three adapters. Different risk profile, different review
    surface, different commit.
  - Phase 4.3 can land with 4.2a alone — it just won't react to
    gemini/codex 429s until 4.2b lands. Threshold-based pressure
    (4.2a) is the dominant signal anyway.

Design (one sentence per file):
  - `src/freeloader/core/quota.py` — add a sibling pure builder
    `build_quota_signal_from_usage(provider, conversation_id,
    window_seconds, window_tokens, tokens_threshold, ts) -> dict`.
    Same canonical shape as `build_quota_signal` (frozen in 4.1):
    `rate_limit_type="inferred_window"`, `status="allowed"` if
    window_tokens < tokens_threshold else `"exceeded"`, `raw`
    carrying the window summary (`window_seconds`, `window_tokens`,
    `tokens_threshold`) so a phase-4.3 Strategy can see what
    triggered the call without re-deriving it.
  - `src/freeloader/router.py` — add per-provider token-window
    state (`self._token_windows: dict[str, list[tuple[float,
    int]]]`); after the adapter's UsageDelta arrives for a
    gemini/codex turn, append `(monotonic_ts, total_tokens)`,
    evict entries older than `window_seconds`, sum the remainder,
    emit one inferred quota_signal via `_emit_inferred_quota_signal`
    helper that mirrors `_emit_quota_signal` (same write-failure
    logging discipline). Skip claude — it has native
    RateLimitDelta, double-emitting would double-count.
  - `tests/core/test_quota_inference.py` — pure builder tests +
    router emission tests with `_ScriptedAdapter` covering both
    under-threshold and over-threshold cases; window eviction;
    claude path emits no inferred signal; inferred signal precedes
    turn_done; ts is ISO UTC; write failure logged not silent.

Threshold and window defaults (placeholder):
  - `INFERENCE_WINDOW_SECONDS = 300` (5 minutes)
  - `INFERENCE_TOKENS_THRESHOLD = 1_000_000` per provider
  These are intentionally round numbers, not tuned to any vendor's
  published limits. Step 4.4 moves them to `freeloader.toml`
  (PLAN decision #10). Tests inject custom values, never depend
  on the placeholders.

Inference is opt-in per provider:
  - `_INFERENCE_PROVIDERS = {"codex", "gemini"}` — hardcoded set.
  - claude is excluded: native RateLimitDelta is the source of
    truth, inference would double-count.
  - Any future provider added to the pool is excluded by default
    until explicitly added to the inference set.

Exit criteria for step 4.2a:
  - [ ] `src/freeloader/core/quota.py` grows
        `build_quota_signal_from_usage(provider, conversation_id,
        window_seconds, window_tokens, tokens_threshold, ts) -> dict`
        producing the canonical shape with
        `rate_limit_type="inferred_window"`.
  - [ ] Router emits one inferred quota_signal per UsageDelta from
        codex/gemini (zero from claude), via `self.events.write(...)`
        before the terminal turn_done.
  - [ ] `tests/core/test_quota_inference.py` covers:
          * builder shape with window_tokens < threshold → status=allowed
          * builder shape with window_tokens >= threshold → status=exceeded
          * router: codex UsageDelta → one inferred quota_signal
          * router: gemini UsageDelta (compound, multiple sub-models) →
            one inferred quota_signal summing all sub-models
          * router: claude UsageDelta → zero inferred quota_signals
          * router: window evicts entries older than window_seconds
          * inferred quota_signal precedes turn_done in event order
          * ts is ISO UTC tz-aware
          * write failure logged not silent
  - [ ] All existing tests still green (178 → 187 expected).
  - [ ] ruff check + ruff format clean.
  - [ ] gate_4.sh's "gemini/codex 429 + token-window inference test"
        check passes (file existence — it does not introspect contents).

Out of scope for 4.2a (deferred):
  - 429 detection from codex/gemini — that's step 4.2b. Requires
    adapter-side stderr/exit-code handling.
  - Persistent window state across FreelOAder restarts. The window
    is in-memory; a restart loses pressure history. Acceptable for
    phase 4 (personal-use proxy) — quota_signal events on disk are
    the durable record; the window is just a derived view.
  - Quota-aware routing Strategy that *reads* these signals — 4.3
    (`src/freeloader/core/routing/quota_aware.py`).
  - `freeloader.toml` thresholds — 4.4.
  - Replay test — 4.5.

Phase 4 sketch (revised):
  - 4.1 claude rate_limit_event → quota_signal events ✅ (closed)
  - 4.2a gemini/codex token-window inference (this step).
  - 4.2b gemini/codex 429 detection (adapter stderr work).
  - 4.3 Quota-aware Strategy reading the derived view.
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
