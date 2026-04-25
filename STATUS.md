# FreelOAder status — 2026-04-25

## Phase: 2/5 — streaming + cancellation  ✅ GATE GREEN
## Next phase: 3/5 — CodexAdapter + GeminiAdapter + round-robin
## Step: 3.1 — CodexAdapter (codex first, then gemini per ROADMAP)
## Task: implement src/freeloader/adapters/codex.py: shell out via
         `codex exec` with `--json`, parse the {thread.started,
         turn.started, item.completed, turn.completed} event stream,
         emit canonical Deltas, capture the server-assigned
         `thread_id` as the backend session id, resume subsequent
         turns via `codex exec resume <thread_id>` (decision #16).

Purpose (why this step exists):
  Phase 2 closed the streaming + cancellation story for one
  backend. Phase 3 flushes out the `CLIAdapter` Protocol boundaries
  by adding a *second* concrete adapter. Codex is structurally
  closest to claude (clean JSONL stream, server-assigned thread_id,
  one model per turn), so it surfaces what pluralizing the Protocol
  costs without gemini's compound-provider quirk landing on top.

Phase 2 recap (closed, 17/17 gate checks green):
  - Step 2.1 SSE streaming for /v1/chat/completions ✅
  - Step 2.2 turn state machine + journal-write-failure logging ✅
  - Step 2.3 disconnect → SIGTERM, 50-cycle no-zombie test, decision
    #5 (discard backend session id on cancel) ✅
  - Step 2.4 5-minute hard timeout via asyncio.timeout, decision #8 ✅
  - Step 2.5 SSE byte-diff vs OpenAI reference (caught a real
    json.dumps separators divergence; fixed in sse_encode) ✅
  - 91/91 tests passing.

Entry criteria for phase 3 (met):
  - [x] Gate 2 GREEN; 91/91 tests passing
  - [x] All phase 2 artifacts shipped
  - [ ] phase_done entry in JOURNAL.jsonl with phase-close commit sha
        (next step: scripts/advance.sh)

Phase 1 + 2 still-untested slice (carried into phase 3):
  - Live `claude -p` subprocess exercise. Every test so far uses
    fake adapters or monkey-patched asyncio.create_subprocess_exec.
    Phase 3 should include an opt-in live-CLI smoke harness for
    *both* claude and codex (gated behind an env flag so CI stays
    offline). The `confirm_claude_model_usage_fields` lesson from
    phase 1 + the `codex thread_id` shape from PLAN decision #16
    both want empirical confirmation.

Phase 3 sketch (from ROADMAP.md):
  - 3.1 CodexAdapter (this step).
  - 3.2 Per-conversation CLI state isolation env vars (decision
    #16): CODEX_HOME / CLAUDE_CONFIG_DIR / XDG_*_HOME.
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
  - claude modelUsage field names still spike-observed; confirm with
    a live-claude smoke test before phase 3 closes
  - Starlette StreamingResponse aclose() injects GeneratorExit, not
    asyncio.CancelledError; both must be handled identically
  - json.dumps default separators are `, ` and `: `; OpenAI's wire
    bytes use compact `,` `:` — sse_encode pins this (2.5)
