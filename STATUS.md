# FreelOAder status — 2026-04-25

## Phase: 4/5 — quota tracking + threshold switching
## Step: 4.1 — claude rate_limit_event → quota_signal journal events

Purpose (why this step exists):
  Phase 4 replaces round-robin with quota-aware routing — the actual
  product (PLAN principle #5: quota is an event stream, not a
  counter; the core value proposition).

  Step 4.1 lands the foundational piece: convert claude's native
  `rate_limit_event` records (the only vendor with explicit quota
  telemetry) into canonical `quota_signal` events written to
  `<data_dir>/events.jsonl` (PLAN decision #6). Subsequent steps
  consume that stream — phase 4 inference for gemini/codex (4.2),
  the quota-aware Strategy that reads the derived view (4.3), and
  config-driven thresholds (4.4).

Design (one sentence per file):
  - `src/freeloader/core/quota.py` — pure builder
    `build_quota_signal(provider, conversation_id, delta, ts) -> dict`
    that produces the canonical event from a `RateLimitDelta` plus
    context. No I/O; no router state. Tested in isolation so phase
    4.2 (gemini/codex inference) can grow a sibling builder without
    touching the router.
  - `src/freeloader/router.py` — when `RateLimitDelta` arrives in
    the `async for` loop, call `self.events.write(build_quota_signal(
    provider, conversation_id, delta, now))` immediately (before
    the terminal `turn_done`). Per-delta, not per-turn — claude can
    emit multiple in one turn (e.g., five_hour and seven_day
    statuses) and each is its own observation.
  - `tests/core/test_quota_claude_rate_limit.py` — drives Router
    with scripted adapters that yield RateLimitDeltas, asserts the
    captured events list contains correctly-shaped quota_signal
    records in the right order relative to turn_done.

Canonical quota_signal shape (frozen here for phase 4.2 to follow):
  {
    "ts": ISO-8601 UTC,
    "kind": "quota_signal",
    "provider": "claude" | "codex" | "gemini",
    "conversation_id": str,
    "rate_limit_type": str,        # e.g. "five_hour"
    "status": str,                 # "allowed" | "exceeded" | ...
    "resets_at": int | None,
    "overage_status": str | None,
    "raw": dict                    # vendor payload, preserved
  }

Why emit on `status=allowed` too: the stream-as-truth principle (#5)
means even an "allowed" record carries info — `resets_at`
timestamp, the fact that we polled — and phase 4.3's quota-aware
Strategy will use those signals to compute pressure trends, not
just threshold breaches. Emitting only the breaches throws away
half the telemetry.

Exit criteria for step 4.1:
  - [ ] `src/freeloader/core/quota.py` exists with
        `build_quota_signal(provider, conversation_id, delta, ts) -> dict`
        producing the canonical shape above.
  - [ ] Router emits one quota_signal per RateLimitDelta seen (both
        allowed and non-allowed), via `self.events.write(...)`,
        before the terminal turn_done.
  - [ ] `tests/core/test_quota_claude_rate_limit.py` covers:
          * one allowed delta → one quota_signal + one turn_done
          * one non-allowed delta → one quota_signal + one turn_done
          * two deltas in one turn → two quota_signals + one turn_done
          * no RateLimitDelta seen → zero quota_signals + one turn_done
          * quota_signal events precede turn_done in event order
          * quota_signal carries provider, conversation_id,
            rate_limit_type, status, resets_at, overage_status, raw,
            and a UTC ISO-8601 ts
  - [ ] All existing tests still green (168 → 173 expected).
  - [ ] ruff check + ruff format clean.

Out of scope for 4.1 (deferred):
  - gemini/codex quota *inference* (cumulative tokens + 429) —
    that's step 4.2.
  - The quota-aware routing *Strategy* that reads the derived view —
    that's step 4.3.
  - `quota-aware` strategy file — gate_4 expects
    `src/freeloader/core/routing/quota_aware.py` for step 4.3.
  - `freeloader.toml` thresholds — that's step 4.4.
  - Replay test (`tests/core/test_routing_replay.py`) — that's
    step 4.5.
  - Live `claude -p` smoke harness for end-to-end rate_limit_event
    capture — carries forward from phase 3 still-untested slice.

Phase 3 recap (closed, 22/22 gate checks green):
  - 3.1 CodexAdapter ✅
  - 3.2 Per-conversation CLI state isolation env vars (claude
    CLAUDE_CONFIG_DIR / codex CODEX_HOME; gemini fallback to
    per-adapter mutex) ✅
  - 3.3 GeminiAdapter — compound provider, stats.models per turn ✅
  - 3.4 Round-robin Router across registered adapters ✅
  - 3.5 Provider-switch + canonical history replay (Router.bind) ✅
  - 3.6 /v1/models endpoint (auto + per-provider ids) ✅
  - 3.7 Cross-adapter contract suite (21 tests, 3 adapters) +
    RoundRobinStrategy extraction ✅
  - 168/168 tests passing.

Phase 4 sketch (from ROADMAP.md):
  - 4.1 claude rate_limit_event → quota_signal events (this step).
  - 4.2 gemini/codex quota inference (cumulative tokens + 429).
  - 4.3 Quota-aware Strategy reading the derived view; pick(order)
    chooses the least-pressured provider.
  - 4.4 Threshold + weight config in freeloader.toml (PLAN
    decision #10).
  - 4.5 Replay test: fixture JOURNAL → deterministic routing
    decisions (no wall-clock dependency).

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
