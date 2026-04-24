# FreelOAder status — 2026-04-24

## Phase: 1/5 — ClaudeAdapter, non-streaming, single conversation
## Step: 1.5 — scratch cwd sandbox  ✅ complete
## Task: advance to step 1.6 — tools-strip. Frontend/router drops
         `tools=[...]` / `tool_choice` from requests with a structured
         log warning. Greens the gate_1 tools-strip test line.

Purpose (why this step existed):
  Decision #3 became code: every claude subprocess gets an
  ephemeral scratch cwd under data_dir and is invoked with
  --add-dir <scratch>. Per-turn (per-send()) lifecycle; cleaned up
  in finally. Defense-in-depth, not a hard boundary — OS-level
  sandboxing is explicitly deferred past phase 1.

Entry criteria (met):
  - [x] Step 1.4 shipped (HEAD=3b5720d)
  - [x] gate_1 at 7/10 phase-specific green pre-1.5

Exit criteria (met):
  - [x] src/freeloader/config.py exports resolve_data_dir() with
        FREELOADER_DATA_DIR → XDG_DATA_HOME/freeloader →
        ~/.local/share/freeloader
  - [x] ClaudeAdapter.__init__ accepts data_dir + executable
  - [x] ClaudeAdapter.send() creates data_dir/scratch/<sid>/<turn_id>/,
        spawns with --add-dir + cwd, cleans up in finally
  - [x] tests/adapters/test_claude_sandbox.py: 6 tests covering
        argv construction, cwd existence at spawn time, per-turn
        cleanup after send(), -r resume flag, session-id directory
        layout, and all three data_dir resolution paths
  - [x] `uv run ruff check src tests`          exits 0
  - [x] `uv run ruff format --check src tests` exits 0
  - [x] `uv run pytest -q`                     28/28 green
  - [x] scripts/gate_1.sh "scratch cwd sandbox test" flipped to [ok]
        (8 of 10 phase-specific green after 1.5)
  - [x] JOURNAL.jsonl: step_start + 3 decisions + step_done

Scope — things 1.5 deliberately did NOT do:
  - No OS-level sandbox. ROADMAP phase 1 defers to phase 3+.
  - No CLI state isolation (decision #16 CLAUDE_CONFIG_DIR).
  - No freeloader.toml (decision #10 lands phase 2). 1.5 only
    touched the data_dir path.
  - No startup cleanup of stale scratch dirs. Per-turn finally is
    the only GC; a crashed process leaves a dir behind. Acceptable
    for MVP.

Next step: 1.6 — tools-strip.
  - Frontend handler (or a router-level filter) drops `tools=[...]`
    and `tool_choice` from the incoming request before dispatch,
    and emits a single structured log WARNING naming the fields
    dropped (PLAN hard problem #1: chat-only mitigation).
  - tests/frontend/test_tools_stripped.py sends a request with
    `tools=[{"type":"function",...}]` and `tool_choice="auto"` and
    asserts:
      • response still 200 OK (previously covered by 1.3's
        "unknown fields allowed" test, but 1.6 formalizes the strip)
      • the router/adapter receives no tools field at dispatch time
      • a warning was logged (caplog capture)
  - Flips "client-sent tools are stripped with warning" in gate_1
    → green (9/10 after 1.6; 1/10 remaining is e2e for 1.7).

Blockers: none.

Recent lessons to keep in mind (see JOURNAL.jsonl for full text):
  - claude -p exits 0 even on rate_limit; inspect events, not exit code
  - cold cache tax 6k–14k input tokens — warm conversations matter
  - agent-loop contamination observable not preventable under OAuth
  - gemini is a compound provider (stats.models per turn)
  - three session id shapes; adapter normalizes to opaque string
  - claude modelUsage field names spike-observed; confirm at 1.7 e2e
