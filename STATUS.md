# FreelOAder status — 2026-04-24

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation  ✅ GATE GREEN
## Next phase: 2/5 — streaming + cancellation
## Step: 2.1 — SSE streaming for /v1/chat/completions
## Task: make `stream=true` work. Stream Delta events as OpenAI-
         shaped SSE chunks live (TextDelta → delta.content chunk;
         FinishDelta → finish_reason on final chunk; UsageDelta in
         the final chunk when `stream_options.include_usage=true`).

Purpose (why this step exists):
  Phase 1 closed with non-streaming only. Real clients (Cursor,
  aider, Hermes, OpenAI SDK) default to streaming; a non-streaming
  proxy is usable but not comfortable. Phase 2 adds SSE so the
  user sees tokens as claude produces them, and builds the
  cancellation plumbing that makes client-disconnect → SIGTERM
  work (PLAN decision #5).

Phase 1 recap (closed, 10/10 gate checks green):
  - Step 1.1 scaffold ✅  (pyproject + src skeleton)
  - Step 1.2 ClaudeAdapter + Delta union + golden replay ✅
  - Step 1.3 FastAPI frontend + router + flatten_messages ✅
  - Step 1.4 history_diff + CanonicalMessage ✅
  - Step 1.5 scratch cwd sandbox ✅
  - Step 1.6 tools-strip chat-only mitigation ✅
  - Step 1.7 e2e two-turn (hash-of-prefix identity + storage +
    events.jsonl + resume) ✅

Phase 1 still-untested slice (deferred to Phase 2+ e2e):
  - Live `claude -p` subprocess exercise. All phase-1 tests used
    fake adapters or monkey-patched asyncio.create_subprocess_exec.
    Phase 2 should include a smoke test that runs actual claude
    once as a sanity check (gated behind an opt-in env flag so CI
    stays offline). confirm_claude_model_usage_fields is a lesson
    to revisit when that smoke test runs.

Entry criteria (met):
  - [x] Gate 1 GREEN; 35/35 tests passing
  - [x] Phase 1 artifacts all shipped
  - [x] phase_done entry logged in JOURNAL.jsonl with commit sha

Exit criteria (for step 2.1):
  - [ ] `/v1/chat/completions` with `stream=true` returns 200 and
        a `text/event-stream` body
  - [ ] Each TextDelta emits an OpenAI-shaped chunk:
        `data: {"id":..., "object":"chat.completion.chunk", "model":
        ..., "choices":[{"index":0,"delta":{"content":"..."},
        "finish_reason":null}]}\n\n`
  - [ ] FinishDelta emits the terminal chunk with finish_reason set
        and an empty delta object
  - [ ] Final `data: [DONE]\n\n` sentinel
  - [ ] When `stream_options.include_usage=true`, append a usage
        chunk before [DONE]
  - [ ] Test: tests/frontend/test_streaming.py — TestClient reads
        SSE, asserts ordered chunks, finish_reason, usage inclusion
  - [ ] Existing non-streaming tests stay green

Blockers: none.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names still spike-observed; confirm with
    a live-claude smoke test before phase 2 ships
