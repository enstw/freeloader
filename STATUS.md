# FreelOAder status — 2026-05-07

## Phase: 5/5 — tool-call decision  ✅ GATE GREEN
## MVP: complete — all 5 phases shipped
## Backlog: 4.2b ✅ shipped

Step 4.2b shipped:
  - `core/quota.py::match_stderr_quota_pressure(provider,
    stderr_text, exit_code)` is the single matcher. Returns
    `[RateLimitDelta(rate_limit_type="429", status="exceeded",
    raw={stderr_excerpt, exit_code, provider, source})]` on a
    pattern hit, else `[]`. Gates on `exit_code != 0` so a clean
    turn that mentions "rate limit" in telemetry is not flagged.
  - Patterns (case-insensitive substring per line): `"429"`,
    `"too many requests"`, `"rate limit"`, `"ratelimit"`,
    `"quota exceeded"`, `"quota_exceeded"`, `"resource_exhausted"`,
    `"resource exhausted"`, `"insufficient_quota"`.
  - `CodexAdapter.send` and `GeminiAdapter.send` drain stderr in
    a background `asyncio.Task` (no pipe-buffer deadlock) and
    yield matcher output before returning. The adapter's `finally`
    cancels and awaits the task on cancellation.
  - The yielded `RateLimitDelta` flows through the existing router
    path (`router.py:265`) — `build_quota_signal` is reused, no
    third sibling builder needed; the 4.2a-era comment
    anticipating one was speculative and was removed.
  - Tests added (+24, total 279):
      • 13 in `tests/core/test_quota_stderr_429.py` (matcher unit).
      • 4 in `tests/adapters/test_codex_stderr_429.py` (subprocess
        fake yields stderr; assert delta).
      • 3 in `tests/adapters/test_gemini_stderr_429.py` (RESOURCE_-
        EXHAUSTED, clean run, HTTP 429 phrasings).
      • 4 in `tests/core/test_quota_stderr_429_router.py` (router
        writes quota_signal, strategy observes, terminal flips to
        rate_limited).
  - Gate 4 green; gate 5 green; ruff/format clean.

Did NOT do (still deferred):
  - claude stderr drain — claude has rate_limit_event in JSONL so
    the 4.2b path doesn't apply; recorded as a low-severity lesson
    (`subject:stderr_pipe_buffer_drain`).
  - Live-smoke 429 induction (would need a saturated subscription).
  - Per-vendor exhaustive pattern catalogue.

Phase 5 outcome:
  Hard problem #1 (tool-call translation) decided. Strategy:
  **chat_only_strip**. Frontend strips `tools`/`tool_choice`,
  signals the strip on the wire (`X-FreelOAder-Tool-Mode:
  chat-only-strip` header + non-streaming `tool_calls: []`), and
  emits a structured per-conversation warning log. Output-parsing
  shim and passthrough were rejected with five evidence-grounded
  reasons recorded in JOURNAL (`kind:decision
  subject:tool_call_strategy`).

  Steps shipped:
  - 5.1 ✅ decision recorded in JOURNAL
  - 5.2 ✅ wire-level signal formalized in
        `frontend/app.py` (+ gate_5 grep fix as a meta-cleanup)
  - 5.3 ✅ `tests/e2e/test_tool_calls.py` exercises the full
        pipeline through chat_only_strip
  - 5.4 ✅ `README.md` "Tool-call mode" section documents the mode
        and its limits

  Tests at MVP close: 243 (started phase 5 at 232 → +7 frontend
  unit tests, +4 e2e tests). Gate 5 GREEN; gate 4 still GREEN.

MVP-complete state:
  - `/v1/chat/completions` non-streaming + SSE streaming
  - `/v1/models` advertising registered providers + auto
  - Three CLI adapters (claude / codex / gemini) behind one
    `CLIAdapter` Protocol
  - Quota-aware routing (`QuotaAwareStrategy`) reading a derived
    view over `quota_signal` events with thresholds from
    `freeloader.toml`
  - Conversation persistence + history-diff
  - Provider-switch replay mid-conversation
  - Per-conversation CLI state isolation under `<data_dir>/cli-state/`
  - Chat-only tool mode with on-the-wire signal

What MVP does NOT include (PLAN.md "Things to explicitly *not* do"
plus phase deferrals):
  - Tool-call shim / passthrough — rejected in phase 5
  - Per-provider threshold overrides in `freeloader.toml`
  - Strategy weights — `QuotaAwareStrategy` is cursor-rotating
  - Runtime config reload
  - Multi-tenant or public deployment (ToS)
  - OS-level CLI sandboxing (sandbox-exec / containers)
  - Persistent CLI processes / pty / `/clear` / tmux
  - Session persistence across FreelOAder restarts
  - Tool *discovery* via `/v1/models`
  - Adapter list / model-name routing config

Phase ledger:
  - 1 ✅ ClaudeAdapter, non-streaming, single conversation
  - 2 ✅ Streaming + cancellation
  - 3 ✅ CodexAdapter + GeminiAdapter + round-robin routing
  - 4 ✅ Quota tracking + threshold switching
  - 5 ✅ Tool-call decision (this phase)

Next moves (post-MVP, no phase number):
  - Operate: actually use FreelOAder against real subscriptions and
    observe whether quota-aware routing holds up. Phase-5 evidence
    for tool-mode came from build-time observation; live operation
    would surface failure modes (CLI prompt drift, cold-cache
    surprises, real quota burn) that build-time can't.
  - With 4.2b shipped, the strategy now reacts to upstream 429s —
    next live-smoke run should add a 429-induction test if a
    saturated account is available.
  - `/codex consult` or `/review` are pre-commit sanity checks, not
    pre-push gates. Push to `main` directly after every commit
    (see `AGENT.md` § "Commit discipline").
