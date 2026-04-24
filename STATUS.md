# FreelOAder status — 2026-04-24

## Phase: 2/5 — streaming + cancellation
## Step: 2.2 — turn state machine
## Task: introduce src/freeloader/core/turn_state.py carrying the
         explicit enum `queued/spawning/streaming/complete/cancelled/
         backend_error/rate_limited/timed_out` plus the transition
         table. Reaching any terminal state atomically writes a
         runtime event (PLAN principle #2 — no separate "logged"
         state). Router.dispatch becomes the machine's driver: it
         owns the per-conversation mutex (decision #1) and records
         the state transitions observed from the Delta stream.

Purpose (why this step exists):
  Step 2.1 shipped SSE streaming; clients now see tokens live. The
  hard problem in phase 2 is lifecycle, not parsing — a dropped
  client must not leak a CLI subprocess, a timed-out turn must not
  pretend to still be streaming, a rate_limit_event must not race
  with a router retry. The state machine gives the remaining phase-2
  steps (2.3 disconnect, 2.4 timeout, 2.5 byte-diff contract) one
  place to reason about those transitions instead of scattering
  boolean flags across app.py / router.py / adapter.

Step 2.1 recap (closed):
  - src/freeloader/frontend/sse.py — OpenAI chat.completion.chunk
    formatters (role / text / finish / usage + sse_encode +
    DONE_SENTINEL).
  - frontend/app.py branches on req.stream: stream=true returns a
    StreamingResponse whose generator consumes router.dispatch and
    emits role → N×text → finish → (usage) → [DONE].
  - stream_options.include_usage gates the usage chunk.
  - Conversation persistence happens inside the generator after the
    router's async-for drains, preserving the non-streaming path's
    append/rewrite semantics.
  - X-FreelOAder-Conversation-Id is set on the streaming response
    headers before any body bytes flow.
  - 8 new tests in tests/frontend/test_streaming.py.
  - 42/42 tests green; gate_1 still GREEN; gate_2 shows 2.1's
    SSE-handler check flipped (1/6 phase-2 specific green).

Phase 2 remaining steps (sketched; refine at each step_start):
  - Step 2.2 — turn state machine (this step).
  - Step 2.3 — client-disconnect → SIGTERM → SIGKILL-after-3s;
    the 50-drop stress test. PLAN decision #5 (discard
    backend_session_id on cancellation).
  - Step 2.4 — 5-minute hard timeout (PLAN decision #8).
  - Step 2.5 — SSE byte-diff contract test vs an OpenAI reference
    fixture. The "is our SSE shape actually compatible" check.

Exit criteria (for step 2.2):
  - [ ] src/freeloader/core/turn_state.py exists with a TurnState
        enum covering all eight states and a pure `transition()`
        function rejecting illegal edges.
  - [ ] tests/core/test_turn_state.py exercises every legal
        transition and at least one illegal one per state.
  - [ ] Router.dispatch drives the machine: queued → spawning →
        streaming → complete on the happy path; emits the correct
        terminal event (turn_done / timed_out / rate_limited / …).
  - [ ] A journal write failure surfaces as `backend_error`, not a
        silent success.
  - [ ] Existing tests (42) stay green; no behavior change visible
        to the frontend on the happy path.

Blockers: none.

Phase 1 still-untested slice (carried forward):
  - Live `claude -p` subprocess exercise. All phase-1 + 2.1 tests
    used fake adapters or monkey-patched asyncio.create_subprocess_
    exec. An opt-in live-claude smoke test should land somewhere in
    phase 2 to verify confirm_claude_model_usage_fields against
    real output.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names still spike-observed; confirm with
    a live-claude smoke test before phase 2 ships
