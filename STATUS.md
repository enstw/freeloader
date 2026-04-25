# FreelOAder status — 2026-04-25

## Phase: 2/5 — streaming + cancellation
## Step: 2.5 — SSE byte-diff vs OpenAI reference (last gate-2 check)
## Task: capture a small fixture of the *exact* byte sequence an
         OpenAI chat.completion stream emits (chunk envelopes,
         finish chunk, usage chunk, [DONE]). Drive a mock-adapter
         dispatch through the FreelOAder streaming path and assert
         our wire bytes match the fixture modulo the volatile
         fields (id, created). This pins the SSE shape so
         OpenAI-SDK clients (which are strict about chunk shape)
         keep working when phase-3 adapters land.

Purpose (why this step exists):
  Step 2.1 made stream=true work; tests there assert chunk
  *structure*. The byte-level contract — exact envelope keys,
  exact chunk separator (`\n\n`), exact terminator (`data: [DONE]
  \n\n`), and the "empty choices array on usage chunk" quirk — is
  what OpenAI-SDK clients depend on. A semantic match isn't
  enough; the bytes have to line up. This is also where the gate
  catches regressions when phase-3 adapters force changes to the
  streaming path.

Step 2.4 recap (closed):
  - Router gained `turn_timeout_seconds` (default 300.0, override
    via constructor arg). PLAN decision #8.
  - dispatch loop now wraps adapter iteration with
    `async with asyncio.timeout(self.turn_timeout_seconds)`. On
    deadline, asyncio raises CancelledError into our await; the
    timeout context converts it to TimeoutError, which we catch
    and use to drive turn → timed_out, journal it, and return
    cleanly so the SSE consumer sees end-of-stream.
  - Forcibly-killed turns (timed_out, like cancelled in 2.3) do
    NOT bind their backend session id — the next turn starts
    fresh with full history replay (PLAN decision #5 logic).
  - tests/core/test_turn_timeout.py: hung adapter terminates in
    <1s under 0.1s timeout; default-cap constant assertion;
    fast-turn sanity check that the timeout doesn't perturb the
    happy path.
  - 87/87 tests green; gate_2 phase-specific 5/6.

Phase 2 remaining steps:
  - Step 2.5 — SSE byte-diff (this step). Last gate-2 check.

Exit criteria (for step 2.5):
  - [ ] tests/frontend/test_sse_shape.py exists and is green.
  - [ ] A reference fixture of expected SSE bytes is checked into
        the test (or generated from a documented OpenAI chunk shape
        + golden id/created substitution).
  - [ ] The test drives a TestClient through stream=true with a
        scripted adapter and asserts the response body matches the
        reference modulo id / created / model fields (substituted
        out before diff).
  - [ ] Existing 87 tests stay green.

Phase 2 close (after 2.5):
  - Run scripts/gate_2.sh — must be GREEN (all 6 phase-2 checks
    plus all 11 cross-phase invariants).
  - Run the live-claude smoke test (carried-forward from phase 1)
    if feasible before advancing — it sanity-checks the entire
    chain end-to-end with a real subprocess. If the test discovers
    drift in the modelUsage field shape, fix the adapter mapper
    and add a JOURNAL lesson.
  - scripts/advance.sh advances to phase 3.

Blockers: none.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names still spike-observed; confirm with
    a live-claude smoke test before phase 2 ships
  - Starlette StreamingResponse aclose() injects GeneratorExit, not
    asyncio.CancelledError; both must be handled identically
