# FreelOAder status — 2026-04-25

## Phase: 3/5 — CodexAdapter + GeminiAdapter + round-robin
## Step: 3.5 — Provider switch mid-conversation (bind + replay)
## Task: add Router.bind(conversation_id, new_provider) that pins
         the conversation's next turn to a different adapter and
         replays the canonical history into it (PLAN principle #3).
         After the post-bind first turn completes, subsequent turns
         resume via the new backend's session id like any other
         conversation. The mechanism — pin without sid, dispatch
         replays full history, sid is captured on completion — is
         the same path quota-aware routing (phase 4) will take when
         it preempts a conversation off a saturated provider.

Purpose (why this step exists):
  Step 3.4 closed routing for *new* conversations. This step closes
  it for *existing* conversations whose provider needs to change —
  either because of a deliberate operator switch, a future quota
  ceiling breach (phase 4), or a vanished provider (the active pool
  shrank). The replay path (PLAN principle #3) is what lets canonical
  message history survive provider switches — backends never share
  state, so their "memory" of the conversation has to be re-established
  by replaying the full canonical history into the new backend's
  first turn.

Step 3.5 exit criteria (must all be true before step_done):
  - [ ] `Router.bind(conversation_id: str, new_provider: str) -> None`
        method exists. Sets `_bindings[conversation_id] = (new_provider,
        None)`. Raises `ValueError` if `new_provider` is not in the
        registered pool.
  - [ ] `Router.dispatch` recognizes the `(provider, None)` sentinel:
        - dispatches to the bound `new_provider` (NOT round-robin pick),
        - replays full canonical history (stored + new) as the prompt,
        - does NOT pass `resume_session_id`.
  - [ ] After the post-bind first turn completes successfully, the
        binding becomes `(new_provider, observed_backend_sid)` — i.e.
        subsequent turns resume normally on the new backend.
  - [ ] Cancelled / timed_out post-bind turn keeps the binding pinned
        but discards the new backend_sid (consistent with PLAN decision
        #5 for any cancelled turn). Next attempt re-replays history.
  - [ ] `turn_done.provider` reflects the new provider for the
        post-bind turn (and all subsequent turns).
  - [ ] Round-robin index is NOT advanced by a bind() — this isn't
        a "new conversation" event.
  - [ ] New `tests/core/test_rebind_replay.py` covers all of the
        above plus the error cases (rebind to unknown provider).
  - [ ] gate_2 still GREEN.

Out-of-scope for 3.5:
  - Implicit rebind when bound provider vanishes from the pool
    (operator removes an adapter mid-flight). Defer until there's a
    real call site that needs it.
  - Quota-aware automatic rebind — phase 4. The plumbing is here;
    only the trigger and policy are deferred.
  - /v1/models endpoint (3.6).
  - Same contract suite green against all three adapters (3.7).

Phase 3 sketch:
  - 3.1 CodexAdapter ✅
  - 3.2 Per-conversation CLI state isolation env vars ✅
  - 3.3 GeminiAdapter — compound provider ✅
  - 3.4 Round-robin Router ✅
  - 3.5 Provider switch + canonical history replay (this step).
  - 3.6 /v1/models advertises freeloader/{auto,claude,codex,gemini}.
  - 3.7 Same contract test suite runs green against all three.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini compound provider; UUID session_id (spike drift)
  - GEMINI_CLI_HOME couples auth to state — per-adapter mutex fallback
  - codex exec resume rejects -s; sandbox set on first turn persists
  - asyncio create_subprocess_exec env=dict REPLACES env entirely
  - Starlette aclose() injects GeneratorExit, not CancelledError
  - json.dumps default separators differ from OpenAI wire bytes
  - Router._bindings is now (provider, sid) tuple — load-bearing for 3.5
