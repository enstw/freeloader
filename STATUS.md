# FreelOAder status — 2026-04-25

## Phase: 2/5 — streaming + cancellation
## Step: 2.4 — 5-minute hard timeout (PLAN decision #8)
## Task: a turn that exceeds 5 minutes of wall-clock must be killed
         (subprocess SIGKILL after the existing 3s SIGTERM grace),
         driven to `timed_out` in the state machine, and journaled
         as `state=timed_out`. Like cancellation (step 2.3), the
         backend_session_id is discarded — a SIGKILLed CLI's session
         state is poisoned.

Purpose (why this step exists):
  PLAN decision #8 explicitly caps turn wall-time at 5 minutes. The
  cap protects against (a) a CLI that hangs without producing
  output, (b) runaway agent loops that the user didn't ask for, and
  (c) a backend whose response thread silently stops emitting
  delta events. Without this, a single bad turn can monopolize a
  conversation's serializing mutex (decision #1) indefinitely.

Step 2.3 recap (closed):
  - Router.dispatch now catches both asyncio.CancelledError AND
    GeneratorExit — Starlette's StreamingResponse uses aclose() on
    disconnect, which surfaces as GeneratorExit not CancelledError.
  - Cancellation drives turn → cancelled, writes turn_done event
    with state=cancelled, and discards the observed backend
    session id (PLAN decision #5). The binding is committed only
    on a non-cancelled terminal.
  - tests/frontend/test_disconnect_no_zombies.py: 50 sequential
    cycles via mocked subprocess; every proc gets terminate(),
    zero leaked. Plus an end-to-end router test that confirms
    state=cancelled + no binding write.
  - Router uses an outer try/finally to await adapter_gen.aclose()
    on every exit path — async-for does not auto-close iterators
    on exception, and the adapter's SIGTERM/rmtree only fire when
    the inner generator's finally runs.
  - 83/83 tests green; gate_2 phase-specific 4/6.

Phase 2 remaining steps:
  - Step 2.4 — 5-minute timeout (this step). Wrap the dispatch
    iteration in asyncio.wait_for or a deadline check; on hit,
    cancel the inner generator → SIGTERM/SIGKILL via the existing
    finally; turn → timed_out; do NOT bind (decision #5 logic
    extends to forcibly killed turns).
  - Step 2.5 — SSE byte-diff contract test. Capture an OpenAI
    reference SSE fixture; assert FreelOAder's stream produces an
    equivalent byte sequence (modulo id/created/timestamps).

Exit criteria (for step 2.4):
  - [ ] tests/core/test_turn_timeout.py exists and is green:
        Router.dispatch with a hung adapter (yields one delta then
        blocks forever) terminates within (test-overridable) timeout
        and reaches state=timed_out.
  - [ ] Configurable hard cap: default 5 minutes, override per
        Router instance via constructor arg (so the timeout test
        runs in <1s).
  - [ ] On timeout: turn → timed_out, turn_done event recorded
        with state=timed_out + outcome="timed_out", binding NOT
        written, adapter generator aclose'd so the subprocess gets
        SIGTERM/SIGKILL.
  - [ ] Existing 83 tests stay green; no behavior change on
        sub-timeout turns.

Blockers: none.

Phase 1 still-untested slice (carried forward):
  - Live `claude -p` subprocess exercise. All phase-1 + 2.1/2.2/2.3
    tests use fake adapters or monkey-patched
    asyncio.create_subprocess_exec. An opt-in live-claude smoke
    test should land before phase 2 closes to verify
    confirm_claude_model_usage_fields against real output and to
    sanity-check the SIGTERM/SIGKILL/scratch-rmtree plumbing.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names still spike-observed; confirm with
    a live-claude smoke test before phase 2 ships
  - Starlette StreamingResponse cancels via GeneratorExit, not
    CancelledError; both paths must be handled identically (2.3)
