# FreelOAder status — 2026-04-25

## Phase: 3/5 — CodexAdapter + GeminiAdapter + round-robin
## Step: 3.4 — Round-robin Router across the three adapters
## Task: refactor Router so it accepts claude/codex/gemini adapters,
         picks one per *new* conversation in round-robin order, and
         remembers which provider each conversation is bound to so
         resume turns dispatch to the same backend. Quota-aware
         routing is phase 4; provider-switch + canonical-history
         replay is step 3.5.

Purpose (why this step exists):
  Phases 3.1–3.3 added the three concrete adapters. The router has
  been single-claude since phase 1. This step fulfils PLAN principle
  #5's first half — selection by policy, not hardcoded — at the
  simplest possible policy (cycle). It also extends the binding
  shape from `conv_id → backend_sid` to `conv_id → (provider,
  backend_sid)` so a resumed turn dispatches to the right adapter,
  which is the load-bearing piece for 3.5's mid-conversation
  provider switch.

Step 3.4 exit criteria (must all be true before step_done):
  - [ ] Router constructor accepts `claude=`, `codex=`, `gemini=`
        kwargs (any combination); the set of non-None adapters is
        the active provider pool. Phase-1 default: if all three are
        None, fall back to a single ClaudeAdapter() — preserves
        existing test ergonomics.
  - [ ] `_bindings` value type changes from `str` (backend_sid) to
        `tuple[str, str]` (provider_name, backend_sid). All five
        existing test sites that read `_bindings` updated.
  - [ ] New conversation: round-robin advances per first-turn
        dispatch. Order = the order kwargs were supplied (claude,
        codex, gemini in the constructor signature; insertion-order
        dict semantics). Cycle wraps.
  - [ ] Resume: bound conversation always dispatches to the bound
        provider. Round-robin index does NOT advance for resumes.
  - [ ] `turn_done.provider` field reflects the actual provider
        used (was hardcoded to "claude").
  - [ ] PLAN decision #5 invariant intact: cancel/timeout discards
        the binding (next turn picks a *fresh* provider via
        round-robin, not the same one).
  - [ ] Three new tests in tests/core/test_router_round_robin.py:
        - 3 fresh conversations cycle through claude → codex →
          gemini → claude.
        - Same conversation stays bound to the same provider across
          turns.
        - turn_done.provider matches the dispatched adapter.
  - [ ] gate_2 still GREEN. Cross-phase invariants green.

Out-of-scope for 3.4 (deferred):
  - bind() rewires + canonical history replay (3.5).
  - /v1/models advertising freeloader/{auto,claude,codex,gemini}
    (3.6).
  - Same contract suite green against all three (3.7).
  - Quota-aware routing — that's a different selection strategy
    layered on the same plumbing; phase 4.
  - Live-CLI smoke harness across all three.

Phase 3 sketch:
  - 3.1 CodexAdapter ✅
  - 3.2 Per-conversation CLI state isolation env vars ✅
  - 3.3 GeminiAdapter — compound provider ✅
  - 3.4 Round-robin Router (this step).
  - 3.5 Provider-switch mid-conversation: bind() rewires + replays
    canonical history into the new backend.
  - 3.6 /v1/models advertises freeloader/{auto,claude,codex,gemini}.
  - 3.7 Same contract test suite runs green against all three.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini compound provider (stats.models per turn) — confirmed live
  - gemini session_id is now UUID, not int (spike drift)
  - GEMINI_CLI_HOME couples auth to state — gemini uses per-adapter
    mutex instead (PLAN-decision-#16 fallback)
  - codex exec resume rejects -s; sandbox set on first turn persists
  - codex --json is pure stdout; no model id field
  - asyncio create_subprocess_exec env=dict REPLACES env entirely
  - Starlette aclose() injects GeneratorExit, not CancelledError
  - json.dumps default separators differ from OpenAI wire bytes
