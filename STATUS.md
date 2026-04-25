# FreelOAder status — 2026-04-25

## Phase: 3/5 — multi-adapter routing  ✅ GATE GREEN
## Next phase: 4/5 — quota tracking + threshold switching
## Step: 4.1 — claude rate_limit_event → quota_signal journal events

Purpose (why this step exists):
  Phase 3 closed multi-adapter routing with round-robin selection.
  Phase 4 replaces round-robin with quota-aware routing — the actual
  product (PLAN principle #5: quota is an event stream, not a
  counter; the core value proposition).

  Step 4.1 lands the foundational piece: convert claude's native
  `rate_limit_event` records (the only vendor with explicit quota
  telemetry) into canonical `quota_signal` events in the journal.
  Subsequent steps consume that stream — phase 4 inference for
  gemini/codex (4.2), the quota-aware Strategy that reads the
  derived view (4.3), and config-driven thresholds (4.4).

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

Entry criteria for phase 4 (met):
  - [x] Gate 3 GREEN; 168/168 tests passing
  - [x] All phase 3 artifacts shipped (3 adapters, multi-adapter
        Router with strategy seam, /v1/models, contract suite)
  - [x] phase_done entry in JOURNAL.jsonl with phase-close commit sha

Phase 3 still-untested slice (carried into phase 4):
  - Live `claude -p` / `codex exec` / gemini smoke harness. All
    three adapters are exercised live during local development
    (codex 2026-04-25 capture; gemini 2026-04-25 capture; claude
    spike 2026-04-05) but no pytest-runnable smoke harness gates
    behind an env flag yet. The carry-forward will land in a
    phase-4 step once quota-signal events demand a live source for
    integration testing.
  - `confirm_claude_model_usage_fields` lesson — claude's modelUsage
    field-name observation from the original spike. The contract
    suite's golden fixtures cover the documented shape; live
    confirmation rides along with the smoke harness.

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
