# FreelOAder status — 2026-04-25

## Phase: 3/5 — CodexAdapter + GeminiAdapter + round-robin
## Step: 3.6 — /v1/models endpoint
## Task: add a GET /v1/models handler that advertises one
         freeloader/<provider> id per registered adapter, plus
         freeloader/auto when 2+ adapters are registered. Static
         OpenAI-shaped response. The model→provider routing hook
         (whether req.model="freeloader/codex" pins the dispatch)
         is deliberately deferred.

Purpose (why this step exists):
  OpenAI clients call /v1/models to discover what's available; some
  refuse to operate without it returning a non-empty list. This
  endpoint exists primarily for compatibility, secondarily so a user
  reading the endpoint can see what FreelOAder is configured with.

  Whether `req.model="freeloader/codex"` should pin dispatch to
  codex is a real product decision (sticky binding vs hint-always-
  wins vs implicit rebind+replay) — and the answer ripples through
  Router.dispatch and the binding semantics. Calling that decision
  out as its own step (or as part of phase 4 quota routing, where
  the same code path lights up) is cleaner than burying it in a
  five-line endpoint.

Step 3.6 exit criteria (must all be true before step_done):
  - [ ] `GET /v1/models` returns 200 with OpenAI-shaped body:
        `{"object": "list", "data": [{"id": ..., "object": "model",
        "created": ..., "owned_by": "freeloader"}, ...]}`.
  - [ ] Advertised ids:
        - `freeloader/<name>` for every adapter registered in the
          Router (preserves registration order).
        - `freeloader/auto` if 2+ adapters are registered. Single-
          adapter Routers do not advertise it (no choice to make).
  - [ ] `tests/frontend/test_models_endpoint.py` covers:
        - single-adapter case (claude only): one model, no
          freeloader/auto.
        - multi-adapter case (claude + codex + gemini): four models
          including freeloader/auto first.
        - `created` is a positive int (Unix timestamp).
        - `owned_by` is `"freeloader"`.
  - [ ] gate_2 still GREEN.

Out-of-scope for 3.6:
  - Honoring `req.model` for routing (sticky-binding-vs-always-wins
    UX call). Currently req.model is echoed back in the response
    chunks; routing is round-robin or follows the existing binding.
  - Same contract test suite green against all three adapters (3.7).
  - Live-CLI smoke harness.

Phase 3 sketch:
  - 3.1 CodexAdapter ✅
  - 3.2 Per-conversation CLI state isolation env vars ✅
  - 3.3 GeminiAdapter — compound provider ✅
  - 3.4 Round-robin Router ✅
  - 3.5 Provider switch + canonical history replay ✅
  - 3.6 /v1/models endpoint (this step).
  - 3.7 Same contract test suite runs green against all three.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens
  - agent-loop contamination observable not preventable under OAuth
  - gemini compound provider; UUID session_id (spike drift)
  - GEMINI_CLI_HOME couples auth to state — per-adapter mutex fallback
  - codex exec resume rejects -s
  - asyncio create_subprocess_exec env=dict REPLACES env entirely
  - Starlette aclose() injects GeneratorExit, not CancelledError
  - json.dumps default separators differ from OpenAI wire bytes
  - Router._bindings is now (provider, sid|None) — None pins for replay
