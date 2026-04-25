# FreelOAder status — 2026-04-25

## Phase: 3/5 — CodexAdapter + GeminiAdapter + round-robin
## Step: 3.1 — CodexAdapter
## Task: implement src/freeloader/adapters/codex.py: shell out via
         `codex exec --json`, parse the {thread.started, turn.started,
         item.completed, turn.completed} event stream, emit canonical
         Deltas, capture the server-assigned `thread_id` as the backend
         session id, resume subsequent turns via
         `codex exec resume <thread_id>` (PLAN decision #16).

Purpose (why this step exists):
  Phase 2 closed streaming + cancellation against one backend. Phase 3
  flushes out the `CLIAdapter` Protocol boundaries by adding a *second*
  concrete adapter. Codex is structurally closest to claude (clean JSONL
  stream, server-assigned thread_id, one model per turn), so it surfaces
  what pluralizing the Protocol costs without gemini's compound-provider
  quirk landing on top. The seam this exercises is PLAN principle #1
  (canonical Delta union as the adapter contract) and decision #16
  (server-assigned session id, resume via vendor's own resume verb).

Step 3.1 exit criteria (must all be true before step_done):
  - [ ] `src/freeloader/adapters/codex.py` exists with the same shape
        as `adapters/claude.py`: `map_event(event) -> list[Delta]` (pure),
        `parse_stream(lines)` async generator, `CodexAdapter.send(prompt,
        *, session_id, resume_session_id=None)` async generator.
  - [ ] `tests/adapters/fixtures/codex_basic.jsonl` exists (hand-written
        from PLAN.md § "codex" event-shape spec; live capture deferred
        to the live-CLI smoke harness later in phase 3).
  - [ ] `tests/adapters/test_codex_golden.py` exists and is green:
        - golden replay over `codex_basic.jsonl` produces the expected
          Delta sequence (SessionIdDelta → TextDelta → FinishDelta →
          UsageDelta).
        - `thread.started` → `SessionIdDelta(session_id=thread_id)`.
        - `turn.started` is a no-op (yields nothing).
        - `item.completed` with `item.type == "agent_message"` →
          `TextDelta(text=item.text)`.
        - `turn.completed` → `FinishDelta(reason="stop")` +
          `UsageDelta(models={...: ModelUsage(...)})`.
        - unknown event type → `RawDelta`.
        - malformed JSONL → `ErrorDelta(source="parse")` + stream
          continues.
        - blank lines skipped.
  - [ ] CodexAdapter satisfies the same `_Adapter` Protocol the Router
        already uses (signature + AsyncIterator[Delta] return). No router
        plumbing yet — round-robin is step 3.4; multi-adapter Router
        construction lands then.
  - [ ] Lifecycle finally block mirrors claude.py: SIGTERM → 3s wait →
        SIGKILL → rmtree(scratch). Step 2.3 already proved the
        router-side cancellation contract handles any well-behaved
        adapter, so subprocess plumbing here just needs to honor the
        same teardown discipline.
  - [ ] `gate_2.sh` still GREEN. Cross-phase invariants (ruff, pytest,
        no frontend→adapter import, append-only JOURNAL) green.

Out-of-scope for 3.1 (deferred to later phase-3 steps):
  - Per-conversation `CODEX_HOME` / `CLAUDE_CONFIG_DIR` / `XDG_*_HOME`
    env-var isolation — step 3.2.
  - GeminiAdapter — step 3.3.
  - Multi-adapter Router (round-robin selection per new conversation) —
    step 3.4.
  - bind() rewires + canonical-history replay on provider switch — 3.5.
  - `/v1/models` advertising freeloader/{auto,claude,codex,gemini} — 3.6.
  - Same contract test suite green against all three — 3.7.
  - Live `codex exec` smoke harness (gated by env flag) — 3.7-ish.

Phase 3 sketch (from ROADMAP.md):
  - 3.1 CodexAdapter (this step).
  - 3.2 Per-conversation CLI state isolation env vars.
  - 3.3 GeminiAdapter — compound provider, stats.models per turn.
  - 3.4 Round-robin Router (cycles providers per new conversation).
  - 3.5 Provider-switch mid-conversation: bind() rewires + replays
    canonical history into the new backend.
  - 3.6 /v1/models advertises freeloader/{auto,claude,codex,gemini}.
  - 3.7 Same contract test suite runs green against all three.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names still spike-observed; confirm with a
    live-claude smoke test before phase 3 closes
  - Starlette StreamingResponse aclose() injects GeneratorExit, not
    asyncio.CancelledError; both must be handled identically
  - json.dumps default separators are `, ` and `: `; OpenAI's wire bytes
    use compact `,` `:` — sse_encode pins this (2.5)
