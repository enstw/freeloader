# FreelOAder status — 2026-04-25

## Phase: 2/5 — streaming + cancellation
## Step: 2.3 — client disconnect → SIGTERM → SIGKILL
## Task: when the HTTP client drops the SSE connection mid-stream,
         FreelOAder must terminate the underlying claude subprocess
         (SIGTERM, SIGKILL after 3s if still alive), drive the turn
         state machine to `cancelled`, and discard the
         backend_session_id captured during that turn (PLAN
         decision #5 — resuming via a poisoned session id causes
         permanent state drift). Stress test: 50 sequential drops
         leaves zero zombie `claude` processes.

Purpose (why this step exists):
  The SSE plumbing from 2.1 + state machine from 2.2 are the easy
  half of phase 2. The hard half is lifecycle: a client that hits
  Ctrl-C in cursor / aider must not leave a `claude` process
  burning quota in the background. Without this step, FreelOAder
  silently degrades over time as orphaned subprocesses accumulate.

Step 2.2 recap (closed):
  - src/freeloader/core/turn_state.py — TurnState (8 variants),
    TERMINAL_STATES, transition() pure function with
    IllegalTransition, is_terminal/legal_targets helpers, Turn
    driver class.
  - tests/core/test_turn_state.py — 31 tests covering every legal
    edge, ≥1 illegal per state, terminal predicate, and the Turn
    driver.
  - Router.dispatch now drives the machine: queued → spawning →
    streaming → terminal. Terminal selection: rate_limit_event with
    status != "allowed" supersedes a clean stop (→ rate_limited);
    FinishDelta(error) → backend_error; empty-stream adapter exit
    → backend_error; otherwise → complete.
  - turn_done event gains a `state` field; `outcome` (mirrors
    finish_reason) is preserved for backward compat.
  - Journal-write failures now log via stdlib `freeloader.router`
    error logger with intended_state/intended_outcome — observable,
    not silent.
  - 79/79 tests green; gate_2 phase-specific now 3/6.

Phase 2 remaining steps (sketched; refine at each step_start):
  - Step 2.3 — client-disconnect cancellation (this step).
  - Step 2.4 — 5-minute hard timeout (PLAN decision #8). Needs
    asyncio.wait_for around the dispatch loop; turn → timed_out;
    subprocess SIGKILL.
  - Step 2.5 — SSE byte-diff contract test vs an OpenAI reference
    fixture. Pinning the wire shape so vendor SDKs don't choke.

Exit criteria (for step 2.3):
  - [ ] tests/frontend/test_disconnect_no_zombies.py exists and is
        green: 50 sequential client-disconnect cycles produce zero
        leaked claude subprocesses (verified via mock subprocess
        with SIGTERM/SIGKILL accounting; no live claude required).
  - [ ] StreamingResponse generator catches the asyncio cancellation
        that fires on client disconnect and propagates it into the
        adapter's send() loop, which terminates the subprocess.
  - [ ] ClaudeAdapter.send() finally-block sends SIGTERM, waits 3s,
        then SIGKILL if still alive; deletes the scratch dir.
  - [ ] Cancelled turn writes `state=cancelled` to events.jsonl;
        the captured backend_session_id is NOT recorded in the
        binding map (PLAN decision #5 discards it).
  - [ ] Existing 79 tests stay green.

Blockers: none.

Phase 1 still-untested slice (carried forward):
  - Live `claude -p` subprocess exercise. All phase-1 + 2.1/2.2
    tests use fake adapters or monkey-patched
    asyncio.create_subprocess_exec. An opt-in live-claude smoke
    test should land before phase 2 closes to verify
    confirm_claude_model_usage_fields against real output and to
    sanity-check the SIGTERM/SIGKILL plumbing of step 2.3.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names still spike-observed; confirm with
    a live-claude smoke test before phase 2 ships
