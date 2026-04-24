# FreelOAder status — 2026-04-24

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.3 — FastAPI frontend: /v1/chat/completions (non-streaming) + minimal router  ✅ complete
## Task: advance to step 1.4 — history_diff. Implement
         diff_against_stored(conversation, incoming_messages) with the
         three MVP cases (append-only / regenerate-last / mismatch),
         define CanonicalMessage, and reconcile adapter/router
         signatures with PLAN principle #1.

Purpose (why this step existed):
  Made PLAN decision #12 (FastAPI) and principle #6 (dumb frontend)
  executable. Wired the architectural seam
  "frontend → router → adapter" via Router injection so the fake
  adapter pattern works in tests. Single-call /v1/chat/completions
  round-trips Delta → ChatCompletion. Live claude still not exercised
  — that's 1.7.

Entry criteria (met):
  - [x] Step 1.2 shipped (HEAD=713779b)
  - [x] ClaudeAdapter.send + Delta union live
  - [x] Golden test covers JSONL→Delta for all 7 variants

Exit criteria (met):
  - [x] pyproject.toml adds `fastapi>=0.115` runtime + `httpx>=0.27`
        dev; uv.lock regenerated (10 new packages: fastapi, starlette,
        anyio, httpx, httpcore, h11, certifi, idna, annotated-doc,
        plus h11 transitive)
  - [x] adapters/claude.py exports flatten_messages() producing
        role-tagged plaintext ([ROLE]\n<text>\n[/ROLE])
  - [x] src/freeloader/router.py: Router.dispatch(messages, *,
        session_id=None, resume_session_id=None) → AsyncIterator[Delta]
  - [x] src/freeloader/frontend/app.py: create_app(router=None) +
        POST /v1/chat/completions handler (~50 lines per principle #6)
  - [x] tests/frontend/__init__.py + test_chat_completions_
        nonstreaming.py — 4 tests green:
          • happy path: OpenAI ChatCompletion shape + flattened prompt
          • stream=true → 400
          • error FinishDelta → "error" finish_reason + zero usage
          • unknown request fields (tools/temperature) accepted
  - [x] app.py imports only from freeloader.router (cross-phase
        invariant honored by convention)
  - [x] `uv run ruff check src tests`          exits 0
  - [x] `uv run ruff format --check src tests` exits 0
  - [x] `uv run pytest -q`                     13/13 green
  - [x] scripts/gate_1.sh common invariants + phase-1 checks stay
        green; 6/10 phase-specific green (unchanged — 1.3 adds
        infrastructure, not a new gated behavior test)
  - [x] JOURNAL.jsonl: step_start + 3 decisions + step_done

Scope — things 1.3 deliberately did NOT do:
  - No streaming. Phase 2. Handler rejects stream=true with 400.
  - No tools=[...] stripping. 1.6. extra="allow" lets clients send
    tools/temperature without 422; silently ignored.
  - No history_diff. 1.4. Each turn flattens the full messages array.
  - No conversation identity / session persistence. Fresh UUID per
    request; 2-turn resume at 1.7.
  - No auth (decision #2). Phase 2+.
  - No uvicorn / server entry point. TestClient only; live-server
    lands at 1.7 e2e.
  - No sandbox (--add-dir). 1.5.
  - No cancellation plumbing. Phase 2.

Next step: 1.4 — history_diff.
  - src/freeloader/canonical/history_diff.py (stub → real):
    diff_against_stored(conversation, incoming_messages) returning
    new_turn_messages. Three MVP cases per principle #4:
    (a) append-only new turn, (b) client regeneration replacing the
    last assistant turn, (c) mismatch when stored prefix diverges →
    raise.
  - src/freeloader/canonical/ grows a CanonicalMessage type
    (role, content_blocks?, tool_calls?, metadata?). Minimal shape
    for 1.4: role + text content.
  - Router+Adapter signatures reconcile with PLAN principle #1:
    send/dispatch now take canonical messages, not raw dicts.
    flatten_messages moves to accepting list[CanonicalMessage].
  - tests/canonical/test_history_diff.py covers the three cases.
  - Flips "history_diff unit test exists" in gate_1 → green
    (7/10 phase checks green after 1.4).

Blockers: none.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names spike-observed; confirm at 1.7 e2e
