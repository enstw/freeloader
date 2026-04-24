# FreelOAder status — 2026-04-24

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.6 — tools-strip (chat-only mitigation)  ✅ complete
## Task: advance to step 1.7 — end-to-end two-turn. Last piece of
         phase 1: conversation storage + hash-of-prefix identity +
         history_diff wired into the handler; second turn passes
         resume_session_id. Greens the final gate_1 line and closes
         phase 1.

Purpose (why this step existed):
  PLAN hard problem #1 has three options — chat-only, shim,
  passthrough. Phase 1 shipped chat-only: tools and tool_choice
  get dropped with a structured WARNING. System-prompt slot stays
  under adapter control so the shim path remains viable at phase 5.

Entry criteria (met):
  - [x] Step 1.5 shipped (HEAD=dd0ec0a)
  - [x] 28/28 tests green; gate_1 8/10 phase-specific green

Exit criteria (met):
  - [x] ChatCompletionRequest gained tools + tool_choice as
        recognized fields (still extra="allow" for temperature /
        top_p / etc)
  - [x] frontend/app.py _warn_if_tools_dropped() logs a stdlib-
        logging WARNING with extra={dropped_fields, model, path}
        when either non-empty field is present. Empty tools=[] is
        deliberately NOT treated as a strip — prevents warning
        spam for habitual-tools=[] clients.
  - [x] tests/frontend/test_tools_stripped.py: 4 tests:
          • tools + tool_choice present → 200 + WARN with both fields
          • tools-only → 200 + WARN with ["tools"]
          • no tools field → 200 + no WARN
          • empty tools=[] → 200 + no WARN (anti-spam)
  - [x] `uv run ruff check src tests`          exits 0
  - [x] `uv run ruff format --check src tests` exits 0
  - [x] `uv run pytest -q`                     32/32 green
  - [x] scripts/gate_1.sh "client-sent tools are stripped with
        warning" flipped to [ok] (9/10 phase-specific green)
  - [x] JOURNAL.jsonl: step_start + 1 decision + step_done

Scope — things 1.6 deliberately did NOT do:
  - No output-parsing shim or passthrough. Phase 5 decision.
  - No 400-on-tools. Existing clients keep working.
  - No stream-aware strip. Phase 2.
  - No allowlist.

Next step: 1.7 — end-to-end two-turn (closes phase 1).
  - Conversation storage: <data_dir>/conversations/<id>.jsonl, one
    line per CanonicalMessage, per decision #6.
  - Conversation identity: SHA-256 of
    (system_messages + first_user_message) per decision #14;
    `X-FreelOAder-Conversation-Id: <opaque>` header override.
  - Events log: <data_dir>/events.jsonl with per-turn `turn_done`
    record {conversation_id, backend_session_id, provider, outcome,
    usage}. Append-only, single-writer asyncio lock.
  - Router stores {conversation_id → (provider, backend_session_id)}
    in-memory binding; reads from the conversation file at startup if
    an id shows up that isn't cached. Backend session id extracted
    from SessionIdDelta.
  - Frontend: call history_diff before dispatch; persist canonical
    turn + write events.jsonl after dispatch.
  - Test: tests/e2e/test_claude_two_turn.py spins up the FastAPI app
    with a fake adapter that returns different session_ids / content
    per call. First POST establishes the binding; second POST sends
    the full message history with turn 1's assistant reply;
    history_diff extracts just the new user turn; adapter.send is
    called with resume_session_id=first-turn's session. Asserts
    conversation_id is stable across turns and that `<data_dir>/
    conversations/<id>.jsonl` has 4 lines (u1, a1, u2, a2) and
    events.jsonl has 2 turn_done records.
  - Flips the last gate_1 line → green = Gate 1 GREEN = Phase 1 DONE.

Blockers: none.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names spike-observed; confirm at 1.7 e2e
