# FreelOAder status — 2026-04-25

## Phase: 5/5 — tool-call decision  ✅ GATE GREEN
## MVP: complete — all 5 phases shipped

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
  - 4.2b gemini/codex 429 stderr detection — additive; consumed
    transparently by 4.3 + 4.5 if/when added
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
  - 4.2b is the smallest concrete additive backlog item if/when 429s
    show up in the wild.
  - `/codex consult` or `/review` against `main` would be a sensible
    pre-share sanity check; branch is now 43+ commits ahead of
    origin/main (solo repo, no PR flow per memory).
