# FreelOAder status — 2026-04-24

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.2 — ClaudeAdapter subprocess spawn + JSONL→Delta mapping + golden replay test  ✅ complete
## Task: advance to step 1.3 — FastAPI frontend: /v1/chat/completions
         (non-streaming) wired to a minimal router that invokes
         ClaudeAdapter. Decision #12 (FastAPI) materializes here.

Purpose (why this step existed):
  Materialized PLAN principle #1 (the CLIAdapter seam) for the first
  vendor: claude. Delta union is now code, not schema-in-a-doc.
  `ClaudeAdapter.send()` has a real subprocess implementation so later
  steps (1.3 frontend, 1.4 history_diff, 1.5 sandbox, 1.6 tools-strip,
  1.7 e2e) have an adapter to call against. Golden-fixture replay test
  (principle #8) locks the JSONL→Delta contract for all 7 variants.

Entry criteria (met):
  - [x] Step 1.1 scaffold green (HEAD=5e250d4)
  - [x] `uv run ruff` + `uv run pytest` pipeline green
  - [x] src/freeloader/adapters/claude.py + canonical/ exist as stubs
  - [x] Gate 1 file-existence slice green; 5 behavior tests red (expected)

Exit criteria (met):
  - [x] src/freeloader/canonical/deltas.py defines Delta union with 7
        variants (TextDelta, FinishDelta, SessionIdDelta, UsageDelta,
        RateLimitDelta, ErrorDelta, RawDelta) + ModelUsage sub-model
        per PLAN principle #1
  - [x] src/freeloader/adapters/claude.py exports:
          • map_event(event: dict) -> list[Delta]        pure mapper
          • parse_stream(lines)                          async generator
          • ClaudeAdapter.send(prompt, *, session_id,
                               resume_session_id=None)   subprocess spawn
  - [x] pyproject.toml adds `pydantic>=2.9` to runtime deps; uv.lock
        regenerated
  - [x] tests/adapters/__init__.py + tests/adapters/fixtures/
        claude_basic.jsonl exist
  - [x] tests/adapters/test_claude_golden.py — 7 async tests green,
        covering: full turn replay, multi-text-block, unknown→RawDelta,
        malformed-JSONL→ErrorDelta+continue, modelUsage-missing
        fallback, error subtype→FinishDelta(error), blank-line skip
  - [x] `uv run ruff check src tests`           exits 0
  - [x] `uv run ruff format --check src tests`  exits 0
  - [x] `uv run pytest -q`                      exits 0  (9/9 passed)
  - [x] scripts/gate_1.sh "golden JSONL fixture replay for
        ClaudeAdapter" flipped to [ok]  (6 of 10 phase-specific green)
  - [x] JOURNAL.jsonl: step_start + 3 decisions + 1 lesson + step_done

Scope — things 1.2 deliberately did NOT do:
  - No live subprocess call in tests. Golden test feeds a fixture
    through parse_stream() directly; subprocess path is exercised only
    at step 1.7 e2e. Principle #8 coverage, not an integration test.
  - No CanonicalMessage type. Adapter takes `prompt: str` for 1.2;
    canonical-message flattening lands in 1.4 with history_diff.
    Captured as JOURNAL decision (subject=adapter_prompt_signature).
  - No `system` parameter on send(). Dead-slot speculation ruled out;
    lands when the first caller materializes.
  - No --add-dir / scratch cwd. Sandbox wiring is step 1.5.
  - No tools=[...] stripping. Frontend work in step 1.6.
  - No cancellation / SIGTERM-on-disconnect. Phase 2 concern; send()
    has a best-effort terminate in finally so local aborted awaits
    don't leak zombies, but no client-disconnect plumbing.
  - No history replay, no role-tagged flatten format.

Next step: 1.3 — FastAPI frontend.
  - Add fastapi + uvicorn runtime deps (decision #12).
  - src/freeloader/frontend/app.py: POST /v1/chat/completions handler,
    non-streaming only, ~50 lines (principle #6). Strips unknown fields
    for now; tools= stripping lands formally in 1.6.
  - Minimal router src/freeloader/router.py: picks ClaudeAdapter (no
    quota / round-robin yet — phase 3+ concern). Generates a fresh
    session UUID per new conversation.
  - Wraps Delta stream into a single OpenAI ChatCompletion response
    object (concat TextDeltas, read FinishDelta + UsageDelta from the
    terminal pair, include model name).
  - tests/frontend/test_chat_completions_nonstreaming.py — uses
    FastAPI TestClient with a fake ClaudeAdapter (yields canned
    Deltas), asserts OpenAI response shape.
  - Remains untested against live claude until step 1.7.

Blockers: none.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names spike-observed; confirm at 1.7 e2e
